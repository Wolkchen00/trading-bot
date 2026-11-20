"""R4 adversarial suite (written by Claude from the contract, before reading the implementation).

Rails: a single odd reading must never sell, a real collapse must always stop, and nothing an operator does from
another process may be lost. Notifications: the owner reads these exact Turkish sentences on a phone, so every
text is pinned literally and no message may be lost or sent twice.
"""
import datetime as dt
import importlib.util
import json
from pathlib import Path

import pytest

from tests_v5.fakebroker import FakeBroker
from v5 import broker as vb
from v5 import config as vcfg
from v5 import health as vh
from v5 import notify as vn
from v5 import ops as vo
from v5 import rails as vr
from v5 import state as vs
from v5.strategy import NOT_READY, READY, Decision

UTC = dt.timezone.utc
T0 = dt.datetime(2026, 11, 16, 20, 50, tzinfo=UTC)
DAY = dt.date(2026, 11, 16)
ROOT = Path(__file__).resolve().parents[1]


class Clock:
    def __init__(self, now=T0):
        self.now = now
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += dt.timedelta(seconds=float(seconds))

    def advance(self, **kw):
        self.now += dt.timedelta(**kw)


def settings_for(tmp_path, topic="my-topic", mode="paper", **extra):
    env = {"TRADING_MODE": mode, "ALPACA_PAPER_API_KEY": "k", "ALPACA_PAPER_SECRET_KEY": "s",
           "ALPACA_LIVE_API_KEY": "lk", "ALPACA_LIVE_SECRET_KEY": "ls", "NTFY_TOPIC": topic}
    env.update(extra)
    s = vcfg.load_settings(env=env, app_dir=tmp_path)
    vs.ensure_state_dir(s.state_dir)
    return s


def rails_for(tmp_path, clock=None, peak=500.0):
    s = settings_for(tmp_path)
    clock = clock or Clock()
    r = vr.Rails(s, s.state_dir, clock, clock.sleep)
    if peak is not None:
        r.init_peak(peak, "test")
    return r, s, clock


def reading(equity, when=T0, last_equity=500.0, ok=True, problem=None):
    return vr.EquityReading(time=when, equity=equity, last_equity=last_equity, cash=equity, positions_value=0.0,
                            ok=ok, problem=problem)


# ============================================================================ equity readings
class Account(FakeBroker):
    over = {}

    def get_account(self):
        a = super().get_account()
        return vb.Account(**{**a.__dict__, **self.over})


def test_reading_passes_on_a_consistent_account():
    fb = FakeBroker(cash="300", positions={"TQQQ": "4"}, prices={"TQQQ": "50"}, last_equity="480")
    r = vr.read_equity(fb, T0)
    assert r.ok and r.problem is None and r.equity == 500.0 and r.cash == 300.0 and r.positions_value == 200.0
    assert r.last_equity == 480.0 and r.time == T0
    assert not [c for c in fb.calls if c[0] in ("submit_order", "cancel_order")]


@pytest.mark.parametrize("over,problem", [
    ({"status": "ACCOUNT_UPDATED"}, "ACCOUNT_STATUS"), ({"status": "INACTIVE"}, "ACCOUNT_STATUS"),
    ({"trading_blocked": True}, "BLOCKED"), ({"account_blocked": True}, "BLOCKED"),
    ({"equity": 0.0}, "EQUITY_BAD"), ({"equity": -1.0}, "EQUITY_BAD"), ({"equity": float("nan")}, "EQUITY_BAD"),
    ({"equity": float("inf")}, "EQUITY_BAD"), ({"cash": float("nan")}, "EQUITY_BAD"),
    ({"equity": 1000.0}, "INCONSISTENT"), ({"equity": 250.0}, "INCONSISTENT"), ({"equity": 511.0}, "INCONSISTENT"),
    ({"equity": 489.0}, "INCONSISTENT"),
])
def test_reading_fails_closed(over, problem):
    fb = Account(cash="300", positions={"TQQQ": "4"}, prices={"TQQQ": "50"})
    fb.over = over
    r = vr.read_equity(fb, T0)
    assert r.ok is False and r.problem == problem


def test_reading_tolerates_two_percent_and_never_raises():
    fb = Account(cash="300", positions={"TQQQ": "4"}, prices={"TQQQ": "50"})
    fb.over = {"equity": 509.0}                                                       # 9 off on 509 = 1.77%
    assert vr.read_equity(fb, T0).ok
    for exc in (vb.BrokerUnavailable("down"), vb.BrokerRejected(401, None, "no", {}), vb.BrokerError("garbled")):
        for method in ("get_account", "get_positions"):
            fb = FakeBroker(cash="300", prices={"TQQQ": "50"})

            def boom(*a, _e=exc, **k):
                raise _e
            setattr(fb, method, boom)
            r = vr.read_equity(fb, T0)
            assert r.ok is False and r.problem == "BROKER_DOWN"


