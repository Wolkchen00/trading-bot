from __future__ import annotations

from types import SimpleNamespace

import pytest
from alpaca.trading.enums import OrderSide, TimeInForce

from core.position_manager import PositionManager
from core.protection import (
    ProtectionOutcome,
    deterministic_client_order_id,
)


ACTIVE = {"new", "accepted", "partially_filled", "held", "pending_new"}


def broker_position(
    symbol="PLTR", qty=15, entry=160.0, current=162.0
):
    return SimpleNamespace(
        symbol=symbol,
        qty=str(qty),
        avg_entry_price=str(entry),
        current_price=str(current),
        unrealized_pl="0",
        asset_class="us_equity",
    )


def broker_order(
    oid,
    *,
    symbol="PLTR",
    qty=15,
    side=OrderSide.SELL,
    status="new",
    stop=157.63,
    limit_price=None,
    client_order_id=None,
):
    stop = float(stop)
    if limit_price is None:
        limit_price = round(
            stop * (0.995 if side == OrderSide.SELL else 1.005), 2
        )
    return SimpleNamespace(
        id=str(oid),
        symbol=symbol,
        qty=str(qty),
        filled_qty="0",
        side=side,
        status=status,
        type="stop_limit",
        order_type="stop_limit",
        stop_price=str(stop),
        limit_price=str(limit_price),
        time_in_force=TimeInForce.GTC,
        replaced_by=None,
        client_order_id=client_order_id,
        legs=None,
    )


class DuplicateClientOrderId(RuntimeError):
    code = 40010001


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


class Bot:
    def __init__(self, client, *, side="LONG", canonical=159.5):
        self.client = client
        state = {
            "entry_price": abs(float(client.pos.avg_entry_price)),
            "qty": abs(float(client.pos.qty)),
            "stop_loss_price": canonical,
            "stop_loss_pct": 0.05,
            "server_stop_verified": True,
            "server_stop_order_id": "old-stop",
            "breakeven_set": False,
        }
        self.positions = {client.pos.symbol: state} if side == "LONG" else {}
        self.short_positions = (
            {client.pos.symbol: state} if side == "SHORT" else {}
        )
        self.index_parking = NeverParking()
        self.notifier = Notifier()
        self._exit_flag_cache = {}
        self._protection_poll_seconds = 0

    def _stash_exit_flags(self, symbol, data):
        self._exit_flag_cache[symbol] = dict(data)


class BaseStopClient:
    def __init__(self, *, side="LONG", stop=157.63, canonical=159.5):
        signed_qty = 15 if side == "LONG" else -15
        symbol = "PLTR" if side == "LONG" else "TSLA"
        current = 162 if side == "LONG" else 90
        self.pos = broker_position(
            symbol=symbol, qty=signed_qty, entry=160, current=current
        )
        order_side = OrderSide.SELL if side == "LONG" else OrderSide.BUY
        self.orders = {
            "old-stop": broker_order(
                "old-stop",
                symbol=symbol,
                side=order_side,
                stop=stop,
            )
        }
        self.canonical = canonical
        self.replace_requests = []
        self.stop_requests = []
        self._seq = 0

    def get_all_positions(self):
        return [self.pos]

    def get_orders(self, _request):
        return [
            order
            for order in self.orders.values()
            if str(order.status) in ACTIVE
        ]

    def get_order_by_id(self, oid):
        return self.orders[str(oid)]

    def get_order_by_client_id(self, cid):
        for order in self.orders.values():
            if str(getattr(order, "client_order_id", "") or "") == str(cid):
                return order
        raise KeyError(cid)

    def _new_stop(self, request, prefix):
        self._seq += 1
        created = broker_order(
            f"{prefix}-{self._seq}",
            symbol=self.pos.symbol,
            qty=getattr(request, "qty", None) or abs(float(self.pos.qty)),
            side=request.side if hasattr(request, "side") else self.orders[
                "old-stop"
            ].side,
            stop=request.stop_price,
            limit_price=getattr(request, "limit_price", None),
            client_order_id=request.client_order_id,
        )
        self.orders[created.id] = created
        return created

    def replace_order_by_id(self, oid, request):
        self.replace_requests.append(request)
        old = self.orders[str(oid)]
        old.status = "replaced"
        created = self._new_stop(request, "replacement")
        old.replaced_by = created.id
        return created

    def submit_order(self, request):
        self.stop_requests.append(request)
        return self._new_stop(request, "submitted")


