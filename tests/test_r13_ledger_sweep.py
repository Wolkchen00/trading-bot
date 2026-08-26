from __future__ import annotations

from datetime import datetime, timezone
import logging
from pathlib import Path
from types import SimpleNamespace

import config as config_module
import core.fill_ledger as fill_ledger
import core.index_parking as parking_module
import core.ledger_sweep as sweep_module
from core.index_parking import IndexParkingManager
from core.ledger_sweep import LedgerSweep
from tools import olcum_raporu as report


NOW = datetime(2026, 8, 26, 16, 0, tzinfo=timezone.utc)


def _activity(
    *, activity_id="activity-1", order_id="order-1", symbol="AAPL",
    side="buy", qty="2", price="101.25",
    transaction_time="2026-08-26T15:00:00Z",
):
    return {
        "id": activity_id,
        "order_id": order_id,
        "symbol": symbol,
        "side": side,
        "qty": qty,
        "price": price,
        "transaction_time": transaction_time,
    }


class ActivityClient:
    def __init__(self, activities):
        self.activities = activities
        self.calls = 0

    def get_account_activities(self, *args, **kwargs):
        self.calls += 1
        return list(self.activities)


def _sweep(client):
    return LedgerSweep(
        client,
        {
            "ledger_sweep_window_hours": 24,
            "ledger_sweep_interval_minutes": 15,
        },
    )


def _broker_order(activity):
    return SimpleNamespace(
        id=activity["order_id"],
        symbol=activity["symbol"],
        side=activity["side"],
        filled_qty=activity["qty"],
        filled_avg_price=activity["price"],
        filled_at=datetime.fromisoformat(
            activity["transaction_time"].replace("Z", "+00:00")
        ),
        updated_at=None,
        status="filled",
        legs=[],
    )


def _isolate(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        config_module, "state_path", lambda name: str(tmp_path / name)
    )


def test_missing_broker_fill_is_added_unknown_without_guessed_pnl(
    tmp_path, monkeypatch,
):
    _isolate(tmp_path, monkeypatch)
    activity = _activity()
    summary = _sweep(ActivityClient([activity])).run(now=NOW)

    assert summary["added"] == 1
    rows = fill_ledger.read_fills()
    assert len(rows) == 1
    assert rows[0]["provenance"] == "UNKNOWN"
    assert rows[0]["pnl_usd"] is None
    assert rows[0]["source"] == "ledger_sweep"


def test_executor_row_without_execution_id_prevents_activity_double_count(
    tmp_path, monkeypatch,
):
    _isolate(tmp_path, monkeypatch)
    activity = _activity(activity_id="broker-activity")
    fill_ledger.record_fill(
        symbol="AAPL", side="BUY", qty=2, price=101.25,
        provenance="strategy", execution_id=None, order_id="order-1",
        ts_utc="2026-08-26T15:00:00Z",
    )

    summary = _sweep(ActivityClient([activity])).run(now=NOW)

    assert summary["added"] == 0
    assert len(fill_ledger.read_fills()) == 1