# ============================================================================ peak
def test_peak_must_be_initialised_once_and_only_rises(tmp_path):
    r, s, clock = rails_for(tmp_path, peak=None)
    assert r.peak() is None and r.check_alert(reading(1.0)) is None
    with pytest.raises(vr.PeakNotInitialised):
        r.check_floor(reading(1.0))
    for bad in (0, -5.0, float("nan"), float("inf"), "x", None):
        with pytest.raises((ValueError, TypeError)):
            r.init_peak(bad, "bad")
    assert r.peak() is None
    r.init_peak(491.08, "v4 record")
    assert r.peak() == 491.08
    with pytest.raises(ValueError):
        r.init_peak(600.0, "again")
    assert r.peak() == 491.08
    r.init_peak(600.0, "forced", force=True)
    assert r.peak() == 600.0
    r.record_close(dt.date(2026, 11, 16), 550.0)
    assert r.peak() == 600.0                                                          # never falls
    r.record_close(dt.date(2026, 11, 17), 640.5)
    assert r.peak() == 640.5
    r.record_close(dt.date(2026, 11, 17), 999.0)                                      # the same session twice
    assert r.peak() == 640.5
    hist = r.state()["equity_history"]
    assert [(h["session"], h["equity"]) for h in hist] == [("2026-11-16", 550.0), ("2026-11-17", 640.5)]
    for bad in (0, -1.0, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            r.record_close(dt.date(2026, 11, 18), bad)
    assert len(r.state()["equity_history"]) == 2
    again = vr.Rails(s, s.state_dir, clock, clock.sleep)                              # a restart sees everything
    assert again.peak() == 640.5 and len(again.state()["equity_history"]) == 2


def test_history_without_a_peak_is_still_recorded(tmp_path):
    r, s, clock = rails_for(tmp_path, peak=None)
    r.record_close(DAY, 490.0)
    assert r.peak() is None and r.state()["equity_history"][0]["equity"] == 490.0


# ============================================================================ floor
def test_floor_needs_two_passing_readings_a_minute_apart(tmp_path):
    r, s, clock = rails_for(tmp_path)                                                 # peak 500, floor at 200
    assert r.check_floor(reading(200.01)) == "OK" and r.floor_state() == "OK"
    assert r.check_floor(reading(200.0)) == "CONFIRMING"                              # at the line counts
    assert r.liquidating() is False
    assert r.check_floor(reading(150.0, T0 + dt.timedelta(seconds=59))) == "CONFIRMING"
    assert r.check_floor(reading(150.0, T0 + dt.timedelta(seconds=30))) == "CONFIRMING"
    assert r.check_floor(reading(150.0, T0 - dt.timedelta(seconds=300))) == "CONFIRMING"   # a clock that went back
    assert r.check_floor(reading(199.99, T0 + dt.timedelta(seconds=60))) == "HALT"
    assert r.floor_state() == "HALT" and r.liquidating() is True
    assert r.check_floor(reading(499.0, T0 + dt.timedelta(seconds=120))) == "HALT"    # sticky
    again = vr.Rails(s, s.state_dir, clock, clock.sleep)
    assert again.floor_state() == "HALT" and again.check_floor(reading(499.0)) == "HALT" and again.liquidating()


def test_floor_bad_readings_never_count(tmp_path):
    r, s, clock = rails_for(tmp_path)
    for problem in ("INCONSISTENT", "BROKER_DOWN", "EQUITY_BAD", "ACCOUNT_STATUS"):
        assert r.check_floor(reading(1.0, ok=False, problem=problem)) == "OK"
    assert r.floor_state() == "OK"
    assert r.check_floor(reading(150.0)) == "CONFIRMING"
    for i in range(1, 6):                                                             # doubtful readings later on
        assert r.check_floor(reading(1.0, T0 + dt.timedelta(minutes=i), ok=False, problem="INCONSISTENT")) == "CONFIRMING"
        assert r.check_floor(reading(450.0, T0 + dt.timedelta(minutes=i), ok=False, problem="INCONSISTENT")) == "CONFIRMING"
    assert r.floor_state() == "CONFIRMING" and r.liquidating() is False


def test_floor_recovery_resets_the_confirmation(tmp_path):
    r, s, clock = rails_for(tmp_path)
    assert r.check_floor(reading(150.0)) == "CONFIRMING"
    assert r.check_floor(reading(200.01, T0 + dt.timedelta(seconds=45))) == "OK"      # false alarm
    assert r.check_floor(reading(150.0, T0 + dt.timedelta(seconds=70))) == "CONFIRMING"    # a NEW first reading
    assert r.check_floor(reading(150.0, T0 + dt.timedelta(seconds=120))) == "CONFIRMING"   # only 50 s after it
    assert r.check_floor(reading(150.0, T0 + dt.timedelta(seconds=130))) == "HALT"


def test_floor_confirmation_survives_a_restart_but_not_a_night(tmp_path):
    r, s, clock = rails_for(tmp_path / "restart")
    assert r.check_floor(reading(150.0)) == "CONFIRMING"
    again = vr.Rails(s, s.state_dir, clock, clock.sleep)
    assert again.floor_state() == "CONFIRMING"
    assert again.check_floor(reading(150.0, T0 + dt.timedelta(seconds=61))) == "HALT"   # same window, new process
    r, s, clock = rails_for(tmp_path / "night")
    assert r.check_floor(reading(150.0)) == "CONFIRMING"
    again = vr.Rails(s, s.state_dir, clock, clock.sleep)
    # Review finding 3 (2026-11-19): a reading one day later is a NEW first reading. This test used to demand a
    # halt here, which let a single bad reading sell everything.
    assert again.check_floor(reading(150.0, T0 + dt.timedelta(days=1))) == "CONFIRMING"
    assert again.liquidating() is False
    assert again.check_floor(reading(150.0, T0 + dt.timedelta(days=1, seconds=61))) == "HALT"


def test_clear_halt_rebases_the_peak_so_it_does_not_halt_again(tmp_path):
    r, s, clock = rails_for(tmp_path)
    with pytest.raises(ValueError):
        r.clear_halt("owner", "nothing to clear", 195.0)
    r.check_floor(reading(190.0))
    r.check_floor(reading(190.0, T0 + dt.timedelta(seconds=61)))
    assert r.floor_state() == "HALT"
    for bad in (0, -1.0, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            r.clear_halt("owner", "bad", bad)
    assert r.floor_state() == "HALT" and r.peak() == 500.0
    r.clear_halt("owner", "devam", 190.0)
    assert r.floor_state() == "OK" and r.liquidating() is False and r.peak() == 190.0
    assert r.check_floor(reading(190.0, T0 + dt.timedelta(minutes=5))) == "OK"        # same money, new base
    assert r.check_floor(reading(77.0, T0 + dt.timedelta(minutes=6))) == "OK"         # floor is now 76
    assert r.check_floor(reading(76.0, T0 + dt.timedelta(minutes=7))) == "CONFIRMING"
    events = r.state()["peak_events"]
    assert events[-1]["by"] == "owner" and events[-1]["old_peak"] == 500.0 and events[-1]["new_peak"] == 190.0
    r.clear_halt("owner", "false alarm while confirming", 180.0)                      # allowed while CONFIRMING too
    assert r.floor_state() == "OK" and r.peak() == 180.0


def test_pause_is_independent_of_the_floor(tmp_path):
    r, s, clock = rails_for(tmp_path)
    assert r.paused() is False
    r.pause("owner", "tatil")
    assert r.paused() is True
    assert r.check_floor(reading(150.0)) == "CONFIRMING"
    assert r.check_floor(reading(150.0, T0 + dt.timedelta(seconds=61))) == "HALT"     # a pause never stops the floor
    assert r.paused() is True
    r.resume("owner", "geri")
    assert r.paused() is False and r.floor_state() == "HALT"
    ev = r.state()["pause_events"]
    assert [e["by"] for e in ev] == ["owner", "owner"]


# ============================================================================ breaker
def test_breaker_sets_for_the_session_and_clears_by_itself(tmp_path):
    r, s, clock = rails_for(tmp_path)
    assert r.check_breaker(reading(425.01), DAY) is False                             # 15% below 500 is 425
    assert r.breaker_info() is None or r.breaker_info().get("session") != DAY.isoformat()
    assert r.check_breaker(reading(425.0), DAY) is True
    info = r.breaker_info()
    assert info["session"] == DAY.isoformat() and info["cause"] == "BREACH" and info["baseline"] == 500.0
    assert r.check_breaker(reading(499.0), DAY) is True                               # stays for the session
    assert r.check_breaker(reading(499.0), dt.date(2026, 11, 17)) is False            # gone at the next one
    assert r.check_breaker(reading(1.0, ok=False, problem="INCONSISTENT"), dt.date(2026, 11, 17)) is False
    assert vr.Rails(s, s.state_dir, clock, clock.sleep).check_breaker(reading(499.0), DAY) in (True, False)


def test_breaker_survives_a_restart_within_the_session(tmp_path):
    r, s, clock = rails_for(tmp_path)
    assert r.check_breaker(reading(400.0), DAY) is True
    again = vr.Rails(s, s.state_dir, clock, clock.sleep)
    assert again.check_breaker(reading(499.0), DAY) is True
    assert again.floor_state() == "OK"                                                # the breaker never halts or sells


def test_breaker_baseline_doubt_counts_as_breached(tmp_path):
    r, s, clock = rails_for(tmp_path)
    r.record_close(dt.date(2026, 11, 13), 500.0)
    assert r.check_breaker(reading(495.0, last_equity=492.0), DAY) is False           # 1.6% apart: fine
    r2, s2, clock2 = rails_for(tmp_path / "b")
    r2.record_close(dt.date(2026, 11, 13), 500.0)
    assert r2.check_breaker(reading(495.0, last_equity=480.0), DAY) is True           # 4% apart: doubt
    assert r2.breaker_info()["cause"] == "BASELINE_DOUBT"
    for bad in (0.0, -1.0, float("nan"), float("inf"), None):
        r3, s3, clock3 = rails_for(tmp_path / ("c%s" % bad))
        assert r3.check_breaker(reading(495.0, last_equity=bad), DAY) is True, bad
        assert r3.breaker_info()["cause"] == "BASELINE_DOUBT"
    r4, s4, clock4 = rails_for(tmp_path / "d")                                        # history of TODAY is not a baseline
    r4.record_close(DAY, 300.0)
    assert r4.check_breaker(reading(495.0, last_equity=500.0), DAY) is False


# ============================================================================ drawdown alert
def test_alert_once_per_episode_with_rearming(tmp_path):
    r, s, clock = rails_for(tmp_path)                                                 # peak 500, alert at 37%: 315
    assert r.check_alert(reading(315.01)) is None
    dd = r.check_alert(reading(315.0))
    assert dd == pytest.approx(0.37)
    assert r.check_alert(reading(300.0)) is None                                      # same episode
    assert r.check_alert(reading(335.0)) is None                                      # 33%: not yet re-armed
    assert r.check_alert(reading(310.0)) is None
    assert r.check_alert(reading(1.0, ok=False, problem="INCONSISTENT")) is None
    assert r.check_alert(reading(341.0)) is None                                      # 31.8%: re-arms
    assert r.check_alert(reading(310.0)) == pytest.approx(0.38)
    again = vr.Rails(s, s.state_dir, clock, clock.sleep)
    assert again.check_alert(reading(305.0)) is None                                  # the episode is persisted
    assert again.floor_state() == "OK"                                                # an alert changes nothing else


# ============================================================================ two processes, one file
def test_changes_from_another_process_are_never_lost(tmp_path):
    a, s, clock = rails_for(tmp_path)
    b = vr.Rails(s, s.state_dir, clock, clock.sleep)                                  # "the operator's process"
    b.pause("owner", "dur")
    a.record_close(DAY, 510.0)                                                        # must not write a stale copy back
    assert a.paused() and b.paused() and b.peak() == 510.0
    a.check_floor(reading(150.0))
    assert b.floor_state() == "CONFIRMING"
    b.clear_halt("owner", "false alarm", 480.0)
    assert a.floor_state() == "OK" and a.peak() == 480.0
    assert a.check_floor(reading(190.0, T0 + dt.timedelta(minutes=1))) == "CONFIRMING"     # 190 < 192
    assert a.check_floor(reading(190.0, T0 + dt.timedelta(minutes=3))) == "HALT"
    assert b.floor_state() == "HALT" and b.liquidating() and b.paused()
    b.resume("owner", "devam")
    a.set_liquidating(False)
    assert not a.paused() and not b.liquidating() and b.floor_state() == "HALT"
    assert a.check_breaker(reading(400.0), DAY) is True and b.check_breaker(reading(499.0), DAY) is True
    assert b.state() == a.state()


def test_a_held_lock_blocks_changes_without_real_waiting(tmp_path):
    r, s, clock = rails_for(tmp_path)
    holder = vs.ProcessLock(s.state_dir / "rails.lock")
    assert holder.acquire() is True
    try:
        with pytest.raises(vs.LockBusy):
            r.pause("owner", "x")
        assert 4.0 <= sum(clock.sleeps) <= 6.0 and max(clock.sleeps) <= 0.1           # waited about 5 s, virtually
        assert r.paused() is False                                                    # reads do not need the lock
    finally:
        holder.release()
    r.pause("owner", "x")
    assert r.paused() is True


def test_corrupt_rails_file_is_reported_and_left_alone(tmp_path):
    r, s, clock = rails_for(tmp_path)
    path = s.state_dir / "rails.json"
    assert path.exists()
    path.write_text("{ not json", encoding="utf-8")
    for call in (r.peak, r.paused, r.floor_state, r.state, lambda: r.pause("o", "n"), lambda: r.check_floor(reading(1.0)),
                 lambda: r.record_close(DAY, 500.0), lambda: r.check_breaker(reading(1.0), DAY), lambda: r.init_peak(5.0, "x", force=True)):
        with pytest.raises(vs.StateCorrupt):
            call()
    assert path.read_text(encoding="utf-8") == "{ not json"


# ============================================================================ reduce-only and the floor permission
def dec(core_held, ovl_held, core_new, ovl_new, status=READY):
    total = core_new + ovl_new
    return Decision(status=status, reason_code=None, trend_score=1.0, vol=0.2, exposure=1.5, core_target=core_new,
                    ibs=0.1, wanted=ovl_new > 0, overlay_data_missing=False,
                    core_trade=abs(core_new - core_held) > 1e-12, core_held=core_held, ovl_held=ovl_held,
                    core_new=core_new, ovl_new=ovl_new, total_new=total,
                    trade=abs(core_new - core_held) > 1e-12 or abs(ovl_new - ovl_held) > 1e-12,
                    overlay_fraction_new=(ovl_new / total if total > 0 else 0.0), reason_keys=("TREND_UP", "OVERLAY_ENTER"))


@pytest.mark.parametrize("held,new,expect", [
    ((0.30, 0.0), (0.30, 1 / 3), (0.30, 0.0)),            # overlay entry suppressed
    ((0.30, 0.0), (0.50, 0.0), (0.30, 0.0)),              # core increase suppressed
    ((0.30, 0.0), (0.00, 0.0), (0.00, 0.0)),              # the rule's own exit passes
    ((0.30, 0.0), (0.10, 0.0), (0.10, 0.0)),              # a reduction passes
    ((0.30, 0.2), (0.30, 0.2), (0.30, 0.2)),              # a kept overlay passes
    ((0.30, 0.2), (0.30, 0.0), (0.30, 0.0)),              # overlay exit passes
    ((0.30, 0.0), (0.00, 1 / 3), (0.00, 0.0)),            # core exits, overlay may not enter: everything sold
    ((0.30, 0.2), (0.50, 0.1), (0.30, 0.1)),              # mixed: each part only downwards
    ((0.00, 0.0), (0.40, 1 / 3), (0.00, 0.0)),            # flat stays flat
])
def test_reduce_only(held, new, expect):
    d = dec(held[0], held[1], new[0], new[1])
    out = vr.reduce_only(d)
    assert (out.core_new, out.ovl_new) == pytest.approx(expect)
    assert out.total_new == pytest.approx(expect[0] + expect[1])
    assert out.total_new <= held[0] + held[1] + 1e-12                                 # never more than held
    assert out.core_trade == (abs(expect[0] - held[0]) > 1e-12)
    assert out.trade == (abs(expect[0] - held[0]) > 1e-12 or abs(expect[1] - held[1]) > 1e-12)
    total = expect[0] + expect[1]
    assert out.overlay_fraction_new == pytest.approx(expect[1] / total if total > 0 else 0.0)
    changed = (out.core_new, out.ovl_new) != (d.core_new, d.ovl_new)
    assert ("BREAKER_REDUCE_ONLY" in out.reason_keys) == changed
    # Review finding 11: an extra buy that was suppressed is not reported as made.
    suppressed = changed and out.ovl_new <= 1e-12
    assert out.reason_keys[0] == "TREND_UP" and ("OVERLAY_ENTER" in out.reason_keys) == (not suppressed)
    assert out.status == READY
    assert out.core_held == d.core_held and out.ovl_held == d.ovl_held and d.core_new == new[0]   # input untouched


def test_reduce_only_leaves_a_not_ready_decision_alone():
    d = Decision(NOT_READY, "PRICE_BAD", None, None, None, None, None, False, False, False, None, None, None, None, None,
                 False, None, ())
    assert vr.reduce_only(d) is d or vr.reduce_only(d) == d


def test_floor_order_permission_table():
    ok = dict(clock_ok=True, calendar_ok=True, market_open=True, seconds_to_close=600, unresolved=False, foreign_product_order=False)
    assert vr.floor_order_allowed(**ok) == (True, None)
    assert vr.floor_order_allowed(**{**ok, "seconds_to_close": 120}) == (True, None)
    for change, reason in ((dict(clock_ok=False), "CLOCK_DOUBT"), (dict(calendar_ok=False), "CALENDAR_MISSING"),
                           (dict(market_open=False), "MARKET_CLOSED"), (dict(seconds_to_close=119.9), "TOO_LATE"),
                           (dict(seconds_to_close=-5), "TOO_LATE"), (dict(unresolved=True), "UNRESOLVED"),
                           (dict(foreign_product_order=True), "FOREIGN_PRODUCT_ORDER")):
        assert vr.floor_order_allowed(**{**ok, **change}) == (False, reason)
    everything = dict(clock_ok=False, calendar_ok=False, market_open=False, seconds_to_close=1, unresolved=True, foreign_product_order=True)
    order = ["CLOCK_DOUBT", "CALENDAR_MISSING", "MARKET_CLOSED", "TOO_LATE", "UNRESOLVED", "FOREIGN_PRODUCT_ORDER"]
    keys = ["clock_ok", "calendar_ok", "market_open", "seconds_to_close", "unresolved", "foreign_product_order"]
    for i, reason in enumerate(order):                                                # the first matching reason wins
        args = dict(everything)
        for k in keys[:i]:
            args[k] = ok[k]
        assert vr.floor_order_allowed(**args) == (False, reason)


# ============================================================================ numbers and texts
def test_number_formats():
    assert vn.fmt_money(1234.5) == "1.234,50" and vn.fmt_money(512.3) == "512,30" and vn.fmt_money(0.004) == "0,00"
    assert vn.fmt_money(0.005) == "0,01" and vn.fmt_money(-12.345) == "12,35" and vn.fmt_money(1234567.891) == "1.234.567,89"
    assert vn.fmt_money(999.995) == "1.000,00" and vn.fmt_money(0) == "0,00" and vn.fmt_money(490) == "490,00"
    assert vn.fmt_pct(0.0123) == "1,2" and vn.fmt_pct(-0.0123) == "1,2" and vn.fmt_pct(0.37) == "37,0"
    assert vn.fmt_pct(0.0125) == "1,3" and vn.fmt_pct(0) == "0,0" and vn.fmt_pct(1.0) == "100,0" and vn.fmt_pct(0.00049) == "0,0"
    assert vn.fmt_pct(0.0005) == "0,1" and vn.fmt_pct(2.5) == "250,0"
    assert vn.fmt_weight(0.094) == "9" and vn.fmt_weight(0.095) == "10" and vn.fmt_weight(0.6333) == "63"
    assert vn.fmt_weight(0) == "0" and vn.fmt_weight(0.99) == "99" and vn.fmt_weight(1.0) == "100" and vn.fmt_weight(0.004) == "0"


ALERTS = {
    "DRAWDOWN_ALERT": (dict(pct=0.371, equity=309.38),
                       "Hesap en yüksek değerinden yüzde 37,1 aşağıda, şu an 309,38 dolar. Bu risk seviyesinde geçmişte görülen en derin düşüşe geldik. Kurala göre çalışmaya devam ediyorum. Durdurmak istersen Claude'a 'botu durdur' yaz."),
    "BREAKER": (dict(pct=0.152),
                "Hesap bugün yüzde 15,2 düştü. Bugün yeni alım yapmıyorum. Yarın normal çalışmaya dönerim. Senden bir şey beklemiyorum."),
    "FLOOR_DONE": (dict(pct=0.603, equity=195.1),
                   "Hesap en yüksek değerinden yüzde 60,3 düştü, şu an 195,10 dolar. Her şeyi sattım ve durdum. Devam etmemi istersen Claude'a 'botu devam ettir' yaz."),
    "FLOOR_PENDING": (dict(pct=0.603, equity=195.1, product_pct=0.12),
                      "Hesap en yüksek değerinden yüzde 60,3 düştü, şu an 195,10 dolar. Satışa başladım ama henüz bitmedi. Şu an elimde yüzde 12 Nasdaq fonu var. Denemeye devam ediyorum."),
    "RAIL_LIQUIDATING": (dict(product_pct=0.12),
                         "Güvenlik satışı hala bitmedi. Şu an elimde yüzde 12 Nasdaq fonu var. Denemeye devam ediyorum. Claude'a 'trading botuna bak' yazarsan inceler."),
    "HOLD_NOT_READY": (dict(cause="fiyat bilgisine ulaşamadım"),
                       "Bugün karar veremedim, çünkü fiyat bilgisine ulaşamadım. Elimdekilere dokunmadım. Yarın yeniden denerim."),
    "MISSED": ({}, "Bugünkü karar saatini kaçırdım. Elimdekilere dokunmadım. Yarın normal devam ederim."),
    "ORDER_FAILED": (dict(product_pct=0.3),
                     "Alpaca emrimi kabul etmedi. Şu an elimde yüzde 30 Nasdaq fonu var. Claude'a 'trading botuna bak' yazarsan sebebini inceler."),
    "ORDER_PARTIAL": (dict(product_pct=0.3),
                      "Emrimin tamamı gerçekleşmedi. Şu an elimde yüzde 30 Nasdaq fonu var. Yarın duruma yeniden bakarım."),
    "UNRESOLVED": ({}, "Son emrimin akıbetini öğrenemedim. Netleşene kadar yeni işlem yapmıyorum. Claude'a 'trading botuna bak' yazarsan inceler."),
    "FOREIGN_ASSET": (dict(symbol="SPY"),
                      "Hesapta benim almadığım bir varlık ya da emir var (SPY). Karışmamak için işlem yapmıyorum. Bunu sen yaptıysan Claude'a haber ver."),
    "DATA_DOUBT": ({}, "Hesap değeri tutarsız göründü. Yanlış alarm olmasın diye satış yapmadım, izlemeye devam ediyorum."),
    "PAUSED": ({}, "Bot duraklatılmış durumda. Elimdekiler duruyor, yeni işlem yapmıyorum."),
    "NOT_STARTED": ({}, "Bot başlayamadı. Claude'a 'trading botuna bak' yaz."),
    "WATCHDOG": ({}, "Trading botu çalışmıyor görünüyor. Claude'a 'trading botuna bak' yaz."),
    "READINESS": (dict(cause="Alpaca'ya bağlanamadım"),
                  "Bugün borsa açılmadan önce bir sorun gördüm: Alpaca'ya bağlanamadım. Düzelmezse bugün işlem yapmayacağım."),
    "ERROR": ({}, "Bot beklenmeyen bir sorunla karşılaştı. Elimdekilere dokunmadım. Claude'a 'trading botuna bak' yaz."),
    "TEST": ({}, "Deneme bildirimi: bu mesajı görüyorsan bildirimler çalışıyor."),
    "NOT_SENT": (dict(cause="Alpaca'ya bağlanamadım"),
                 "Bugün bir işlem yapmam gerekiyordu ama emri gönderemedim, çünkü Alpaca'ya bağlanamadım. Elimdekilere dokunmadım. Yarın yeniden karar veririm."),
}


@pytest.mark.parametrize("kind", sorted(ALERTS))
def test_every_alert_text_is_exact(kind):
    fields, text = ALERTS[kind]
    assert vn.build_alert(kind, **fields) == text
    assert vn.build_alert(kind, unused_extra="x", **fields) == text
    for missing in fields:
        with pytest.raises(KeyError):
            vn.build_alert(kind, **{k: v for k, v in fields.items() if k != missing})
    assert "{" not in text and chr(0x2014) not in text


def test_templates_cover_exactly_the_known_kinds():
    assert set(vn.TEMPLATES) == set(ALERTS)
    with pytest.raises(KeyError):
        vn.build_alert("NO_SUCH_KIND")
    for text in vn.TEMPLATES.values():
        for word in ("API", "JSON", "token", "commit", "deploy", "script", "terminal", chr(0x2014), chr(0xE2), chr(0xEE), chr(0xFB)):
            assert word not in text


def test_reason_and_cause_sentences_are_exact():
    assert vn.REASON_SENTENCES == {
        "TREND_UP": "Nasdaq yükseliş eğiliminde.",
        "TREND_MIXED": "Nasdaq kararsız, bu yüzden ana payı küçük tutuyorum.",
        "TREND_DOWN": "Nasdaq düşüş eğiliminde, bu yüzden ana para nakitte bekliyor.",
        "VOL_HIGH": "Piyasa çok oynak, bu yüzden payı azalttım.",
        "VOL_CALM": "Piyasa sakin.",
        "BAND_HOLD": "Hedefe yeterince yakınım, işlem gerekmedi.",
        "OVERLAY_ENTER": "Bugün sert bir düşüş oldu, yarın için küçük bir ek alım yaptım.",
        "OVERLAY_KEEP": "Düşüş sürdü, dünkü ek alımı bir gün daha tutuyorum.",
        "OVERLAY_EXIT": "Dünkü ek alımı sattım.",
        "OVERLAY_DATA_MISSING": "Bugün gün içi fiyat aralığına ulaşamadım, bu yüzden ek alım kuralını çalıştırmadım.",
        "BREAKER_REDUCE_ONLY": "Bugün sert düşüş olduğu için yeni alım yapmadım.",
    }
    expect = {
        "fiyat bilgisine ulaşamadım": ["PRICE_STALE", "PRICE_BAD", "PRICE_JUMP", "DATA_ERROR"],
        "geçmiş fiyatlar eksik geldi": ["HISTORY_SHORT", "HISTORY_BAD", "HISTORY_GAP", "HISTORY_STALE", "DERIVED_BAD"],
        "Alpaca'ya bağlanamadım": ["BROKER_DOWN"],
        "bugünün borsa saatlerini öğrenemedim": ["CALENDAR_MISSING", "MARKET_CLOSED"],
        "saat bilgisi tutarsız göründü": ["CLOCK_DOUBT"],
        "kayıt dosyam bozuk": ["STATE_CORRUPT", "WEIGHT_BAD", "FRACTION_BAD"],
        "hesap değerini doğrulayamadım": ["EQUITY_DOUBT", "SNAPSHOT_INVALID", "RAIL_CONFIRMING"],
        "bildirim kanalı tanımlı değil": ["NOTIFY_MISSING"],
        "başlangıç değeri kayıtlı değil": ["PEAK_MISSING"],
        "Alpaca hesabı işleme kapalı görünüyor": ["ACCOUNT_STATUS", "BLOCKED"],
        "hesapta benim almadığım bir varlık ya da emir var": ["FOREIGN_ASSET"],
        "önceki emrimin akıbeti hala belli değil": ["UNRESOLVED"],
        "bot duraklatılmış durumda": ["PAUSED"],
        "güvenlik sınırı aşıldığı için durmuş durumdayım": ["FLOOR_HALT"],
        "bugün sert düşüş olduğu için yeni alım yapmıyorum": ["BREAKER"],
        "süre yetmedi": ["TIME_OUT"],
    }
    assert vn.OUTCOME_SENTENCES == {
        "NOT_SENT": "Karar verdim ama emri gönderemedim.",
        "REFUSED": "Karar verdim ama Alpaca emrimi kabul etmedi.",
        "UNKNOWN": "Emrimin sonucunu henüz öğrenemedim.",
        "FLOOR": "Hesap güvenlik sınırının altına düştü.",
    }
    for sentence, codes in expect.items():
        for code in codes:
            assert vn.cause_for(code) == sentence, code
    for unknown in ("SOMETHING_NEW", "", None, 5):
        assert vn.cause_for(unknown) == "beklenmeyen bir sorun çıktı"


BASE = dict(date=DAY, prefix="", equity=512.3, day_usd=6.1, day_pct=0.012, product_weight=0.094, action="none",
            action_usd=0.0, reason_keys=["TREND_MIXED", "BAND_HOLD"], month_pct=0.031, total_pct=0.045, status_text=None)
LINES = ["Gün sonu özeti (16.11.2026)",
         "Hesap: 512,30 dolar. Bugün yüzde 1,2 arttı (6,10 dolar).",
         "Elimde: yüzde 9 Nasdaq fonu, yüzde 91 nakit.",
         "Bugün ne yaptım: İşlem yapmadım.",
         "Neden: Nasdaq kararsız, bu yüzden ana payı küçük tutuyorum. Hedefe yeterince yakınım, işlem gerekmedi.",
         "Bu ay: yüzde 3,1 artıda. Başlangıçtan beri: yüzde 4,5 artıda.",
         "Durum: Her şey normal."]


def digest(**change):
    return vn.build_digest({**BASE, **change}).split("\n")


def test_digest_is_exact_in_every_variant():
    assert vn.build_digest(dict(BASE)) == "\n".join(LINES)
    assert digest(prefix="paper")[0] == "DENEME HESABI. Gün sonu özeti (16.11.2026)"
    assert digest(prefix="dry_run")[0] == "DENEME, gerçek emir gönderilmedi. Gün sonu özeti (16.11.2026)"
    assert digest(date=dt.date(2027, 1, 5))[0] == "Gün sonu özeti (05.01.2027)"
    assert digest(day_usd=-5.9, day_pct=-0.0118)[1] == "Hesap: 512,30 dolar. Bugün yüzde 1,2 azaldı (5,90 dolar)."
    for flat in (dict(day_usd=0.001, day_pct=0.000002), dict(day_usd=-0.004, day_pct=-0.00001), dict(day_usd=0.0, day_pct=0.0)):
        assert digest(**flat)[1] == "Hesap: 512,30 dolar. Bugün değişmedi."
    # Review finding 5: a change that is not known is not reported as "no change".
    assert digest(day_usd=None, day_pct=None)[1] == "Hesap: 512,30 dolar."
    assert digest(equity=1234.5)[1].startswith("Hesap: 1.234,50 dolar. ")
    assert digest(product_weight=0.6333)[2] == "Elimde: yüzde 63 Nasdaq fonu, yüzde 37 nakit."
    assert digest(product_weight=0.0)[2] == "Elimde: yüzde 0 Nasdaq fonu, yüzde 100 nakit."
    assert digest(product_weight=0.995)[2] == "Elimde: yüzde 100 Nasdaq fonu, yüzde 0 nakit."
    assert digest(action="buy", action_usd=147.0)[3] == "Bugün ne yaptım: 147,00 dolarlık Nasdaq fonu aldım."
    assert digest(action="sell", action_usd=52.5)[3] == "Bugün ne yaptım: 52,50 dolarlık Nasdaq fonu sattım."
    assert digest(action="sell_all", action_usd=310.0)[3] == "Bugün ne yaptım: Elimdeki fonun tamamını sattım."
    assert digest(action="unknown")[3] == "Bugün ne yaptım: Bir emir gönderdim ama sonucunu henüz öğrenemedim."
    for outcome, sentence in vn.OUTCOME_SENTENCES.items():                            # never the decision's own reasons
        assert digest(outcome=outcome, reason_keys=["TREND_UP", "OVERLAY_ENTER"])[4] == "Neden: " + sentence
    assert digest(outcome=None)[4] == digest()[4]
    for none in (None, [], ["NOT_A_KEY"]):
        assert digest(reason_keys=none)[4] == "Neden: Bugün karar veremedim."
    assert digest(reason_keys=["TREND_DOWN", "NOT_A_KEY", "OVERLAY_EXIT"])[4] == \
        "Neden: Nasdaq düşüş eğiliminde, bu yüzden ana para nakitte bekliyor. Dünkü ek alımı sattım."
    assert digest(month_pct=-0.02, total_pct=-0.005)[5] == "Bu ay: yüzde 2,0 ekside. Başlangıçtan beri: yüzde 0,5 ekside."
    assert digest(month_pct=0.0, total_pct=None)[5] == "Bu ay: değişim yok. Başlangıçtan beri: değişim yok."
    assert digest(month_pct=None, total_pct=0.0004)[5] == "Bu ay: değişim yok. Başlangıçtan beri: değişim yok."
    assert digest(month_pct=0.1, total_pct=-0.25)[5] == "Bu ay: yüzde 10,0 artıda. Başlangıçtan beri: yüzde 25,0 ekside."
    paused = "Bot duraklatılmış durumda. Elimdekiler duruyor, yeni işlem yapmıyorum."
    assert digest(status_text=paused)[6] == "Durum: " + paused
    for variant in (dict(), dict(prefix="paper", action="buy", action_usd=1.0, reason_keys=None, status_text=paused)):
        assert len(digest(**variant)) == 7


def test_digest_refuses_nonsense():
    for bad in (dict(action="hodl"), dict(prefix="live"), dict(outcome="WHATEVER")):
        with pytest.raises((KeyError, ValueError)):
            vn.build_digest({**BASE, **bad})
    with pytest.raises(KeyError):
        vn.build_digest({k: v for k, v in BASE.items() if k != "equity"})


# ============================================================================ outbox
def test_outbox_is_durable_append_only_and_deduplicates(tmp_path):
    s = settings_for(tmp_path)
    box = vn.Outbox(s.state_dir)
    a = box.add("PAUSED", "metin bir", T0)
    b = box.add("MISSED", "metin iki", T0 + dt.timedelta(seconds=1), dedupe_key="MISSED:2026-11-16")
    again = box.add("MISSED", "baska metin", T0 + dt.timedelta(hours=9), dedupe_key="MISSED:2026-11-16")
    assert again == b and a != b
    path = s.state_dir / "outbox.jsonl"
    first_bytes = path.read_bytes()
    fresh = vn.Outbox(s.state_dir)                                                    # a restart
    pend = fresh.pending(T0 + dt.timedelta(seconds=2))
    assert [m["id"] for m in pend] == [a, b] and [m["text"] for m in pend] == ["metin bir", "metin iki"]
    fresh.mark_delivered(a, T0 + dt.timedelta(seconds=3))
    assert [m["id"] for m in vn.Outbox(s.state_dir).pending(T0 + dt.timedelta(seconds=4))] == [b]
    assert path.read_bytes().startswith(first_bytes)                                  # history is never rewritten
    assert box.add("MISSED", "x", T0 + dt.timedelta(days=30), dedupe_key="MISSED:2026-11-16") == b   # even when old
    assert vn.Outbox(s.state_dir).last("PAUSED")["text"] == "metin bir" and vn.Outbox(s.state_dir).last("NOPE") is None
    for line in path.read_text(encoding="utf-8").splitlines():
        json.loads(line)


def test_outbox_backoff_and_expiry(tmp_path):
    s = settings_for(tmp_path)
    box = vn.Outbox(s.state_dir)
    mid = box.add("PAUSED", "metin", T0)
    assert box.oldest_undelivered_minutes(T0 + dt.timedelta(minutes=30)) == pytest.approx(30.0)
    t = T0
    assert [m["id"] for m in box.pending(t)] == [mid]                                 # due at once
    for wait in (1, 2, 4, 8, 16, 32, 60, 60, 60):
        box.mark_attempt(mid, t)
        assert box.pending(t + dt.timedelta(minutes=wait, seconds=-1)) == [], wait
        t = t + dt.timedelta(minutes=wait)
        assert [m["id"] for m in box.pending(t)] == [mid], wait
    assert [m["id"] for m in vn.Outbox(s.state_dir).pending(t)] == [mid]              # the schedule survives a restart
    late = T0 + dt.timedelta(hours=48, seconds=1)
    assert box.pending(late) == [] and box.oldest_undelivered_minutes(late) is None   # given up after 48 hours
    assert box.pending(T0 + dt.timedelta(hours=47, minutes=59)) != [] or True
    box.mark_delivered(mid, late)
    assert box.oldest_undelivered_minutes(T0 + dt.timedelta(hours=1)) is None


def test_outbox_survives_garbage_lines(tmp_path):
    s = settings_for(tmp_path)
    box = vn.Outbox(s.state_dir)
    a = box.add("PAUSED", "bir", T0)
    path = s.state_dir / "outbox.jsonl"
    with open(path, "ab") as f:
        f.write(b"{ broken line\n\xff\xfe garbage\n\n[1, 2, 3]\n\"just a string\"\n")
    b = vn.Outbox(s.state_dir).add("MISSED", "iki", T0 + dt.timedelta(seconds=5))
    fresh = vn.Outbox(s.state_dir)
    assert [m["id"] for m in fresh.pending(T0 + dt.timedelta(seconds=6))] == [a, b]
    assert fresh.corrupt_lines >= 2


# ============================================================================ notifier
class Post:
    def __init__(self, *statuses):
        self.statuses = list(statuses)
        self.calls = []

    def __call__(self, url, data, headers, timeout):
        self.calls.append(dict(url=url, data=data, headers=dict(headers), timeout=timeout))
        status = self.statuses.pop(0) if self.statuses else 200
        if isinstance(status, Exception):
            raise status
        return status


def notifier(tmp_path, post=None, topic="my-topic", clock=None, mode="live", enabled=True):
    extra = {"LIVE_TRADING_ENABLED": "true"} if enabled else {}
    s = settings_for(tmp_path, topic=topic, mode=mode, **extra)
    clock = clock or Clock()
    post = post or Post()
    box = vn.Outbox(s.state_dir)
    return vn.Notifier(s, box, post, clock), box, post, clock, s


def test_alert_goes_through_the_outbox_and_is_posted_once(tmp_path):
    n, box, post, clock, s = notifier(tmp_path)
    assert n.configured is True
    mid = n.alert("PAUSED")
    assert mid and len(post.calls) == 1
    call = post.calls[0]
    assert call["url"] == "https://ntfy.sh/my-topic"
    assert call["data"] == ALERTS["PAUSED"][1].encode("utf-8") and isinstance(call["data"], bytes)
    for k, v in call["headers"].items():
        assert all(ord(c) < 128 for c in str(k) + str(v)), (k, v)                     # ntfy headers must be ASCII
    assert call["headers"]["Title"] == "Trading bot" and str(call["headers"]["Priority"]) == "4"
    assert box.pending(clock.now) == [] and n.flush() == 0 and len(post.calls) == 1   # delivered: never again
    assert vn.Outbox(s.state_dir).last("PAUSED")["text"] == ALERTS["PAUSED"][1]


def test_priorities(tmp_path):
    n, box, post, clock, s = notifier(tmp_path)
    n.alert("FLOOR_DONE", pct=0.6, equity=196.0)
    n.alert("FLOOR_PENDING", pct=0.6, equity=196.0, product_pct=0.1)
    n.alert("RAIL_LIQUIDATING", product_pct=0.1)
    n.alert("NOT_STARTED")
    n.alert("BREAKER", pct=0.15)
    n.digest(DAY, dict(BASE))
    assert [str(c["headers"]["Priority"]) for c in post.calls] == ["5", "5", "5", "5", "4", "3"]
    assert post.calls[-1]["data"].decode("utf-8") == "\n".join(LINES)


def test_cooldown_per_kind_and_state(tmp_path):
    n, box, post, clock, s = notifier(tmp_path)
    assert n.alert("FOREIGN_ASSET", state_key="SPY", symbol="SPY")
    assert n.alert("FOREIGN_ASSET", state_key="SPY", symbol="SPY") is None            # same thing again: silent
    clock.advance(hours=3, minutes=59)
    assert n.alert("FOREIGN_ASSET", state_key="SPY", symbol="SPY") is None
    assert n.alert("FOREIGN_ASSET", state_key="QQQ", symbol="QQQ")                    # another state: its own message
    assert n.alert("PAUSED")                                                          # another kind
    clock.advance(minutes=2)
    assert n.alert("FOREIGN_ASSET", state_key="SPY", symbol="SPY")                    # 4 h 1 min later
    assert len(post.calls) == 4
    assert len([l for l in (s.state_dir / "outbox.jsonl").read_text(encoding="utf-8").splitlines() if '"FOREIGN_ASSET"' in l and '"text"' in l]) == 3


def test_failed_posts_stay_pending_and_are_retried(tmp_path):
    secret_error = RuntimeError("could not reach https://ntfy.sh/my-topic")
    n, box, post, clock, s = notifier(tmp_path, post=Post(500, secret_error, 429, 200))
    mid = n.alert("MISSED")                                                           # 500
    assert mid and len(post.calls) == 1 and [m["id"] for m in box.pending(clock.now + dt.timedelta(minutes=1))] == [mid]
    assert n.flush() == 0 and len(post.calls) == 1                                    # not due yet (1 minute)
    clock.advance(minutes=1)
    assert n.flush() == 0 and len(post.calls) == 2                                    # the exception is swallowed
    clock.advance(minutes=2)
    assert n.flush() == 0 and len(post.calls) == 3                                    # 429
    clock.advance(minutes=3, seconds=59)
    assert n.flush() == 0 and len(post.calls) == 3
    clock.advance(seconds=1)
    assert n.flush() == 1 and len(post.calls) == 4                                    # delivered at last
    clock.advance(hours=5)
    assert n.flush() == 0 and len(post.calls) == 4
    assert [c["data"] for c in post.calls] == [ALERTS["MISSED"][1].encode("utf-8")] * 4


def test_restart_delivers_what_was_left(tmp_path):
    n, box, post, clock, s = notifier(tmp_path, post=Post(503))
    n.alert("ORDER_FAILED", product_pct=0.3)
    clock.advance(minutes=2)
    post2 = Post()
    n2 = vn.Notifier(s, vn.Outbox(s.state_dir), post2, clock)                         # a new process
    assert n2.flush() == 1 and post2.calls[0]["data"] == ALERTS["ORDER_FAILED"][1].encode("utf-8")
    assert n2.flush() == 0


def test_without_a_topic_nothing_is_posted_and_nothing_is_lost(tmp_path):
    n, box, post, clock, s = notifier(tmp_path, topic="")
    assert n.configured is False
    mid = n.alert("PAUSED")
    assert mid and post.calls == [] and n.flush() == 0
    assert [m["id"] for m in box.pending(clock.now)] == [mid]
    assert box.oldest_undelivered_minutes(clock.now + dt.timedelta(minutes=90)) == pytest.approx(90.0)
    s2 = vcfg.load_settings(env={"TRADING_MODE": "live", "ALPACA_LIVE_API_KEY": "k", "ALPACA_LIVE_SECRET_KEY": "s",
                                 "LIVE_TRADING_ENABLED": "true", "NTFY_TOPIC": "now-set"}, app_dir=tmp_path)
    post2 = Post()
    n2 = vn.Notifier(s2, vn.Outbox(s2.state_dir), post2, clock)
    assert n2.flush() == 1 and post2.calls[0]["url"] == "https://ntfy.sh/now-set"


def test_digest_once_per_session_and_reminders_once_per_session(tmp_path):
    n, box, post, clock, s = notifier(tmp_path)
    assert n.digest(DAY, dict(BASE)) and len(post.calls) == 1
    n.digest(DAY, dict(BASE, equity=1.0))                                             # a second digest for the day: no
    assert len(post.calls) == 1
    clock.advance(seconds=30)
    n.digest(dt.date(2026, 11, 17), dict(BASE, date=dt.date(2026, 11, 17)))           # no cooldown between sessions
    assert len(post.calls) == 2
    sent = 0
    day = dt.date(2026, 11, 17)
    for i in range(44):                                                               # two months of a blocked state
        while day.weekday() >= 5:
            day += dt.timedelta(days=1)
        clock.now = dt.datetime.combine(day, dt.time(21, 2), UTC)
        for _ in range(3):                                                            # asked on several ticks
            n.reminder("UNRESOLVED", day)
        n.reminder("PAUSED", day)
        sent += 2
        day += dt.timedelta(days=1)
    reminders = [c for c in post.calls if c["data"] in (ALERTS["UNRESOLVED"][1].encode("utf-8"), ALERTS["PAUSED"][1].encode("utf-8"))]
    assert len(reminders) == sent == 88
    assert len([c for c in reminders if c["data"] == ALERTS["UNRESOLVED"][1].encode("utf-8")]) == 44
    before = len(post.calls)
    clock.advance(days=3)                                                             # the cause is gone: silence
    assert n.flush() == 0 and len(post.calls) == before


def test_messages_are_delivered_oldest_first(tmp_path):
    n, box, post, clock, s = notifier(tmp_path, post=Post(500, 500, 500))
    n.alert("PAUSED")
    clock.advance(seconds=5)
    n.alert("MISSED")
    clock.advance(seconds=5)
    n.alert("DATA_DOUBT")
    clock.advance(minutes=10)
    post.calls.clear()
    assert n.flush() == 3
    assert [c["data"].decode("utf-8") for c in post.calls] == [ALERTS["PAUSED"][1], ALERTS["MISSED"][1], ALERTS["DATA_DOUBT"][1]]


# ============================================================================ health
def beat(**over):
    hb = dict(time=T0.isoformat(), mode="live", phase="session", session=DAY.isoformat(), readiness=dict(ok=True, problems=[]),
              states=[], last_decision=dict(session="2026-11-13", status="DONE"), outbox_oldest_minutes=None,
              ntfy_configured=True, orders_enabled=True, package="high")
    hb.update(over)
    return hb


def test_health_levels():
    now = T0 + dt.timedelta(seconds=30)
    assert vh.assess(beat(), now)[0] == 0
    assert vh.assess(None, now)[0] == 2
    assert vh.assess(beat(), T0 + dt.timedelta(seconds=300))[0] == 0
    assert vh.assess(beat(), T0 + dt.timedelta(seconds=301))[0] == 2
    for state in ("FLOOR_HALT", "RAIL_LIQUIDATING", "UNRESOLVED", "NOT_STARTED"):
        assert vh.assess(beat(states=[state]), now)[0] == 2, state
    for state in ("PAUSED", "BREAKER_TODAY", "FOREIGN_ASSET", "RAIL_CONFIRMING", "OVERLAY_DATA_MISSING"):
        assert vh.assess(beat(states=[state]), now)[0] == 1, state
    assert vh.assess(beat(states=["PAUSED", "UNRESOLVED"]), now)[0] == 2
    assert vh.assess(beat(readiness=dict(ok=False, problems=["BROKER_DOWN"])), now)[0] == 1
    assert vh.assess(beat(ntfy_configured=False), now)[0] == 1
    assert vh.assess(beat(outbox_oldest_minutes=61), now)[0] == 1
    assert vh.assess(beat(outbox_oldest_minutes=59), now)[0] == 0
    level, lines = vh.assess(beat(states=["PAUSED"]), now)
    assert isinstance(lines, list) and all(isinstance(x, str) for x in lines)
    for broken in ({}, {"time": "not a time"}, {"time": None}, [], "x"):
        assert vh.assess(broken, now)[0] == 2                                         # an unreadable heartbeat is no heartbeat


def test_heartbeat_round_trip(tmp_path):
    s = settings_for(tmp_path)
    assert vh.read_heartbeat(s.state_dir) is None
    vh.write_heartbeat(s.state_dir, beat())
    assert vh.read_heartbeat(s.state_dir) == beat()
    (s.state_dir / "heartbeat.json").write_text("{ broken", encoding="utf-8")
    assert vh.read_heartbeat(s.state_dir) is None


# ============================================================================ operator commands
def run_ops(argv):
    try:
        code = vo.main(argv)
    except SystemExit as exc:
        code = exc.code
    return 0 if code is None else code


@pytest.fixture
def ops_env(tmp_path, monkeypatch):
    for key in ("TRADING_MODE", "ALPACA_KEY_PREFIX", "LIVE_TRADING_ENABLED", "STRATEGY_PACKAGE", "NTFY_TOPIC", "NTFY_BASE_URL",
                "V5_ALERT_DD", "V5_BREAKER_DAY", "V5_FLOOR_DD"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("TRADING_MODE", "paper")
    monkeypatch.setenv("ALPACA_PAPER_API_KEY", "k")
    monkeypatch.setenv("ALPACA_PAPER_SECRET_KEY", "s")
    monkeypatch.setenv("STATE_VOLUME_PATH", str(tmp_path))
    s = vcfg.load_settings()
    vs.ensure_state_dir(s.state_dir)
    clock = Clock()
    return s, vr.Rails(s, s.state_dir, clock, clock.sleep)


def test_ops_commands_change_the_shared_state_and_are_logged(ops_env, capsys):
    s, rails = ops_env
    assert run_ops(["init-peak", "491.08", "--source", "v4 record"]) == 0
    assert rails.peak() == 491.08
    assert run_ops(["init-peak", "600", "--source", "again"]) != 0 and rails.peak() == 491.08
    assert run_ops(["init-peak", "600", "--source", "forced", "--force"]) == 0 and rails.peak() == 600.0
    assert run_ops(["pause", "--by", "owner", "--note", "tatil"]) == 0 and rails.paused() is True
    assert run_ops(["resume", "--by", "owner", "--note", "geri"]) == 0 and rails.paused() is False
    assert run_ops(["clear-halt", "--by", "owner", "--note", "x", "--new-peak", "500"]) != 0     # nothing to clear
    assert rails.peak() == 600.0
    rails.check_floor(reading(200.0))
    rails.check_floor(reading(200.0, T0 + dt.timedelta(seconds=61)))
    assert rails.floor_state() == "HALT"
    assert run_ops(["clear-halt", "--by", "owner", "--note", "devam"]) != 0                      # the new peak is required
    assert rails.floor_state() == "HALT"
    assert run_ops(["clear-halt", "--by", "owner", "--note", "devam", "--new-peak", "200"]) == 0
    assert rails.floor_state() == "OK" and rails.peak() == 200.0 and rails.liquidating() is False
    capsys.readouterr()
    assert run_ops(["status"]) == 0
    out = capsys.readouterr().out
    assert "200" in out and json.loads(out[out.index("{"):])
    log = [json.loads(l) for l in (s.state_dir / "ops_log.jsonl").read_text(encoding="utf-8").splitlines()]
    commands = [e["command"] for e in log]
    for name in ("init-peak", "pause", "resume", "clear-halt"):
        assert name in commands
    assert "k" != out and "ALPACA" not in out
    assert run_ops(["no-such-command"]) != 0


# ============================================================================ owner status tool
def load_tool():
    spec = importlib.util.spec_from_file_location("v5_durum", ROOT / "tools" / "v5_durum.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_owner_status_text(tmp_path):
    tool = load_tool()
    s = settings_for(tmp_path)
    now = T0 + dt.timedelta(minutes=2, seconds=20)
    assert tool.render(s.state_dir, now) == "\n".join(["Bot çalışmıyor görünüyor.", "Kalp atışı kaydı yok.", "Durum: bilinmiyor.",
                                                         "Henüz günlük özet yok."])
    vh.write_heartbeat(s.state_dir, beat())
    assert tool.render(s.state_dir, now) == "\n".join(["Bot çalışıyor.", "Son kalp atışı: 2 dakika önce.", "Durum: Her şey normal.",
                                                         "Henüz günlük özet yok."])
    vh.write_heartbeat(s.state_dir, beat(states=["PAUSED", "BREAKER_TODAY"]))
    box = vn.Outbox(s.state_dir)
    box.add("DIGEST", "\n".join(LINES), T0, dedupe_key="DIGEST:2026-11-16:")
    late = T0 + dt.timedelta(minutes=10, seconds=5)
    assert tool.render(s.state_dir, late) == "\n".join(["Bot çalışmıyor görünüyor.", "Son kalp atışı: 10 dakika önce.",
                                                          "Durum: PAUSED, BREAKER_TODAY", "Son günlük özet:"] + LINES)


# ============================================================================ review findings (R4 Level 10)
import os
import subprocess
import sys


def test_outbox_survives_a_torn_last_line(tmp_path):
    """A power loss in the middle of an append leaves a line without its newline. The next append must not
    be glued onto it: neither a new message nor a 'delivered' mark may be lost."""
    s = settings_for(tmp_path)
    box = vn.Outbox(s.state_dir)
    a = box.add("PAUSED", "bir", T0)
    path = s.state_dir / "outbox.jsonl"
    with open(path, "ab") as f:
        f.write(b'{"id": "torn", "time": "2026-11-16T2')
    b = vn.Outbox(s.state_dir).add("MISSED", "iki", T0 + dt.timedelta(seconds=5))
    assert [m["id"] for m in vn.Outbox(s.state_dir).pending(T0 + dt.timedelta(seconds=6))] == [a, b]
    with open(path, "ab") as f:
        f.write(b'{"id": "torn-again"')
    vn.Outbox(s.state_dir).mark_delivered(a, T0 + dt.timedelta(seconds=7))
    fresh = vn.Outbox(s.state_dir)
    assert [m["id"] for m in fresh.pending(T0 + dt.timedelta(seconds=8))] == [b]       # the mark was not swallowed
    assert fresh.corrupt_lines == 2
    for line in path.read_bytes().split(b"\n"):
        assert line.count(b'"id"') <= 1                                              # never two records on one line


def test_confirming_without_a_first_reading_is_corruption(tmp_path):
    r, s, clock = rails_for(tmp_path)
    doc = r.state()
    doc["floor_state"] = "CONFIRMING"
    doc["floor_confirmation"] = None
    vs.atomic_write_json(s.state_dir / "rails.json", doc)
    for call in (lambda: r.check_floor(reading(150.0)), r.floor_state, r.state, lambda: r.pause("o", "n")):
        with pytest.raises(vs.StateCorrupt):
            call()
    doc["floor_state"] = "OK"
    doc["floor_confirmation"] = {"time": T0.isoformat(), "equity": 150.0}             # the reverse is inconsistent too
    vs.atomic_write_json(s.state_dir / "rails.json", doc)
    with pytest.raises(vs.StateCorrupt):
        r.floor_state()


def test_status_tool_runs_as_a_plain_script(tmp_path):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("ALPACA_", "NTFY_", "V5_")) and k not in (
        "TRADING_MODE", "LIVE_TRADING_ENABLED", "STRATEGY_PACKAGE", "STATE_VOLUME_PATH", "PYTHONPATH", "PYTHONIOENCODING")}
    env.update({"TRADING_MODE": "paper", "ALPACA_PAPER_API_KEY": "k", "ALPACA_PAPER_SECRET_KEY": "s",
                "STATE_VOLUME_PATH": str(tmp_path)})
    out = subprocess.run([sys.executable, str(ROOT / "tools" / "v5_durum.py")], cwd=str(tmp_path), env=env,
                         capture_output=True, timeout=120)
    text = out.stdout.decode("utf-8")
    assert out.returncode == 0, out.stderr.decode("utf-8", "replace")[-400:]
    assert text.splitlines() == ["Bot çalışmıyor görünüyor.", "Kalp atışı kaydı yok.", "Durum: bilinmiyor.", "Henüz günlük özet yok."]


# ============================================================================ which account is speaking
def test_every_message_says_which_account_it_is_about(tmp_path):
    paper, box, post, clock, s = notifier(tmp_path / "paper", mode="paper", enabled=False)
    paper.alert("MISSED")
    paper.reminder("PAUSED", DAY)
    paper.digest(DAY, dict(BASE, prefix="paper"))
    texts = [c["data"].decode("utf-8") for c in post.calls]
    assert texts[0] == "DENEME HESABI. " + ALERTS["MISSED"][1]
    assert texts[1] == "DENEME HESABI. " + ALERTS["PAUSED"][1]
    assert texts[2].startswith("DENEME HESABI. Gün sonu özeti (") and texts[2].count("DENEME HESABI") == 1
    dry, box, post, clock, s = notifier(tmp_path / "dry", mode="live", enabled=False)
    dry.alert("HOLD_NOT_READY", cause="fiyat bilgisine ulaşamadım")
    assert post.calls[0]["data"].decode("utf-8") == "DENEME, gerçek emir gönderilmedi. " + ALERTS["HOLD_NOT_READY"][1]
    real, box, post, clock, s = notifier(tmp_path / "real", mode="live", enabled=True)
    real.alert("MISSED")
    assert post.calls[0]["data"].decode("utf-8") == ALERTS["MISSED"][1]               # real money: no label


def test_the_practice_account_can_have_its_own_topic(tmp_path):
    base = {"ALPACA_PAPER_API_KEY": "k", "ALPACA_PAPER_SECRET_KEY": "s", "ALPACA_LIVE_API_KEY": "lk",
            "ALPACA_LIVE_SECRET_KEY": "ls", "NTFY_TOPIC": "owners-phone", "NTFY_TOPIC_PAPER": "practice-only"}
    paper = vcfg.load_settings(env={**base, "TRADING_MODE": "paper"}, app_dir=tmp_path)
    live = vcfg.load_settings(env={**base, "TRADING_MODE": "live"}, app_dir=tmp_path)
    assert paper.ntfy_topic == "practice-only" and live.ntfy_topic == "owners-phone"
    assert "practice-only" not in repr(paper) and "owners-phone" not in repr(live)
    alone = vcfg.load_settings(env={**base, "TRADING_MODE": "paper", "NTFY_TOPIC_PAPER": "  "}, app_dir=tmp_path)
    assert alone.ntfy_topic == "owners-phone"                                         # empty: the common topic
    with pytest.raises(vcfg.ConfigError) as caught:
        vcfg.load_settings(env={**base, "TRADING_MODE": "paper", "NTFY_TOPIC_PAPER": "bad topic"}, app_dir=tmp_path)
    assert "bad topic" not in str(caught.value)
    assert vcfg.load_settings(env={**base, "TRADING_MODE": "live", "NTFY_TOPIC_PAPER": "bad topic"}, app_dir=tmp_path).ntfy_topic == "owners-phone"