def manager(client, *, side="LONG", canonical=159.5, attempts=3):
    bot = Bot(client, side=side, canonical=canonical)
    pm = PositionManager(bot)
    pm._verify_attempts = attempts
    return pm, bot


def test_new_invocation_uses_new_cid_and_full_salt_material():
    client = BaseStopClient(stop=157.63, canonical=159.5)
    pm, bot = manager(client)

    first = pm._update_server_stop_loss("PLTR", 159.5, 15)
    first_active = client.orders[first.order_id]
    first_active.stop_price = "157.63"
    first_active.limit_price = "156.84"
    bot.positions["PLTR"]["server_stop_order_id"] = first.order_id
    second = pm._update_server_stop_loss("PLTR", 159.5, 15)

    assert first.verified and second.verified
    assert len(client.replace_requests) == 2
    first_cid = client.replace_requests[0].client_order_id
    second_cid = client.replace_requests[1].client_order_id
    assert first_cid != second_cid
    assert len(first_cid) < 48 and len(second_cid) < 48

    same_prefix_a = "12345678" + "a" * 24
    same_prefix_b = "12345678" + "b" * 24
    assert deterministic_client_order_id(
        "PLTR", "LONG", 159.5, 15, same_prefix_a
    ) != deterministic_client_order_id(
        "PLTR", "LONG", 159.5, 15, same_prefix_b
    )


class AcceptedThenDuplicateClient(BaseStopClient):
    def replace_order_by_id(self, oid, request):
        self.replace_requests.append(request)
        old = self.orders[str(oid)]
        old.status = "replaced"
        accepted = self._new_stop(request, "accepted")
        old.replaced_by = accepted.id
        raise DuplicateClientOrderId("40010001 client_order_id must be unique")


def test_duplicate_rejection_is_verified_by_definitive_cid_reread():
    client = AcceptedThenDuplicateClient()
    pm, _ = manager(client)

    result = pm._update_server_stop_loss("PLTR", 159.5, 15)

    assert result.outcome is ProtectionOutcome.VERIFIED
    assert result.verified and result.at_target
    assert result.order_id.startswith("accepted-")
    assert len(client.replace_requests) == 1
    assert client.stop_requests == []


class PltrIncidentClient(BaseStopClient):
    """2026-08-07: replace eski stopu siler, cid tarihsel emre carpar."""

    def replace_order_by_id(self, oid, request):
        self.replace_requests.append(request)
        old = self.orders[str(oid)]
        old.status = "canceled"
        old.replaced_by = None
        historical = broker_order(
            "e7094de1",
            symbol="PLTR",
            qty=15,
            status="canceled",
            stop=159.5,
            client_order_id=request.client_order_id,
        )
        self.orders[historical.id] = historical
        raise DuplicateClientOrderId("40010001 client_order_id must be unique")


def test_pltr_2026_08_07_destroyed_replace_gets_fresh_cid_no_leg_repair():
    client = PltrIncidentClient(stop=157.63, canonical=159.5)
    pm, bot = manager(client, attempts=3)

    result = pm._update_server_stop_loss("PLTR", 159.5, 15)

    assert result.outcome is ProtectionOutcome.NO_LEG_RESUBMITTED
    assert result.verified and result.at_target
    assert len(client.replace_requests) == 1
    assert len(client.stop_requests) == 1
    assert (
        client.stop_requests[0].client_order_id
        != client.replace_requests[0].client_order_id
    )
    active = [order for order in client.orders.values() if order.status in ACTIVE]
    assert len(active) == 1
    assert float(active[0].stop_price) == pytest.approx(159.5)
    assert any("ciplak pencere=" in message for message in bot.notifier.messages)


class DelayedReplacementClient(BaseStopClient):
    def __init__(self):
        super().__init__(stop=157.63, canonical=159.5)
        self.old_reads = 0
        self.delayed_request = None

    def replace_order_by_id(self, oid, request):
        self.replace_requests.append(request)
        self.delayed_request = request
        old = self.orders[str(oid)]
        old.status = "canceled"
        old.replaced_by = None
        return SimpleNamespace(id=None)

    def get_order_by_id(self, oid):
        order = self.orders[str(oid)]
        if str(oid) == "old-stop":
            self.old_reads += 1
            if self.old_reads == 3:
                replacement = self._new_stop(
                    self.delayed_request, "delayed-replacement"
                )
                order.replaced_by = replacement.id
        return order


