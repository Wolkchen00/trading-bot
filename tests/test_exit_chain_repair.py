from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest
from alpaca.trading.enums import OrderSide, TimeInForce

import config as config_module
from core.position_manager import PositionManager
from core.protection import ProtectionOutcome


ACTIVE = {"new", "accepted", "partially_filled", "held", "pending_new"}


def broker_position(symbol="PLTR", qty=10, entry=100, current=103.01):
    return SimpleNamespace(
        symbol=symbol,
        qty=str(qty),
        avg_entry_price=str(entry),
        current_price=str(current),
        unrealized_pl=str((current - entry) * qty),
        asset_class="us_equity",
    )


def broker_order(
    oid, *, symbol="PLTR", qty=10, side=OrderSide.SELL,
    status="new", order_type="stop_limit", stop=100.1, limit=99.6,
    filled=0, client_order_id=None,
):
    return SimpleNamespace(
        id=str(oid),
        symbol=symbol,
        qty=str(qty),
        filled_qty=str(filled),
        filled_avg_price="103.01" if filled else None,
        side=side,
        status=status,
        type=order_type,
        order_type=order_type,
        stop_price=str(stop) if stop is not None else None,
        limit_price=str(limit) if limit is not None else None,
        time_in_force=TimeInForce.GTC,
        replaced_by=None,
        client_order_id=client_order_id,
        legs=None,
    )


class Notifier:
    def __init__(self):
        self.messages = []

    def notify_error(self, message):
        self.messages.append(message)
        return True


class NeverParking:
    @staticmethod
    def is_parking_symbol(_symbol):
        return False


class ExitClient:
    def __init__(
        self, *, qty=10, current=103.01, side="LONG", partial_mode="filled",
        fill_exit_during_cancel=False,
    ):
        signed_qty = qty if side == "LONG" else -qty
        self.pos = broker_position(
            symbol="PLTR" if side == "LONG" else "TSLA",
            qty=signed_qty,
            current=current,
        )
        stop_side = OrderSide.SELL if side == "LONG" else OrderSide.BUY
        stop_price = 100.1 if side == "LONG" else 100.3
        self.orders = {
            "stop-0": broker_order(
                "stop-0", symbol=self.pos.symbol, qty=qty,
                side=stop_side, stop=stop_price,
                limit=round(stop_price * (0.995 if side == "LONG" else 1.005), 2),
            )
        }
        self.partial_mode = partial_mode
        self.fill_exit_during_cancel = fill_exit_during_cancel
        self.market_submits = []
        self.stop_submits = []
        self.replace_calls = []
        self._seq = 0

    def get_all_positions(self):
        return [self.pos] if abs(float(self.pos.qty)) > 0 else []

    def get_orders(self, request=None):
        status = str(getattr(getattr(request, "status", None), "value", "open"))
        if status.lower() == "all":
            return list(self.orders.values())
        return [order for order in self.orders.values() if str(order.status) in ACTIVE]

    def get_order_by_id(self, oid):
        return self.orders[str(oid)]

    def get_order_by_client_id(self, cid):
        for order in self.orders.values():
            if str(getattr(order, "client_order_id", "") or "") == str(cid):
                return order
        raise KeyError(cid)

    def cancel_order_by_id(self, oid):
        order = self.orders[str(oid)]
        if self.fill_exit_during_cancel and order.type == "stop_limit":
            order.status = "filled"
            order.filled_qty = order.qty
            self.pos.qty = "0"
            self.fill_exit_during_cancel = False
            return
        if order.type == "market" and order.status == "partially_filled":
            order.status = "canceled"
            return
        order.status = "canceled"

    def replace_order_by_id(self, oid, request):
        old = self.orders[str(oid)]
        old.status = "replaced"
        self._seq += 1
        new = broker_order(
            f"stop-r{self._seq}", symbol=old.symbol,
            qty=getattr(request, "qty", None) or old.qty,
            side=old.side, stop=request.stop_price,
            limit=getattr(request, "limit_price", None),
            client_order_id=request.client_order_id,
        )
        old.replaced_by = new.id
        self.orders[new.id] = new
        self.replace_calls.append(request)
        return new

    def submit_order(self, request):
        self._seq += 1
        if getattr(request, "stop_price", None) is not None:
            order = broker_order(
                f"stop-s{self._seq}", symbol=request.symbol, qty=request.qty,
                side=request.side, stop=request.stop_price,
                limit=request.limit_price,
                client_order_id=request.client_order_id,
            )
            self.orders[order.id] = order
            self.stop_submits.append(request)
            return order

        self.market_submits.append(request)
        if self.partial_mode == "rejected":
            raise RuntimeError("market rejected")
        qty = float(request.qty)
        filled = qty if self.partial_mode == "filled" else (
            1.0 if self.partial_mode == "partial" else 0.0
        )
        status = "filled" if self.partial_mode == "filled" else (
            "partially_filled" if self.partial_mode == "partial" else "new"
        )
        order = broker_order(
            f"partial-{self._seq}", symbol=request.symbol, qty=qty,
            side=OrderSide.SELL, status=status, order_type="market",
            stop=None, limit=None, filled=filled,
            client_order_id=request.client_order_id,
        )
        self.orders[order.id] = order
        if filled:
            self.pos.qty = str(max(float(self.pos.qty) - filled, 0))
        return order

    def seed_partial_order(self, cid, qty=5, filled=5, status="filled"):
        order = broker_order(
            "partial-existing", symbol=self.pos.symbol, qty=qty,
            side=OrderSide.SELL, status=status, order_type="market",
            stop=None, limit=None, filled=filled, client_order_id=cid,
        )
        self.orders[order.id] = order
        return order


