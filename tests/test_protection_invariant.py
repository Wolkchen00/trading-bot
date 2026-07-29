from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from alpaca.trading.enums import OrderSide, TimeInForce

from core.executor import OrderExecutor
from core.notifier import TelegramNotifier
from core.position_manager import PositionManager
from core.protection import (
    ProtectionOutcome,
    ProtectionResult,
    classify_covering_order,
)
from stock_bot import StockBot


def position(
    symbol="AAPL", qty=10, entry=100.0, current=110.0, asset_class="us_equity"
):
    return SimpleNamespace(
        symbol=symbol,
        qty=str(qty),
        avg_entry_price=str(entry),
        current_price=str(current),
        unrealized_pl="0",
        asset_class=asset_class,
    )


def order(
    oid,
    symbol="AAPL",
    qty=10,
    side=OrderSide.SELL,
    status="new",
    order_type="stop_limit",
    stop=95.0,
    limit=94.5,
    tif=TimeInForce.GTC,
    filled=0,
    replaced_by=None,
):
    return SimpleNamespace(
        id=oid,
        symbol=symbol,
        qty=str(qty),
        filled_qty=str(filled),
        side=side,
        status=status,
        type=order_type,
        order_type=order_type,
        stop_price=str(stop) if stop is not None else None,
        limit_price=str(limit) if limit is not None else None,
        time_in_force=tif,
        replaced_by=replaced_by,
        legs=None,
    )


class Notifier:
    def __init__(self):
        self.messages = []

    def notify_error(self, message):
        self.messages.append(message)
        return True


class Parking:
    def __init__(self, symbols=()):
        self.symbols = set(symbols)

    def is_parking_symbol(self, symbol):
        return symbol in self.symbols


class Bot:
    def __init__(self, client, long=None, short=None, parking=()):
        self.client = client
        self.positions = long or {}
        self.short_positions = short or {}
        self.index_parking = Parking(parking)
        self.notifier = Notifier()
        self._exit_flag_cache = {}
        self._protection_poll_seconds = 0

    def _stash_exit_flags(self, symbol, data):
        self._exit_flag_cache[symbol] = dict(data)


class ReplaceClient:
    def __init__(self, pos, old, reject_count=0):
        self.pos = pos
        self.orders = {old.id: old}
        self.reject_count = reject_count
        self.replace_calls = []

    def get_all_positions(self):
        return [self.pos]

    def get_orders(self, _request):
        active = {
            "new", "accepted", "pending_new", "pending_replace",
            "partially_filled", "held",
        }
        return [item for item in self.orders.values() if str(item.status) in active]

    def get_order_by_id(self, oid):
        return self.orders[str(oid)]

    def replace_order_by_id(self, oid, request):
        self.replace_calls.append((str(oid), request))
        if self.reject_count:
            self.reject_count -= 1
            raise RuntimeError("replace rejected")
        old = self.orders[str(oid)]
        new_id = f"r{len(self.replace_calls)}"
        old.status = "replaced"
        old.replaced_by = new_id
        qty = old.qty if request.qty is None else request.qty
        new = order(
            new_id,
            symbol=old.symbol,
            qty=qty,
            side=old.side,
            stop=request.stop_price,
            limit=request.limit_price,
            tif=request.time_in_force,
        )
        self.orders[new_id] = new
        return new


def manager_for(client, qty=10, side="LONG"):
    symbol = client.pos.symbol
    data = {
        "entry_price": float(client.pos.avg_entry_price),
        "qty": abs(float(client.pos.qty)),
        "server_stop_verified": False,
        "server_stop_order_id": next(iter(client.orders), None),
    }
    bot = Bot(
        client,
        long={symbol: data} if side == "LONG" else {},
        short={symbol: data} if side == "SHORT" else {},
    )
    manager = PositionManager(bot)
    manager._verify_attempts = 4
    bot.position_manager = manager
    return manager, bot