def test_delayed_replacement_seen_in_bounded_poll_never_submits_second_stop():
    client = DelayedReplacementClient()
    pm, _ = manager(client, attempts=4)

    result = pm._update_server_stop_loss("PLTR", 159.5, 15)

    assert result.outcome is ProtectionOutcome.REPLACED_VERIFIED
    assert result.verified and result.at_target
    assert result.order_id.startswith("delayed-replacement-")
    assert client.stop_requests == []
    active = [order for order in client.orders.values() if order.status in ACTIVE]
    assert len(active) == 1


@pytest.mark.parametrize(
    ("side", "active_stop", "expected_severity", "alarm_count"),
    [
        ("LONG", 99.5, "WARNING", 0),
        ("LONG", 98.9, "CRITICAL", 1),
        ("SHORT", 100.5, "WARNING", 0),
        ("SHORT", 101.1, "CRITICAL", 1),
    ],
)
def test_degraded_protection_directional_thresholds(
    side, active_stop, expected_severity, alarm_count
):
    client = BaseStopClient(
        side=side, stop=active_stop, canonical=100.0
    )
    client.pos.avg_entry_price = "100"
    pm, bot = manager(client, side=side, canonical=100.0)

    summary = pm.ensure_protective_stops(
        {"stop_loss_pct": 0.05, "protection_drift_critical_pct": 0.01}
    )

    result = summary.results[0]
    assert result.outcome is ProtectionOutcome.DEGRADED_PROTECTED
    assert result.verified and not result.at_target
    assert f"severity={expected_severity}" in result.detail
    assert len(bot.notifier.messages) == alarm_count
    book = bot.positions if side == "LONG" else bot.short_positions
    assert book[client.pos.symbol]["stop_loss_price"] == pytest.approx(100.0)


class Http500(RuntimeError):
    status_code = 500


class ReconciliationClient:
    def __init__(self, position_responses, order_responses=None):
        self.pos = SimpleNamespace(avg_entry_price="100", qty="0", symbol="NONE")
        self.position_responses = list(position_responses)
        self.order_responses = list(order_responses or [[]])

    @staticmethod
    def _next(responses):
        response = responses.pop(0) if len(responses) > 1 else responses[0]
        if isinstance(response, Exception):
            raise response
        return response

    def get_all_positions(self):
        return self._next(self.position_responses)

    def get_orders(self, _request):
        return self._next(self.order_responses)


def reconciliation_manager(position_responses, order_responses=None):
    client = ReconciliationClient(position_responses, order_responses)
    bot = Bot(client)
    bot.positions = {}
    bot.short_positions = {}
    pm = PositionManager(bot)
    return pm, bot, client


def test_single_transient_500_retries_then_succeeds_without_alarm():
    pm, bot, _ = reconciliation_manager([Http500("500 first"), []])

    summary = pm.ensure_protective_stops({"stop_loss_pct": 0.05})

    assert summary.ok
    assert bot.notifier.messages == []
    assert pm._reconciliation_retry_counts["positions"] == 0


def test_second_consecutive_500_alarms():
    pm, bot, _ = reconciliation_manager(
        [Http500("500 first"), Http500("500 second")]
    )

    summary = pm.ensure_protective_stops({"stop_loss_pct": 0.05})

    assert not summary.ok
    assert len(bot.notifier.messages) == 1
    assert pm._reconciliation_retry_counts["positions"] == 2


def test_success_between_transient_failures_resets_operation_counter():
    pm, bot, client = reconciliation_manager([Http500("500 first"), []])

    first = pm.ensure_protective_stops({"stop_loss_pct": 0.05})
    client.position_responses = [Http500("500 later"), []]
    second = pm.ensure_protective_stops({"stop_loss_pct": 0.05})

    assert first.ok and second.ok
    assert bot.notifier.messages == []
    assert pm._reconciliation_retry_counts["positions"] == 0
