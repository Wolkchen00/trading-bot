from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from tests_v5.fakebroker import FakeBroker, partial, reject
from v5.broker import BrokerClock
from v5.clock import Session, Window
from v5.data import DecisionInputs
from v5.executor import (
    CAPPED, DONE, DRY_RUN, FAILED, NETTED, NOT_SUBMITTED, PARTIAL, UNRESOLVED,
    Executor, OrderPlan, SessionRecord, Snapshot, client_order_id,
)
from v5.packages import PACKAGES
from v5.strategy import Decision, READY


UTC = timezone.utc
DAY = date(2026, 11, 19)


class Time:
    def __init__(self, value): self.value = value
    def now(self): return self.value
    def sleep(self, seconds): self.value += timedelta(seconds=seconds)


def setup(tmp_path, cash=10000, positions=None, enabled=True, purpose="dec"):
    now = datetime(2026, 11, 19, 19, 50, tzinfo=UTC)
    clock = Time(now)
    broker = FakeBroker(
        cash=cash, positions=positions, prices={"TQQQ": 100, "SPY": 10},
        clock=BrokerClock(now, True, now + timedelta(days=1), now + timedelta(minutes=10)),
    )
    window = Window(
        now - timedelta(minutes=1), now + timedelta(minutes=1),
        now + timedelta(seconds=60), now + timedelta(seconds=5),
        now + timedelta(minutes=11), now + timedelta(minutes=15),
    )
    record = SessionRecord.new(tmp_path, DAY, "paper", purpose, not enabled)
    record.data["target"] = (
        {"core_new": 0.0, "ovl_new": 0.0, "total_new": 0.0}
        if purpose == "flr" else
        {"core_new": 0.2, "ovl_new": 0.3, "total_new": 0.5}
    )
    record.save()
    executor = Executor(
        broker, SimpleNamespace(orders_enabled=enabled, mode="paper"), tmp_path,
        PACKAGES["high"], clock.now, clock.sleep,
    )
    return broker, clock, window, record, executor


def restart(broker, clock, path, enabled=True):
    return Executor(
        broker, SimpleNamespace(orders_enabled=enabled, mode="paper"), path,
        PACKAGES["high"], clock.now, clock.sleep,
    )


def test_record_helpers_and_client_ids(tmp_path):
    assert client_order_id("paper", DAY, "dec", 1) == "v5-paper-20261119-dec-1"
    for args in (("x", DAY, "dec", 1), ("paper", DAY, "x", 1), ("paper", DAY, "dec", 4)):
        with pytest.raises(ValueError): client_order_id(*args)
    record = SessionRecord.new(tmp_path, DAY, "paper")
    executor = Executor(None, SimpleNamespace(orders_enabled=True, mode="paper"), tmp_path, PACKAGES["high"], lambda: datetime.now(UTC), lambda _: None)
    executor.mark_not_ready(record, "PRICE_STALE")
    assert not record.is_final
    executor.finalize(record, "HOLD_NOT_READY", "PRICE_STALE")
    assert record.is_final


def test_full_fill_commits_allocation_and_final_restart_is_noop(tmp_path):
    broker, clock, window, record, executor = setup(tmp_path)
    executor.run_chain(record, window)
    assert record.status == DONE and len(broker.orders) == 1
    assert executor.load_overlay_fraction() == 0.6
    assert record.data["intents"][0]["theta"] == 1.0
    calls = len(broker.calls)
    loaded = SessionRecord.load(tmp_path, DAY)
    restart(broker, clock, tmp_path).run_chain(loaded, window)
    assert len(broker.calls) == calls and len(broker.orders) == 1


def test_netted_capped_and_dry_run_send_no_order(tmp_path):
    broker, clock, window, record, executor = setup(tmp_path / "net", positions={"TQQQ": 10})
    record.data["target"] = {"core_new": 0.09095, "ovl_new": 0, "total_new": 0.09095}
    record.save(); executor.run_chain(record, window)
    assert record.status == NETTED and not broker.orders

    broker, clock, window, record, executor = setup(tmp_path / "cap", cash="0.99")
    executor.run_chain(record, window)
    assert record.status == NETTED and not broker.orders

    broker, clock, window, record, executor = setup(tmp_path / "dry", enabled=False)
    executor.run_chain(record, window)
    assert record.status == DRY_RUN and not broker.orders
    assert not (tmp_path / "dry" / "overlay.json").exists()


@pytest.mark.parametrize("fault", ["timeout_before_accept", "timeout_after_accept"])
def test_ambiguous_submit_never_creates_duplicate(fault, tmp_path):
    broker, clock, window, record, executor = setup(tmp_path)
    broker.script(fault)
    executor.run_chain(record, window)
    assert record.status == DONE and len(broker.orders) == 1
    calls = [args["client_order_id"] for name, args in broker.calls if name == "submit_order"]
    assert len(set(calls)) == 1


def test_partial_then_second_attempt_rebuilds_and_finishes(tmp_path):
    broker, clock, window, record, executor = setup(tmp_path)
    broker.script(partial("0.5"))
    executor.run_chain(record, window)
    assert record.status == DONE and len(record.data["intents"]) == 2
    assert record.data["intents"][0]["outcome"] == PARTIAL
    assert len({order.client_order_id for order in broker.orders}) == 2