def test_replace_accepted_then_verified_whole_share_gtc():
    client = ReplaceClient(position(qty=10), order("old", stop=94, limit=93.5))
    manager, bot = manager_for(client)

    result = manager._update_server_stop_loss("AAPL", 98.0, 10, side="LONG")

    assert result.outcome == ProtectionOutcome.REPLACED_VERIFIED
    assert result.order_id == "r1"
    assert result.stop_price == 98.0
    assert result.qty_covered == 10
    request = client.replace_calls[0][1]
    assert request.qty == 10
    assert request.time_in_force == TimeInForce.GTC
    assert float(request.stop_price) == 98.0
    assert float(request.limit_price) == 97.51
    assert bot.positions["AAPL"]["server_stop_verified"] is True
    assert bot.positions["AAPL"]["server_stop_order_id"] == "r1"


def test_replace_rejected_then_state_reread_and_retry():
    client = ReplaceClient(
        position(qty=10), order("old", stop=94, limit=93.5), reject_count=1
    )
    manager, _ = manager_for(client)

    result = manager._update_server_stop_loss("AAPL", 98.0, 10, side="LONG")

    assert result.outcome == ProtectionOutcome.REPLACED_VERIFIED
    assert len(client.replace_calls) == 2
    assert client.replace_calls[0][1].client_order_id == (
        client.replace_calls[1][1].client_order_id
    )


def test_fractional_replace_omits_qty_and_preserves_day():
    client = ReplaceClient(
        position(qty=1.5),
        order("old", qty=1.5, stop=94, limit=93.5, tif=TimeInForce.DAY),
    )
    manager, _ = manager_for(client, qty=1.5)

    result = manager._update_server_stop_loss("AAPL", 98.0, 1.5, side="LONG")

    assert result.outcome == ProtectionOutcome.REPLACED_VERIFIED
    request = client.replace_calls[0][1]
    assert request.qty is None
    assert request.time_in_force == TimeInForce.DAY
    assert "qty" not in request.model_dump(exclude_none=True)


class NoLegClient:
    def __init__(self):
        self.pos = position(qty=4)
        self.orders = {
            "tp": order(
                "tp", qty=4, order_type="limit", stop=None, limit=120,
                side=OrderSide.SELL,
            )
        }
        self.events = []

    def get_all_positions(self):
        return [self.pos]

    def get_orders(self, _request):
        return [
            item for item in self.orders.values()
            if item.status in {"new", "accepted", "pending_new"}
        ]

    def get_order_by_id(self, oid):
        return self.orders[str(oid)]

    def cancel_order_by_id(self, oid):
        self.events.append(("cancel", str(oid)))
        self.orders[str(oid)].status = "canceled"

    def submit_order(self, request):
        self.events.append(("submit", request.client_order_id))
        submitted = order(
            "new-stop", qty=request.qty, stop=request.stop_price,
            limit=request.limit_price, tif=request.time_in_force,
        )
        self.orders[submitted.id] = submitted
        return submitted


def test_no_leg_cancels_waits_then_resubmits_and_verifies():
    client = NoLegClient()
    manager, bot = manager_for(client, qty=4)
    # manager_for saw the TP id as cached server ID; it must not mistake it for a stop.
    bot.positions["AAPL"]["server_stop_order_id"] = None

    result = manager._update_server_stop_loss("AAPL", 96.0, 4, side="LONG")

    assert result.outcome == ProtectionOutcome.NO_LEG_RESUBMITTED
    assert client.events[0] == ("cancel", "tp")
    assert client.events[1][0] == "submit"
    assert result.order_id == "new-stop"


def test_elected_but_unfilled_long_stop_limit_is_not_coverage():
    pos = position(qty=5, entry=100, current=90)
    hung = order("hung", qty=5, stop=95, limit=94.5)

    result = classify_covering_order(hung, pos, "LONG")

    assert result.outcome == ProtectionOutcome.ELECTED_UNFILLED
    assert result.verified is False


