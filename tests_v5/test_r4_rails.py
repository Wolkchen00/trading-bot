from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from tests_v5.fakebroker import FakeBroker
from v5.broker import Account, BrokerUnavailable
from v5.rails import (
    EquityReading,
    PeakNotInitialised,
    Rails,
    floor_order_allowed,
    read_equity,
    reduce_only,
)
from v5.state import LockBusy, ProcessLock, StateCorrupt
from v5.strategy import Decision, NOT_READY, READY


NOW = datetime(2026, 11, 16, 20, 50, tzinfo=timezone.utc)


def settings():
    return SimpleNamespace(rails=SimpleNamespace(alert_dd=0.37, breaker_day=0.15, floor_dd=0.60))


def rails(tmp_path, now=NOW, sleep=lambda _: None):
    return Rails(settings(), tmp_path, lambda: now, sleep)


def reading(equity, when=NOW, last=100.0, ok=True):
    return EquityReading(when, equity, last, equity, 0.0, ok, None if ok else "INCONSISTENT")


class AccountBroker(FakeBroker):
    def __init__(self, account, positions=()):
        self.account = account
        self.result_positions = list(positions)
        self.calls = []

    def get_account(self):
        self.calls.append("account")
        return self.account

    def get_positions(self):
        self.calls.append("positions")
        return self.result_positions


def test_read_equity_order_and_broker_failure():
    good = Account("ACTIVE", 100, 99, 100, 100, False, False)
    broker = AccountBroker(good)
    got = read_equity(broker, NOW)
    assert got.ok and broker.calls == ["account", "positions"]
    assert replace(good, status="CLOSED") and read_equity(AccountBroker(replace(good, status="CLOSED")), NOW).problem == "ACCOUNT_STATUS"
    assert read_equity(AccountBroker(replace(good, trading_blocked=True)), NOW).problem == "BLOCKED"
    assert read_equity(AccountBroker(replace(good, equity=float("nan"))), NOW).problem == "EQUITY_BAD"
    assert read_equity(AccountBroker(replace(good, cash=90)), NOW).problem == "INCONSISTENT"

    class Down:
        def get_account(self):
            raise BrokerUnavailable("down")

    assert read_equity(Down(), NOW).problem == "BROKER_DOWN"


def test_floor_confirmation_recovery_restart_and_rebase(tmp_path):
    first = rails(tmp_path)
    first.init_peak(100, "test")
    assert first.check_floor(reading(40)) == "CONFIRMING"
    assert first.check_floor(reading(40, NOW + timedelta(seconds=59))) == "CONFIRMING"
    assert first.check_floor(reading(50, NOW + timedelta(seconds=60))) == "OK"
    assert first.check_floor(reading(40, NOW + timedelta(seconds=70))) == "CONFIRMING"
    assert first.check_floor(reading(40, NOW + timedelta(seconds=130))) == "HALT"
    second = rails(tmp_path)
    assert second.floor_state() == "HALT" and second.liquidating()
    assert second.check_floor(reading(100)) == "HALT"
    second.clear_halt("owner", "ok", 40)
    assert second.check_floor(reading(40)) == "OK"


def test_bad_reading_never_changes_floor_and_missing_peak(tmp_path):
    item = rails(tmp_path)
    with pytest.raises(PeakNotInitialised):
        item.check_floor(reading(1))
    item.init_peak(100, "test")
    assert item.check_floor(reading(1, ok=False)) == "OK"
    item.check_floor(reading(40))
    assert item.check_floor(reading(1, NOW + timedelta(minutes=2), ok=False)) == "CONFIRMING"


def test_breaker_baselines_and_next_day(tmp_path):
    item = rails(tmp_path)
    session = date(2026, 11, 16)
    assert item.check_breaker(reading(85, last=100), session)
    assert item.breaker_info()["cause"] == "BREACH"
    assert not item.check_breaker(reading(100, last=100), session + timedelta(days=1))
    assert item.check_breaker(reading(100, last=float("nan")), session + timedelta(days=1))
    assert item.breaker_info()["cause"] == "BASELINE_DOUBT"

    other = rails(tmp_path / "other")
    other.record_close(session, 100)
    assert other.check_breaker(reading(100, last=90), session + timedelta(days=1))