class Bot:
    def __init__(self, client, pos_data, state_sink=None):
        self.client = client
        self.positions = {client.pos.symbol: pos_data}
        self.short_positions = {}
        self.index_parking = NeverParking()
        self.sell_cooldown = {}
        self.consecutive_errors = 0
        self.executor = SimpleNamespace(execute_sell=lambda *_args, **_kwargs: None)
        self.notifier = Notifier()
        self._exit_flag_cache = {}
        self._protection_poll_seconds = 0
        self.state_sink = state_sink if state_sink is not None else []

    def _stash_exit_flags(self, symbol, data):
        self._exit_flag_cache[symbol] = deepcopy(data)

    def _save_position_metadata(self):
        self.state_sink.append(deepcopy(self.positions))
        return True


def long_state(qty=10, stop=100.1):
    return {
        "entry_price": 100.0,
        "qty": qty,
        "entry_time": "2026-08-09T09:30:00",
        "highest_price": 103.01,
        "breakeven_set": True,
        "partial_sold": False,
        "stop_loss_pct": 0.05,
        "stop_loss_price": stop,
        "server_stop_verified": True,
        "server_stop_order_id": "stop-0",
    }


def runtime_config(paper: bool):
    merged = dict(config_module.STOCK_CONFIG)
    if paper:
        for key, value in config_module.PAPER_AGGRESSIVE_CONFIG.items():
            if not key.startswith(("short_", "enable_", "prefer_")):
                merged[key] = value
    return merged


@pytest.fixture(autouse=True)
def telemetry_file(tmp_path, monkeypatch):
    path = tmp_path / "telemetry.jsonl"
    monkeypatch.setattr(config_module, "state_path", lambda _name: str(path))
    return path


def manager(client, pos_data=None):
    bot = Bot(client, pos_data or long_state(abs(float(client.pos.qty))))
    pm = PositionManager(bot)
    pm._verify_attempts = 2
    return pm, bot


def active_stops(client):
    return [
        order for order in client.orders.values()
        if order.type == "stop_limit" and str(order.status) in ACTIVE
    ]


def test_real_runtime_thresholds_are_paper_three_live_five_percent():
    assert runtime_config(True)["partial_profit_pct"] == pytest.approx(0.03)
    assert runtime_config(False)["partial_profit_pct"] == pytest.approx(0.05)


def test_paper_301_percent_reaches_fill_verified_partial_and_restores_one_stop():
    client = ExitClient(current=103.01, partial_mode="filled")
    pm, bot = manager(client)

    pm.manage_positions(runtime_config(True))

    assert len(client.market_submits) == 1
    assert bot.positions["PLTR"]["partial_sold"] is True
    assert bot.positions["PLTR"]["partial_intent"]["status"] == "FILLED"
    assert float(client.pos.qty) == pytest.approx(5)
    assert len(active_stops(client)) == 1
    assert float(active_stops(client)[0].qty) == pytest.approx(5)


def test_live_301_percent_does_not_partial_but_live_501_does():
    below = ExitClient(current=103.01)
    pm, _ = manager(below)
    pm.manage_positions(runtime_config(False))
    assert below.market_submits == []

    reached = ExitClient(current=105.01)
    state = long_state()
    state["highest_price"] = 105.01
    pm, _ = manager(reached, state)
    pm.manage_positions(runtime_config(False))
    assert len(reached.market_submits) == 1


@pytest.mark.parametrize(
    ("side", "requested", "active"),
    [("LONG", 98.0, 100.1), ("SHORT", 105.0, 100.3)],
)
def test_server_stop_boundary_never_regresses_either_side(side, requested, active):
    client = ExitClient(side=side, current=110 if side == "LONG" else 90)
    client.orders["stop-0"].stop_price = str(active)
    symbol = client.pos.symbol
    state = long_state(stop=active)
    state["breakeven_set"] = False
    bot = Bot(client, state)
    if side == "SHORT":
        bot.positions = {}
        bot.short_positions = {symbol: state}
    pm = PositionManager(bot)
    pm._verify_attempts = 2

    result = pm._update_server_stop_loss(
        symbol, requested, abs(float(client.pos.qty)), side=side
    )

    assert result.outcome is ProtectionOutcome.NOOP_BETTER_PROTECTED
    assert result.verified and result.at_target
    assert client.replace_calls == []
    assert float(result.stop_price) == pytest.approx(active)