def test_pending_new_response_is_not_proof_of_coverage():
    pos = position(qty=5)
    pending = order("pending", qty=5, status="pending_new")

    result = classify_covering_order(pending, pos, "LONG")

    assert result.outcome == ProtectionOutcome.FAILED_NAKED
    assert result.verified is False


def test_short_side_buy_stop_replacement_is_verified():
    pos = position(symbol="TSLA", qty=-3, entry=100, current=90)
    old = order(
        "old-short", symbol="TSLA", qty=3, side=OrderSide.BUY,
        stop=108, limit=108.54,
    )
    client = ReplaceClient(pos, old)
    manager, bot = manager_for(client, qty=3, side="SHORT")

    result = manager._update_server_stop_loss("TSLA", 102.0, 3, side="SHORT")

    assert result.outcome == ProtectionOutcome.REPLACED_VERIFIED
    request = client.replace_calls[0][1]
    assert request.qty == 3
    assert request.time_in_force == TimeInForce.GTC
    assert float(request.limit_price) > float(request.stop_price)
    assert bot.short_positions["TSLA"]["server_stop_verified"] is True


def test_parking_symbol_is_reported_not_silently_skipped():
    class Client:
        def get_all_positions(self):
            return [position(symbol="SPY", qty=2)]

        def get_orders(self, _request):
            return []

    bot = Bot(Client(), parking={"SPY"})
    manager = PositionManager(bot)

    summary = manager.ensure_protective_stops({"stop_loss_pct": 0.05})

    assert len(summary.results) == 1
    assert summary.results[0].outcome == ProtectionOutcome.SKIPPED_PARKING
    # Park kolu tasarim geregi stop'suz ve HER turda mevcut. Bu yuzden:
    #  - "failed" sayilmaz (yoksa summary.ok kalici False'a cakilir),
    #  - kritik alarm uretmez (yoksa 5 dk'lik mutabakat kanali bogar),
    #  - ama sessizce de gecilmez: sonucta SKIPPED_PARKING olarak gorunur ve
    #    durum degistiginde bir INFO satiri yazilir.
    assert summary.failed == 0
    assert summary.skipped_parking == 1
    assert not bot.notifier.messages, "park kolu kritik alarm uretmemeli"
    assert bot._expected_uncovered_seen.get("SPY:PARKING")


def test_parking_with_real_coverage_is_reported_verified_without_alarm():
    covered = order("spy-stop", symbol="SPY", qty=2, stop=95, limit=94.5)

    class Client:
        def get_all_positions(self):
            return [position(symbol="SPY", qty=2, entry=100, current=105)]

        def get_orders(self, _request):
            return [covered]

    bot = Bot(Client(), parking={"SPY"})
    manager = PositionManager(bot)

    summary = manager.ensure_protective_stops({"stop_loss_pct": 0.05})

    assert summary.results[0].outcome == ProtectionOutcome.VERIFIED
    assert summary.failed == 0
    assert bot.notifier.messages == []


def test_restart_persists_protection_and_close_marker(tmp_path: Path):
    state_file = tmp_path / "positions.json"
    original = StockBot.__new__(StockBot)
    original.POSITIONS_FILE = str(state_file)
    original.positions = {
        "AAPL": {
            "entry_price": 100,
            "server_stop_verified": True,
            "server_stop_order_id": "stop-42",
            "close_in_progress": True,
        }
    }
    original.short_positions = {}
    original.options_positions = {}
    original.last_trade_time = {}
    original._consecutive_losses = 0
    original._symbol_consecutive_losses = {}
    original._daily_buys_count = 0
    original.trades_today = []

    assert original._save_position_metadata() is True

    restarted = StockBot.__new__(StockBot)
    restarted.POSITIONS_FILE = str(state_file)
    restarted.positions = {"AAPL": {"entry_price": 100}}
    restarted.short_positions = {}
    restarted.options_positions = {}
    restarted._consecutive_losses = 0
    restarted._symbol_consecutive_losses = {}
    restarted._load_position_metadata()

    restored = restarted.positions["AAPL"]
    assert restored["server_stop_verified"] is True
    assert restored["server_stop_order_id"] == "stop-42"
    assert restored["close_in_progress"] is True
    restarted._exit_flag_cache = {}
    restarted._stash_exit_flags("AAPL", restored)
    assert restarted._exit_flag_cache["AAPL"]["server_stop_order_id"] == "stop-42"