def test_degraded_content_dedupe_is_scoped_to_order_id(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    fill_ledger.record_fill(
        symbol="AAPL", side="BUY", qty=2, price=101.25,
        provenance="strategy", execution_id=None, order_id="other-order",
        ts_utc="2026-08-26T14:00:00Z",
    )

    summary = _sweep(ActivityClient([_activity(order_id="target-order")])).run(
        now=NOW
    )

    assert summary["added"] == 1
    assert {row["order_id"] for row in fill_ledger.read_fills()} == {
        "other-order", "target-order",
    }


def test_second_sweep_is_idempotent(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    client = ActivityClient([_activity()])
    sweep = _sweep(client)

    assert sweep.run(now=NOW)["added"] == 1
    assert sweep.run(now=NOW)["added"] == 0
    assert len(fill_ledger.read_fills()) == 1


def test_real_spy_sell_case_repairs_metric4_missing_count(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    activity = _activity(
        activity_id="spy-fill-20260825",
        order_id="9973db0e",
        symbol="SPY",
        side="sell",
        qty="4.916948",
        price="766.47",
        transaction_time="2026-08-25T20:33:47Z",
    )
    now = datetime(2026, 8, 26, 20, 0, tzinfo=timezone.utc)
    assert _sweep(ActivityClient([activity])).run(now=now)["added"] == 1

    reconciliation = report.reconcile_fill_ledger(
        [_broker_order(activity)],
        fill_ledger.read_fills(),
        datetime(2026, 8, 25, tzinfo=timezone.utc),
        now,
    )
    assert reconciliation.status is report.Status.PASS
    assert sum(reconciliation.broker_missing_from_ledger.values()) == 0


def test_unreadable_broker_warns_and_leaves_ledger_unchanged(
    tmp_path, monkeypatch,
):
    _isolate(tmp_path, monkeypatch)
    fill_ledger.record_fill(
        symbol="MSFT", side="BUY", qty=1, price=400,
        provenance="strategy", order_id="existing",
    )
    before = (tmp_path / "fill_ledger.jsonl").read_bytes()
    warnings = []
    monkeypatch.setattr("core.ledger_sweep.logger.warning", warnings.append)

    class BrokenClient:
        def get_account_activities(self, *args, **kwargs):
            raise RuntimeError("activities down")

        def get_orders(self, _request):
            raise RuntimeError("orders down")

    summary = _sweep(BrokenClient()).run(now=NOW)

    assert summary["added"] == 0
    assert summary["error"]
    assert (tmp_path / "fill_ledger.jsonl").read_bytes() == before
    assert any("LEDGER SWEEP HATASI" in message for message in warnings)


def test_fallback_unfilled_closed_orders_do_not_warn_or_change_ledger(
    tmp_path, monkeypatch, caplog,
):
    _isolate(tmp_path, monkeypatch)

    class FallbackClient:
        def get_account_activities(self, *args, **kwargs):
            raise RuntimeError("activities endpoint yok")

        def get_orders(self, _request):
            empty_leg = SimpleNamespace(
                id="empty-bracket-leg", symbol="AAPL", side="sell",
                filled_qty="0", filled_avg_price=None, filled_at=None,
                updated_at=None, status="canceled", legs=[],
            )
            return [SimpleNamespace(
                id="rejected-parent", symbol="AAPL", side="buy",
                filled_qty="0", filled_avg_price=None, filled_at=None,
                updated_at=None, status="rejected", legs=[empty_leg],
            )]

    sweep_module.logger.addHandler(caplog.handler)
    try:
        with caplog.at_level(logging.DEBUG, logger=sweep_module.logger.name):
            summary = _sweep(FallbackClient()).run(now=NOW)
    finally:
        sweep_module.logger.removeHandler(caplog.handler)

    assert summary["added"] == 0
    assert fill_ledger.read_fills() == []
    assert not [
        record for record in caplog.records
        if record.levelno >= logging.WARNING and "LEDGER SWEEP" in record.message
    ]


def test_filled_fallback_order_missing_symbol_warns_with_field_name(
    tmp_path, monkeypatch, caplog,
):
    _isolate(tmp_path, monkeypatch)

    class FallbackClient:
        def get_account_activities(self, *args, **kwargs):
            raise RuntimeError("activities endpoint yok")

        def get_orders(self, _request):
            return [SimpleNamespace(
                id="filled-without-symbol", symbol="", side="buy",
                filled_qty="1", filled_avg_price="100",
                filled_at=NOW, updated_at=NOW, status="filled", legs=[],
            )]

    sweep_module.logger.addHandler(caplog.handler)
    try:
        with caplog.at_level(logging.DEBUG, logger=sweep_module.logger.name):
            summary = _sweep(FallbackClient()).run(now=NOW)
    finally:
        sweep_module.logger.removeHandler(caplog.handler)

    warnings = [
        record.message for record in caplog.records
        if record.levelno >= logging.WARNING and "LEDGER SWEEP" in record.message
    ]
    assert summary["added"] == 0
    assert fill_ledger.read_fills() == []
    assert warnings
    assert any("symbol" in message for message in warnings)


def test_unfilled_parking_order_warns_with_order_id_and_writes_nothing(
    tmp_path, monkeypatch,
):
    _isolate(tmp_path, monkeypatch)
    order = SimpleNamespace(
        id="parking-race-order",
        filled_qty="0",
        filled_avg_price=None,
        client_order_id="park-client",
    )

    class Client:
        def get_order_by_id(self, order_id):
            assert order_id == "parking-race-order"
            return order

    manager = object.__new__(IndexParkingManager)
    manager.bot = SimpleNamespace(client=Client())
    manager.symbol = "SPY"
    warnings = []
    monkeypatch.setattr(parking_module.logger, "warning", warnings.append)

    manager._record_parking_fill(order, "SELL")

    assert fill_ledger.read_fills() == []
    assert any(
        "parking-race-order" in message and "ledger sweep" in message
        for message in warnings
    )


def test_report_imports_single_canonical_order_key():
    assert report.order_fill_key is fill_ledger.order_fill_key
    source = Path(report.__file__).read_text(encoding="utf-8")
    assert "from core.fill_ledger import order_fill_key" in source
    assert "result[order_fill_key(" in source