def test_verified_trailing_update_writes_canonical_server_target():
    client = ExitClient(current=110)
    pm, bot = manager(client)

    result = pm._update_server_stop_loss("PLTR", 102.0, 10, side="LONG")

    assert result.verified
    assert bot.positions["PLTR"]["stop_loss_price"] == pytest.approx(102.0)
    assert float(active_stops(client)[0].stop_price) == pytest.approx(102.0)


@pytest.mark.parametrize("partial_mode", ["rejected", "timeout"])
def test_nofill_paths_leave_partial_false_and_restore_real_qty_stop(partial_mode):
    client = ExitClient(current=103.01, partial_mode=partial_mode)
    pm, bot = manager(client)

    pm.manage_positions(runtime_config(True))

    assert bot.positions["PLTR"]["partial_sold"] is False
    assert bot.positions["PLTR"]["partial_intent"]["status"] == "TERMINAL_NOFILL"
    assert float(client.pos.qty) == pytest.approx(10)
    assert len(active_stops(client)) == 1
    assert float(active_stops(client)[0].qty) == pytest.approx(10)
    assert len(client.market_submits) == 1


def test_single_share_partial_fill_is_not_counted_as_half_sale_and_retries_remainder():
    client = ExitClient(current=103.01, partial_mode="partial")
    pm, bot = manager(client)

    pm.manage_positions(runtime_config(True))

    intent = bot.positions["PLTR"]["partial_intent"]
    assert intent["status"] == "PARTIAL"
    assert intent["filled_qty"] == pytest.approx(1)
    assert bot.positions["PLTR"]["partial_sold"] is False
    assert float(active_stops(client)[0].qty) == pytest.approx(9)

    client.partial_mode = "filled"
    pm.manage_positions(runtime_config(True))

    assert len(client.market_submits) == 2
    assert float(client.market_submits[1].qty) == pytest.approx(4)
    assert bot.positions["PLTR"]["partial_sold"] is True
    assert bot.positions["PLTR"]["partial_intent"]["status"] == "FILLED"


def test_restart_reconciles_same_cid_and_never_submits_second_half_sale():
    cid = "r1p-PLTR-persisted"
    client = ExitClient(qty=5, current=103.01)
    client.orders["stop-0"].qty = "5"
    client.seed_partial_order(cid, qty=5, filled=5, status="filled")
    state = long_state(qty=5)
    state["partial_intent"] = {
        "status": "INTENT",
        "client_order_id": cid,
        "order_id": None,
        "target_qty": 5.0,
        "filled_qty": 0.0,
        "attempt_qty": 5.0,
        "attempt_base_filled_qty": 0.0,
        "attempt_terminal": False,
        "starting_qty": 10.0,
    }
    pm, bot = manager(client, state)

    pm.manage_positions(runtime_config(True))

    assert client.market_submits == []
    assert bot.positions["PLTR"]["partial_sold"] is True
    assert bot.positions["PLTR"]["partial_intent"]["status"] == "FILLED"
    assert len(active_stops(client)) == 1


def test_exit_leg_fill_during_cancel_rereads_position_and_never_sells_stale_qty():
    client = ExitClient(
        current=103.01, partial_mode="filled", fill_exit_during_cancel=True
    )
    pm, bot = manager(client)

    pm.manage_positions(runtime_config(True))

    assert client.market_submits == []
    assert float(client.pos.qty) == 0
    assert bot.positions["PLTR"]["partial_sold"] is False
    assert active_stops(client) == []


def test_terminal_nofill_budget_stops_fourth_cancel_submit_churn(monkeypatch):
    warnings = []
    monkeypatch.setattr(
        "core.position_manager.logger.warning", warnings.append
    )
    client = ExitClient(current=103.01, partial_mode="rejected")
    pm, bot = manager(client)
    config = runtime_config(True)

    for _ in range(4):
        pm.manage_positions(config)

    budget = bot.positions["PLTR"]["partial_retry_budget"]
    assert len(client.market_submits) == 3
    assert budget["terminal_nofill"] == 3
    assert budget["warned"] is True
    assert any("PARTIAL RETRY BUTCESI DOLDU" in item for item in warnings)


def test_intent_and_threshold_are_written_before_submit(telemetry_file):
    state_sink = []
    client = ExitClient(current=103.01, partial_mode="rejected")
    bot = Bot(client, long_state(), state_sink=state_sink)
    pm = PositionManager(bot)
    pm._verify_attempts = 2

    pm.manage_positions(runtime_config(True))

    first_intent_snapshot = next(
        snapshot for snapshot in state_sink
        if snapshot["PLTR"].get("partial_intent", {}).get("status") == "INTENT"
    )
    cid = first_intent_snapshot["PLTR"]["partial_intent"]["client_order_id"]
    assert client.market_submits[0].client_order_id == cid
    telemetry = telemetry_file.read_text(encoding="utf-8")
    assert '"kind": "PARTIAL_THRESHOLD"' in telemetry
    assert '"kind": "PARTIAL_INTENT"' in telemetry