def test_three_never_fills_close_failed(tmp_path):
    broker, clock, window, record, executor = setup(tmp_path)
    broker.script("never_fill", "never_fill", "never_fill")
    executor.run_chain(record, window)
    assert record.status == FAILED and len(record.data["intents"]) == 3


def test_one_resize_is_allowed_but_second_or_other_refusal_fails(tmp_path):
    broker, clock, window, record, executor = setup(tmp_path / "one")
    broker.script(reject(403, 40310000, "insufficient buying power"))
    executor.run_chain(record, window)
    assert record.status == DONE and len(record.data["intents"]) == 2

    broker, clock, window, record, executor = setup(tmp_path / "two")
    broker.script(reject(403, 40310000, "x"), reject(403, 40310000, "x"))
    executor.run_chain(record, window)
    assert record.status == FAILED and len(record.data["intents"]) == 2

    broker, clock, window, record, executor = setup(tmp_path / "other")
    broker.script(reject(422, 1, "x")); executor.run_chain(record, window)
    assert record.status == FAILED and len(record.data["intents"]) == 1


def test_foreign_hold_clears_and_floor_ignores_foreign_position(tmp_path):
    broker, clock, window, record, executor = setup(tmp_path / "dec", positions={"SPY": 1})
    executor.run_chain(record, window)
    assert record.data["hold"] == "FOREIGN_ASSET" and not broker.orders
    broker.positions.pop("SPY"); executor.run_chain(record, window)
    assert record.status == DONE and record.data["hold"] is None

    broker, clock, window, record, executor = setup(
        tmp_path / "flr", positions={"TQQQ": 1, "SPY": 1}, purpose="flr"
    )
    executor.run_chain(record, window)
    assert record.status == DONE and "TQQQ" not in broker.positions and "SPY" in broker.positions


def test_floor_flat_done_and_foreign_product_order_blocks(tmp_path):
    broker, clock, window, record, executor = setup(tmp_path / "flat", purpose="flr")
    executor.run_chain(record, window)
    assert record.status == DONE and not broker.orders

    broker, clock, window, record, executor = setup(tmp_path / "blocked", positions={"TQQQ": 1}, purpose="flr")
    broker.script("never_fill")
    broker.submit_order("TQQQ", "sell", "foreign", qty="0.5")
    executor.run_chain(record, window)
    assert record.data["hold"] == "FOREIGN_ASSET"


def test_unstarted_plan_after_deadline_is_not_submitted(tmp_path):
    broker, clock, window, record, executor = setup(tmp_path)
    snapshot = Snapshot(10000, 10000, "0", "0", None, 0, (), (), (), ())
    plan = OrderPlan("buy", "buy", None, "5000", 5000, None, False)
    record.data["status"] = "PLANNED"
    record.data["intents"] = [executor._intent(record, plan, snapshot, 1, 0)]
    record.save(); clock.value = window.submit_until
    executor.run_chain(record, window)
    assert record.status == NOT_SUBMITTED and not broker.orders


def test_unresolved_blocks_next_session_until_after_close_proof(tmp_path):
    broker, clock, window, first, executor = setup(tmp_path)
    short = Window(window.decide_from, window.decide_until, clock.value + timedelta(seconds=5), window.cancel_at, window.reconcile_from, window.reconcile_until)
    broker.script("timeout_before_accept"); executor.run_chain(first, short)
    assert first.status == UNRESOLVED
    later_day = DAY + timedelta(days=1)
    later = SessionRecord.new(tmp_path, later_day, "paper")
    later.data["target"] = {"core_new": 0.4, "ovl_new": 0, "total_new": 0.4}; later.save()
    later_window = Window(clock.value, clock.value, clock.value + timedelta(minutes=1), clock.value + timedelta(seconds=30), clock.value, clock.value)
    executor.run_chain(later, later_window)
    assert later.data["hold"] == "UNRESOLVED" and not broker.orders
    session = Session(DAY, datetime(2026, 11, 19, 14, 30, tzinfo=UTC), datetime(2026, 11, 19, 21, tzinfo=UTC))
    executor.reconcile(first, session)
    assert first.status == FAILED
    executor.run_chain(later, later_window)
    assert later.status == DONE


def test_restart_of_started_intent_looks_up_same_id(tmp_path):
    broker, clock, window, record, executor = setup(tmp_path)
    snapshot = Snapshot(10000, 10000, "0", "0", None, 0, (), (), (), ())
    plan = OrderPlan("buy", "buy", None, "5000", 5000, None, False)
    intent = executor._intent(record, plan, snapshot, 1, 0)
    intent["submit_started"] = True
    record.data["status"], record.data["intents"] = "PLANNED", [intent]
    record.save()
    loaded = SessionRecord.load(tmp_path, DAY)
    restart(broker, clock, tmp_path).run_chain(loaded, window)
    assert loaded.status == DONE and len(broker.orders) == 1