def test_close_position_acceptance_but_open_restores_protection_and_keeps_marker():
    live_pos = position(qty=5, entry=100, current=99)

    class Client:
        def __init__(self):
            self.events = []

        def get_open_position(self, _symbol):
            return live_pos

        def get_orders(self, _request):
            self.events.append("orders")
            return []

        def close_position(self, _symbol):
            self.events.append("close")
            return order("close-order", order_type="market", stop=None, limit=None)

        def get_all_positions(self):
            return [live_pos]

    client = Client()
    bot = Bot(
        client,
        long={
            "AAPL": {
                "entry_price": 100,
                "qty": 5,
                "entry_time": "",
                "stop_loss_price": 95,
                "stop_loss_pct": 0.05,
                "server_stop_verified": True,
                "server_stop_order_id": "old-stop",
                "close_in_progress": False,
            }
        },
    )
    bot.sell_cooldown = {}
    bot.consecutive_errors = 0
    bot.trades_today = []
    bot.last_trade_time = {}
    bot.position_manager = SimpleNamespace(
        _verify_attempts=2,
        _update_server_stop_loss=lambda *args, **kwargs: ProtectionResult(
            ProtectionOutcome.NO_LEG_RESUBMITTED,
            "restored-stop", 95.0, 5.0, "restored",
        ),
    )
    save_snapshots = []

    def save():
        save_snapshots.append(dict(bot.positions["AAPL"]))
        return True

    bot._save_position_metadata = save
    executor = OrderExecutor(bot)

    assert executor.execute_sell("AAPL", "TEST_CLOSE") is False
    assert save_snapshots[0]["close_in_progress"] is True
    assert "AAPL" in bot.positions
    assert bot.positions["AAPL"]["close_in_progress"] is True
    assert bot.positions["AAPL"]["server_stop_verified"] is True
    assert bot.positions["AAPL"]["server_stop_order_id"] == "restored-stop"
    assert bot.notifier.messages


def test_live_bracket_rejection_does_not_submit_market_fallback():
    class Client:
        def __init__(self):
            self.submit_count = 0

        def get_account(self):
            return SimpleNamespace(cash="1000", equity="1000")

        def submit_order(self, _request):
            self.submit_count += 1
            raise RuntimeError("bracket rejected")

    client = Client()
    bot = SimpleNamespace(
        client=client,
        is_paper=False,
        equity_floor=0,
        max_pos_usd=100,
        consecutive_errors=0,
        positions={},
    )
    analysis = {"price": 10, "atr": 0, "confidence": 80, "reasons": []}
    config = {
        "cash_reserve_pct": 0,
        "max_position_pct": 1,
        "min_trade_value": 10,
        "default_tier_weight": 1,
        "tier_weights": {},
        "stop_loss_pct": 0.05,
        "stop_loss_max_pct": 0.08,
        "atr_stop_multiplier": 2,
        "take_profit_pct": 0.10,
        "min_risk_reward": 2,
    }

    assert OrderExecutor(bot).execute_buy("AAPL", analysis, config) is False
    assert client.submit_count == 1
    assert bot.positions == {}


def test_critical_alarm_is_durable_when_delivery_is_disabled(
    tmp_path: Path, monkeypatch
):
    import config

    alarm_file = tmp_path / "alarms.jsonl"
    monkeypatch.setattr(config, "state_path", lambda _name: str(alarm_file))
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    notifier = TelegramNotifier()

    assert notifier.notify_critical("KORUMA", "AAPL naked") is False
    saved = alarm_file.read_text(encoding="utf-8")
    assert '"kind": "KORUMA"' in saved
    assert "AAPL naked" in saved