def test_alert_episode_and_peak_only_rises(tmp_path):
    item = rails(tmp_path)
    item.init_peak(100, "test")
    assert item.check_alert(reading(63)) == pytest.approx(0.37)
    assert item.check_alert(reading(60)) is None
    assert item.check_alert(reading(67)) is None
    assert item.check_alert(reading(68.01)) is None
    assert item.check_alert(reading(63)) == pytest.approx(0.37)
    item.record_close(date(2026, 11, 16), 120)
    item.record_close(date(2026, 11, 17), 90)
    assert item.peak() == 120
    with pytest.raises(ValueError):
        item.init_peak(5, "again")


def decision(**changes):
    base = Decision(READY, None, 1, 0.2, 1, 0.5, 0.1, True, False, True, 0.3, 0.0, 0.5, 0.1, 0.6, True, 1 / 6, ("TREND_UP",))
    return replace(base, **changes)


@pytest.mark.parametrize(
    "value,expected",
    [
        (decision(core_held=0.3, core_new=0.3, ovl_held=0, ovl_new=0.1), (0.3, 0.0)),
        (decision(core_held=0.3, core_new=0.5, ovl_held=0, ovl_new=0), (0.3, 0.0)),
        (decision(core_held=0.3, core_new=0, ovl_held=0, ovl_new=0), (0.0, 0.0)),
        (decision(core_held=0.3, core_new=0.3, ovl_held=0.1, ovl_new=0.1), (0.3, 0.1)),
        (decision(core_held=0.3, core_new=0, ovl_held=0, ovl_new=0.1), (0.0, 0.0)),
    ],
)
def test_reduce_only(value, expected):
    result = reduce_only(value)
    assert (result.core_new, result.ovl_new) == expected
    changed = (value.core_new, value.ovl_new) != expected
    assert ("BREAKER_REDUCE_ONLY" in result.reason_keys) is changed


def test_reduce_only_not_ready_identity():
    value = decision(status=NOT_READY)
    assert reduce_only(value) is value


def test_floor_order_allowed_order():
    assert floor_order_allowed(True, True, True, 120, False, False) == (True, None)
    reasons = ["CLOCK_DOUBT", "CALENDAR_MISSING", "MARKET_CLOSED", "TOO_LATE", "UNRESOLVED", "FOREIGN_PRODUCT_ORDER"]
    for index, reason in enumerate(reasons):
        args = [True, True, True, 120, False, False]
        args[index if index < 3 else index] = [False, False, False, 119, True, True][index]
        assert floor_order_allowed(*args)[1] == reason
    assert floor_order_allowed(False, False, False, 0, True, True)[1] == "CLOCK_DOUBT"


def test_second_object_changes_are_never_lost(tmp_path):
    a = rails(tmp_path)
    b = rails(tmp_path)
    a.init_peak(100, "test")
    b.pause("owner", "wait")
    a.record_close(date(2026, 11, 16), 110)
    assert b.paused() and a.paused()
    a.check_floor(reading(40))
    a.check_floor(reading(40, NOW + timedelta(seconds=60)))
    assert b.floor_state() == "HALT"


def test_lock_busy_and_corrupt_state(tmp_path):
    held = ProcessLock(tmp_path / "rails.lock")
    assert held.acquire()
    sleeps = []
    try:
        with pytest.raises(LockBusy):
            rails(tmp_path, sleep=sleeps.append).init_peak(100, "test")
    finally:
        held.release()
    assert sum(sleeps) == pytest.approx(5.0)
    path = tmp_path / "rails.json"
    path.write_text("broken", encoding="utf-8")
    with pytest.raises(StateCorrupt):
        rails(tmp_path).state()
    assert path.read_text(encoding="utf-8") == "broken"


def test_pause_does_not_affect_floor(tmp_path):
    item = rails(tmp_path)
    item.init_peak(100, "test")
    item.pause("owner", "wait")
    assert item.check_floor(reading(40)) == "CONFIRMING"
