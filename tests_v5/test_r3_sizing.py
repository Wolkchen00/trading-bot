from dataclasses import replace

import pytest

from tests_v5.fakebroker import FakeBroker
from v5.broker import validate_order_request
from v5.executor import Snapshot, SnapshotInvalid, allocate, plan_order, take_snapshot
from v5.packages import PACKAGES


def snap(equity=1000, cash=1000, qty="0", price=None, weight=0):
    return Snapshot(equity, cash, qty, qty, price, weight, (), (), (), ())


def test_all_sizing_kinds_and_valid_wire_sizes():
    params = PACKAGES["high"]
    buy = plan_order(snap(), 0.123456, params)
    assert (buy.kind, buy.notional) == ("buy", "123.45")
    cap = plan_order(snap(equity=10000, cash=100), 0.5, params)
    assert cap.kind == "buy" and cap.notional == "99.5" and cap.capped
    below = plan_order(snap(equity=10000, cash=0.99), 0.5, params)
    assert below.kind == "none" and below.capped
    net = plan_order(snap(qty="5", weight=0.5), 0.4995, params)
    assert net.kind == "none" and not net.capped
    sell = plan_order(snap(qty="10.123456789", cash=0, weight=0.8), 0.123456789, params)
    assert sell.kind == "sell" and float(sell.qty) <= 10.123456789
    dust = plan_order(snap(equity=10, cash=9.4, qty="0.006", weight=0.06), 0.0, params)
    assert (dust.kind, dust.qty) == ("sell_all", "0.006")
    for plan in (buy, cap, sell, dust):
        validate_order_request("TQQQ", plan.side, "v5-paper-20261119-dec-1", plan.notional, plan.qty)


def test_allocate_opposing_progress_and_dust():
    core, overlay, fraction = allocate(0.5, 0, 0, 0.50005, 1, 10000)
    assert core == 0 and overlay == 0.50005 and fraction == 1
    core, overlay, fraction = allocate(0.5, 0.2, 0.1, 0.4, 0.5, 10000)
    assert overlay == 0.4 and abs(core - 0.2) < 1e-15
    core, overlay, fraction = allocate(0.5, 0.00001, 0.5, 0, 0.5, 1000)
    assert overlay == 0 and core > 0.5 and fraction == 0


def test_snapshot_foreign_and_ownership_rules():
    broker = FakeBroker(
        cash=1000, positions={"TQQQ": 1, "QQQ": "0.001", "SPY": 1},
        prices={"TQQQ": 100, "QQQ": 500, "SPY": 2},
    )
    broker.script("never_fill")
    own = broker.submit_order("TQQQ", "buy", "persisted", notional="10")
    broker.script("never_fill")
    broker.submit_order("TQQQ", "buy", "v5-stray", notional="10")
    result = take_snapshot(broker, PACKAGES["high"], {"persisted"})
    assert own in result.own_open_orders
    assert any("SPY" in item for item in result.foreign_positions)
    assert not any("QQQ" in item for item in result.foreign_positions)
    assert any("v5-stray" in item for item in result.foreign_orders)


@pytest.mark.parametrize("case", ["status", "blocked", "equity", "weight"])
def test_snapshot_invalid(case):
    class Bad(FakeBroker):
        def get_account(self):
            value = super().get_account()
            if case == "status": return replace(value, status="INACTIVE")
            if case == "blocked": return replace(value, account_blocked=True)
            if case == "equity": return replace(value, equity=float("nan"))
            return value
    broker = Bad(
        cash=-1000 if case == "weight" else 1000,
        positions={"TQQQ": 20} if case == "weight" else None,
        prices={"TQQQ": 100},
    )
    with pytest.raises(SnapshotInvalid):
        take_snapshot(broker, PACKAGES["high"], set())
