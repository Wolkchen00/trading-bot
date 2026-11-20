"""Tests for the independent review of R5 (2026-11-19): at least one per finding.

The scenarios are the reviewer's, rebuilt on this suite's own fake market (the reviewer's own scripts live outside
the repository and were run before and after the fix as well). Every test here fails on the code of commit
0155d30. The questions are still the same three: is the owner ever left without a message, is an order ever
sent on a state that was not verified in that very tick, does a message ever claim what did not happen.
"""
import datetime as dt
import json
import os
import shutil
import subprocess
from decimal import Decimal

import pytest

from tests_v5.fakebroker import reject
from tests_v5.test_r5_saldirgan import (
    D1, D2, D3, ERROR_TEXT, PRODUCT, ROOT, UNRESOLVED_TEXT, UTC, Sim, hold_text, load_tool, window,
)
from v5 import broker as vb
from v5 import config as vcfg
from v5 import executor as ex
from v5 import health as vh
from v5 import notify as vn
from v5 import ops as vo
from v5 import rails as vr
from v5 import runner as vrun

FRIDAY = dt.date(2026, 11, 13)
SECONDS = dt.timedelta(seconds=1)
MINUTES = dt.timedelta(minutes=1)
PENDING = "Satışa başladım ama henüz bitmedi."
SOLD_ALL = "Her şeyi sattım ve durdum."


class Death(BaseException):
    """The process dies here: not an Exception, so nothing in the engine may swallow it."""


def not_sent(cause):
    return ("Bugün bir işlem yapmam gerekiyordu ama emri gönderemedim, çünkü %s. Elimdekilere dokunmadım. "
            "Yarın yeniden karar veririm." % cause)


def break_call(sim, name, after_calls=0, until=None):
    """Make one broker call fail, after its first `after_calls` uses and until the time `until`. Returns the undo."""
    original = getattr(sim.fb, name)
    seen = {"n": 0}

    def wrapped(*args, **kwargs):
        seen["n"] += 1
        if seen["n"] > after_calls and (until is None or sim.clock.now < until):
            raise vb.BrokerUnavailable(name + " is down")
        return original(*args, **kwargs)
    setattr(sim.fb, name, wrapped)
    return lambda: setattr(sim.fb, name, original)


def live_record(sim, day, purpose="dec"):
    return ex.SessionRecord.load(sim.settings.state_dir, day, purpose=purpose, dry_run=False)


def rows(sim):
    return {row["session"]: row["equity"] for row in sim.rails().state()["equity_history"]}


# ============================================================================ 1. decided but never sent
def test_a_decided_trade_that_was_never_sent_is_told_and_never_claimed(tmp_path):
    sim = Sim(tmp_path)
    w = window(sim, D1)
    # The engine's own look at the account works; the executor's look right after it does not, until too late.
    break_call(sim, "get_open_orders", after_calls=1, until=w.submit_until + 30 * SECONDS)
    sim.run_day(D1)
    rec = sim.record(D1)
    assert rec.status == ex.NOT_SUBMITTED and rec.is_final and sim.submits() == []
    assert rec.data["decision"]["trade"] is True                                      # there WAS something to do
    assert sim.alerts() == [not_sent("Alpaca'ya bağlanamadım")]                       # told at once, and once
    lines = sim.digests()[0].split("\n")
    assert lines[2] == "Elimde: yüzde 0 Nasdaq fonu, yüzde 100 nakit."
    assert lines[3] == "Bugün ne yaptım: İşlem yapmadım."
    assert lines[4] == "Neden: Karar verdim ama emri gönderemedim."                   # never the decision's reasons
    assert lines[6] == "Durum: " + not_sent("Alpaca'ya bağlanamadım")


def test_a_decision_that_dies_before_its_order_is_told_as_not_sent(tmp_path):
    sim = Sim(tmp_path)
    w = window(sim, D1)
    sim.ibs[D1] = 0.0                                                                 # the extra buy is wanted too
    runner = sim.run_day(D1, stop=w.decide_from - 20 * SECONDS)

    def die(*args, **kwargs):
        raise Death()
    runner.executor.run_chain = die                                                   # dies between deciding and sending
    sim.clock.now = w.decide_from
    with pytest.raises(Death):
        runner.tick()
    rec = sim.record(D1)
    assert rec.status == ex.NEW and rec.data["target"] is not None and rec.data["intents"] == []
    assert "OVERLAY_ENTER" in rec.data["decision"]["reason_keys"]
    sim.run_day(D1, start=w.submit_until + 20 * SECONDS)                              # a new process, too late to send
    rec = sim.record(D1)
    assert rec.status == ex.NOT_SUBMITTED and sim.submits() == []
    assert sim.alerts() == [not_sent("süre yetmedi")]
    lines = sim.digests()[0].split("\n")
    assert "ek alım yaptım" not in sim.digests()[0]                                   # nothing was bought
    assert lines[3] == "Bugün ne yaptım: İşlem yapmadım." and lines[4] == "Neden: Karar verdim ama emri gönderemedim."
    assert lines[6] == "Durum: " + not_sent("süre yetmedi")


def test_a_refused_order_is_not_explained_with_the_reasons_of_the_decision(tmp_path):
    sim = Sim(tmp_path)
    sim.fb.script(reject(422, 42210000, "refused"))
    sim.run_day(D1)
    assert sim.record(D1).status == ex.FAILED
    lines = sim.digests()[0].split("\n")
    assert lines[3] == "Bugün ne yaptım: İşlem yapmadım."
    assert lines[4] == "Neden: Karar verdim ama Alpaca emrimi kabul etmedi."
    assert lines[6].startswith("Durum: Alpaca emrimi kabul etmedi.")


# ============================================================================ 2. every tick gates the chain again
def first_step_without_an_order(sim):
    """The first step of the day's chain ends with no order (one failed look at the open orders)."""
    return break_call(sim, "get_open_orders", after_calls=1)


@pytest.mark.parametrize("gate,cause", [
    ("skew", "saat bilgisi tutarsız göründü"),
    ("closed", "bugünün borsa saatlerini öğrenemedim"),
    ("clock_down", "saat bilgisi tutarsız göründü"),
    ("paused", "bot duraklatılmış durumda"),
    ("restart_skew", "saat bilgisi tutarsız göründü"),
    ("restart_paused", "bot duraklatılmış durumda"),
])
def test_a_chain_under_way_sends_nothing_once_a_gate_closes(tmp_path, gate, cause):
    sim = Sim(tmp_path)
    w = window(sim, D1)
    undo = first_step_without_an_order(sim)

    def hook(t, runner):
        if t != w.decide_from + 20 * SECONDS:
            return None
        undo()                                                                        # the broker answers again ...
        rec = sim.record(D1)
        assert rec.status == ex.NEW and rec.data["target"] is not None and sim.submits() == []
        if gate.endswith("skew"):                                                     # ... but something else is wrong
            sim.fb.skew = 95.0
        elif gate == "closed":
            sim.fb.force_closed = True
        elif gate == "clock_down":
            break_call(sim, "get_clock")
        else:
            sim.rails().pause("owner", "bekle")
        return sim.runner() if gate.startswith("restart") else None                   # also on a new process' first tick
    sim.run_day(D1, hook=hook)
    rec = sim.record(D1)
    assert sim.submits() == [] and sim.orders() == []
    assert rec.status == ex.NOT_SUBMITTED and rec.is_final
    assert sim.alerts() == [not_sent(cause)]
    assert sim.digests()[0].split("\n")[3] == "Bugün ne yaptım: İşlem yapmadım."


def test_the_same_chain_goes_on_when_no_gate_closes(tmp_path):
    sim = Sim(tmp_path)
    w = window(sim, D1)
    undo = first_step_without_an_order(sim)

    def hook(t, runner):
        if t == w.decide_from + 20 * SECONDS:
            undo()
    sim.run_day(D1, hook=hook)
    assert sim.record(D1).status == ex.DONE and len(sim.orders()) == 1 and sim.alerts() == []
    assert sim.orders()[0].submitted_at == w.decide_from + 20 * SECONDS


def test_a_blocked_chain_still_follows_the_order_it_already_sent(tmp_path):
    sim = Sim(tmp_path)
    sim.fb.script("never_fill")
    w = window(sim, D1)

    def hook(t, runner):
        if t == w.decide_from + 20 * SECONDS:
            assert sim.record(D1).status == ex.SUBMITTED
            sim.fb.skew = 95.0
    sim.run_day(D1, hook=hook)
    rec = sim.record(D1)
    assert len(sim.submits()) == 1 and rec.is_final                                   # no second order ...
    assert sim.orders()[0].status == "canceled"                                       # ... and the first one was cancelled
    assert [c for c in sim.fb.calls if c[0] == "cancel_order"]
    assert rec.data["intents"][-1]["order_status"] == "canceled"                      # and booked


def test_a_breaker_that_trips_while_a_buy_is_under_way_stops_it(tmp_path):
    sim = Sim(tmp_path, cash="245", qty="4.9", price="50", peak=490.0)                # half in the fund
    sim.ibs[D1] = 0.0                                                                 # the rule adds the extra buy
    w = window(sim, D1)
    undo = first_step_without_an_order(sim)

    def hook(t, runner):
        if t == w.decide_from + 20 * SECONDS:
            undo()
            rec = sim.record(D1)
            assert rec.data["target"]["total_new"] > 0.5                              # a net buy was decided
            sim.fb.set_price(PRODUCT, "34")                                           # the account is down 16%
    sim.run_day(D1, hook=hook)
    assert sim.submits() == [] and sim.record(D1).status == ex.NOT_SUBMITTED
    assert any(t.startswith("Hesap bugün yüzde 16,0 düştü.") for t in sim.alerts())
    assert not_sent("bugün sert düşüş olduğu için yeni alım yapmıyorum") in sim.alerts()


@pytest.mark.parametrize("gate", ["skew", "closed", "cleared"])
def test_a_safety_sale_under_way_is_gated_again_too(tmp_path, gate):
    sim = Sim(tmp_path, cash="250", qty="5", price="50", peak=1300.0)                 # 61.5% below the peak
    w = window(sim, D1)
    halt_tick = w.decide_from + 60 * SECONDS                                          # the second reading, 60 s later
    undo = first_step_without_an_order(sim)

    def hook(t, runner):
        if t != halt_tick + 20 * SECONDS:
            return None
        undo()
        flr = sim.record(D1, "flr")
        assert flr is not None and flr.status == ex.NEW and flr.data["target"] is not None and sim.submits() == []
        if gate == "skew":
            sim.fb.skew = 95.0
        elif gate == "closed":
            sim.fb.force_closed = True
        else:
            sim.rails().clear_halt("owner", "devam", 500.0)                           # the owner lifts the halt
        return None
    sim.run_day(D1, hook=hook)
    assert sim.submits() == [] and sim.fb.positions[PRODUCT] == Decimal("5")
    assert sim.record(D1, "flr").status == ex.NOT_SUBMITTED
    pending = [t for t in sim.alerts() if PENDING in t]
    if gate == "cleared":
        assert pending == [] and sim.rails().floor_state() == "OK"                    # no "still selling" after the go
    else:
        assert len(pending) == 1 and sim.rails().liquidating() is True


# ============================================================================ 3. a floor confirmation expires
def reading_at(moment, equity):
    return vr.EquityReading(moment, equity, equity, equity, 0.0, True, None)


def test_two_floor_readings_must_belong_to_the_same_window(tmp_path):
    sim = Sim(tmp_path, peak=1300.0)
    rails = sim.rails()
    t0 = dt.datetime(2026, 11, 16, 20, 56, tzinfo=UTC)
    assert rails.check_floor(reading_at(t0, 500.0)) == "CONFIRMING"
    assert rails.check_floor(reading_at(t0 + 59 * SECONDS, 500.0)) == "CONFIRMING"
    t1 = t0 + dt.timedelta(days=1)
    assert rails.check_floor(reading_at(t1, 500.0)) == "CONFIRMING"                   # a NEW first reading, never a halt
    assert rails.state()["floor_confirmation"]["time"] == t1.isoformat() and rails.liquidating() is False
    assert rails.check_floor(reading_at(t1 + 20 * SECONDS, 900.0)) == "OK"
    assert rails.check_floor(reading_at(t1 + 40 * SECONDS, 500.0)) == "CONFIRMING"
    assert rails.check_floor(reading_at(t1 + 100 * SECONDS, 500.0)) == "HALT"         # two readings, 60 s apart
    other = Sim(tmp_path / "edge", peak=1300.0).rails()
    assert other.check_floor(reading_at(t0, 500.0)) == "CONFIRMING"
    assert other.check_floor(reading_at(t0 + 600 * SECONDS, 500.0)) == "HALT"         # ten minutes still count ...
    late = Sim(tmp_path / "late", peak=1300.0).rails()
    assert late.check_floor(reading_at(t0, 500.0)) == "CONFIRMING"
    assert late.check_floor(reading_at(t0 + 601 * SECONDS, 500.0)) == "CONFIRMING"    # ... a second more does not


def test_one_bad_reading_a_day_later_never_sells(tmp_path):
    sim = Sim(tmp_path, cash="330", qty="3.4", price="50", peak=900.0)                # 500: well above the floor (360)
    w1, w2 = window(sim, D1), window(sim, D2)

    def day_one(t, runner):
        if t == w1.decide_from - 20 * SECONDS:
            sim.fb.down = True                                                        # nothing can be read ...
        elif t == w1.decide_until:
            sim.fb.down = False
            sim.fb.set_price(PRODUCT, "0.01")                                         # ... then one absurd print, last look
        elif t == w1.decide_until + 20 * SECONDS:
            sim.fb.set_price(PRODUCT, "50")
    runner = sim.run_day(D1, hook=day_one)
    assert sim.rails().floor_state() == "CONFIRMING" and sim.submits() == []          # the day ended on one reading
    assert sim.record(D1).status == ex.HOLD_NOT_READY

    def day_two(t, runner):
        if t == w2.decide_from:
            sim.fb.set_price(PRODUCT, "0.01")                                         # one absurd print again
        elif t == w2.decide_from + 20 * SECONDS:
            sim.fb.set_price(PRODUCT, "50")
    sim.run_day(D2, runner, hook=day_two)
    assert sim.rails().floor_state() == "OK" and sim.fb.positions[PRODUCT] == Decimal("3.4")
    assert sim.record(D2, "flr") is None and not [o for o in sim.orders() if "-flr-" in o.client_order_id]
    assert not [t for t in sim.alerts() if SOLD_ALL in t or PENDING in t]
    assert sim.record(D2).is_final and sim.record(D2).status in (ex.NO_TRADE, ex.DONE)


# ============================================================================ 4. no order record is left behind
def order_that_cannot_be_looked_up(sim, until=None):
    """The broker takes the order (and fills it) but the answer is lost, and it cannot be found by id either."""
    sim.fb.script("timeout_after_accept")
    return break_call(sim, "get_order_by_client_id", until=until), break_call(sim, "get_order", until=until)


def test_an_order_with_an_unknown_outcome_is_called_unknown_and_settled_later(tmp_path):
    sim = Sim(tmp_path)
    close = sim.session(D1).close
    order_that_cannot_be_looked_up(sim, until=close + dt.timedelta(hours=2))
    runner = sim.run_day(D1)
    rec = sim.record(D1)
    assert not rec.is_final and len(sim.orders()) == 1 and sim.orders()[0].status == "filled"
    lines = sim.digests()[0].split("\n")
    assert lines[2] == "Elimde: yüzde %s Nasdaq fonu, yüzde %d nakit." % (
        vn.fmt_weight(sim.weight()), 100 - int(vn.fmt_weight(sim.weight())))          # what the account really holds
    assert lines[3] == "Bugün ne yaptım: Bir emir gönderdim ama sonucunu henüz öğrenemedim."
    assert lines[4] == "Neden: Emrimin sonucunu henüz öğrenemedim."
    assert lines[6] == "Durum: " + UNRESOLVED_TEXT
    assert "UNRESOLVED" in sim.heartbeat()["states"] and vh.assess(sim.heartbeat(), sim.clock.now)[0] == 2
    assert not (sim.settings.state_dir / "overlay.json").exists()
    sim.clock.now = close + dt.timedelta(hours=2, minutes=1)                          # the broker answers again
    runner.tick()
    rec = sim.record(D1)
    assert rec.is_final and rec.status in (ex.DONE, ex.CAPPED) and len(sim.submits()) == 1
    assert (sim.settings.state_dir / "overlay.json").exists()                         # the books are closed
    assert "UNRESOLVED" not in sim.heartbeat()["states"] and len(sim.digests()) == 1
    sim.run_day(D2, runner)
    assert sim.record(D2).is_final and sim.digests()[-1].split("\n")[6] == "Durum: Her şey normal."


def test_an_order_left_open_by_a_dead_process_is_settled_and_reported_the_next_day(tmp_path):
    sim = Sim(tmp_path)
    w = window(sim, D1)
    sim.ibs[D1] = 0.0                                                                 # the one-day extra buy
    undo = order_that_cannot_be_looked_up(sim)
    sim.run_day(D1, stop=w.decide_from + 2 * MINUTES)                                 # the process dies with the order out
    for restore in undo:
        restore()
    stuck = sim.record(D1)
    assert not stuck.is_final and sim.digests() == [] and len(sim.orders()) == 1
    held = sim.weight()
    assert held > 0.6                                                                 # core and the extra buy
    close_value = float(sim.fb.equity)
    runner = sim.run_day(D2, start=sim.session(D2).open + 30 * MINUTES)               # back after the next open
    first = sim.record(D1)
    assert first.is_final and first.status in (ex.DONE, ex.CAPPED)
    assert sim.settings.state_dir.joinpath("overlay.json").exists()
    assert len(sim.digests()) == 2
    day_one = sim.digests()[0].split("\n")
    assert day_one[0] == "DENEME HESABI. Gün sonu özeti (16.11.2026)"
    assert day_one[3].startswith("Bugün ne yaptım: ") and day_one[3].endswith("dolarlık Nasdaq fonu aldım.")
    assert rows(sim)[D1.isoformat()] == pytest.approx(close_value)                    # the day has its closing value
    second = sim.record(D2)
    assert second.status == ex.DONE and sim.orders()[-1].side == "sell"               # the extra buy expired on time
    assert sim.weight() < 0.45
    assert "UNRESOLVED" not in sim.heartbeat()["states"]


def test_an_open_order_of_yesterday_blocks_today_until_it_is_settled(tmp_path):
    sim = Sim(tmp_path)
    undo = order_that_cannot_be_looked_up(sim)                                        # and it stays that way
    runner = sim.run_day(D1)
    assert not sim.record(D1).is_final
    before = len(sim.submits())
    sim.ibs[D2] = 0.0                                                                 # the rule would love to buy
    runner = sim.run_day(D2, runner)
    assert len(sim.submits()) == before and sim.record(D2).status == ex.HOLD_NOT_READY
    assert UNRESOLVED_TEXT in sim.alerts() and sim.digests()[-1].split("\n")[6] == "Durum: " + UNRESOLVED_TEXT
    for restore in undo:
        restore()
    runner = sim.run_day(D3, runner)
    assert sim.record(D1).is_final and sim.record(D3).is_final
    assert sim.record(D3).status in (ex.DONE, ex.NO_TRADE) and "UNRESOLVED" not in sim.heartbeat()["states"]


# ============================================================================ 5. the closing value is a closing value
def test_a_report_made_the_next_morning_never_stores_the_overnight_move(tmp_path):
    sim = Sim(tmp_path)
    w = window(sim, D1)
    sim.run_day(D1, stop=w.decide_from + 2 * MINUTES)                                 # trades, then the process dies
    assert sim.record(D1).status == ex.DONE and sim.digests() == []
    close_value = float(sim.fb.equity)
    sim.fb.set_price(PRODUCT, "60")                                                   # the fund opens 20% higher
    morning_value = float(sim.fb.equity)
    assert morning_value > close_value * 1.05
    runner = sim.runner()
    for minutes in (60, 59):
        sim.clock.now = sim.session(D2).open - minutes * MINUTES                      # 08:30 on the next day
        runner.tick()
    assert len(sim.digests()) == 1
    lines = sim.digests()[0].split("\n")
    assert lines[0] == "DENEME HESABI. Gün sonu özeti (16.11.2026)"
    assert lines[1] == "Hesap şu an %s dolar." % vn.fmt_money(morning_value)        # the value belongs to now, not D1
    assert D1.isoformat() not in rows(sim) and sim.rails().peak() == 490.0            # nothing stored, peak untouched

    def broker_baseline(t, r):
        sim.fb.last_equity = Decimal(str(close_value))                                # the broker's own close of D1
    sim.run_day(D2, runner, hook=broker_baseline)
    assert rows(sim)[D1.isoformat()] == pytest.approx(close_value)                    # the true close, from the broker
    assert not [t for t in sim.alerts() if "tutarsız" in t]                           # and no false "data doubt"
    second = sim.digests()[1].split("\n")
    assert second[1].startswith("Hesap: %s dolar. Bugün yüzde " % vn.fmt_money(morning_value)) and "arttı" in second[1]


def test_a_missing_closing_row_is_not_a_data_doubt_the_next_day(tmp_path):
    sim = Sim(tmp_path)
    runner = sim.run_day(D1)
    w2 = window(sim, D2)

    def day_two(t, r):
        if t == sim.session(D2).open + 2 * dt.timedelta(hours=1):
            sim.fb.set_price(PRODUCT, "56")                                           # the account gains about 4%
        elif t == sim.session(D2).close + 10 * SECONDS:
            sim.fb.down = True                                                        # no reading after this close
    runner = sim.run_day(D2, runner, hook=day_two)
    assert D2.isoformat() not in rows(sim) and len(sim.digests()) == 1
    close_two = None

    def day_three(t, r):
        nonlocal close_two
        if t == sim.session(D3).open + 30 * MINUTES:
            sim.fb.down = False
            close_two = float(sim.fb.equity)
            sim.fb.last_equity = sim.fb.equity
    sim.ibs[D3] = 0.0                                                                 # the rule wants the extra buy
    sim.run_day(D3, runner, hook=day_three)
    assert not [t for t in sim.alerts() if "tutarsız" in t]                           # no "Hesap değeri tutarsız göründü"
    assert sim.rails().breaker_info() is None
    assert rows(sim)[D2.isoformat()] == pytest.approx(close_two)
    third = sim.record(D3)
    assert third.status == ex.DONE and "OVERLAY_ENTER" in third.data["decision"]["reason_keys"]
    assert sim.orders()[-1].side == "buy"                                             # the extra buy was not suppressed
    assert sim.digests()[-1].split("\n")[1].endswith("Bugün değişmedi.")              # and a flat day reads flat


# ============================================================================ 6. and 7. the floor's messages
def test_a_fund_that_comes_back_during_a_halt_is_sold_and_the_owner_hears_it(tmp_path):
    sim = Sim(tmp_path, cash="250", qty="5", price="50", peak=1300.0)
    runner = sim.run_day(D1)
    assert sim.rails().floor_state() == "HALT" and PRODUCT not in sim.fb.positions
    assert len([t for t in sim.alerts() if SOLD_ALL in t]) == 1 and sim.rails().liquidating() is False
    sim.fb.positions[PRODUCT] = Decimal("2")                                          # the owner buys by hand,
    sim.fb.cash -= Decimal("100")                                                     # without lifting the halt
    sim.run_day(D2, runner)
    assert PRODUCT not in sim.fb.positions and sim.orders()[-1].client_order_id == "v5-paper-20261117-flr-1"
    assert len([t for t in sim.alerts() if SOLD_ALL in t]) == 2                       # the second sale is told as well
    assert sim.rails().liquidating() is False


def test_a_dry_run_says_when_the_safety_limit_is_broken(tmp_path):
    sim = Sim(tmp_path, mode="live", cash="250", qty="5", price="50", peak=1300.0)    # real account, flag off
    assert sim.dry is True
    sim.run_day(D1)
    assert sim.submits() == [] and sim.fb.positions[PRODUCT] == Decimal("5")
    pending = [t for t in sim.alerts() if PENDING in t]                               # every alert carries the dry label
    assert len(pending) == 1 and pending[0].startswith("Hesap en yüksek değerinden yüzde 61,5 düştü, şu an 500,00 dolar.")
    assert sim.digests()[0].split("\n")[6].startswith("Durum: Güvenlik satışı hala bitmedi.")


def test_a_pending_safety_sale_with_the_broker_down_is_not_silent(tmp_path):
    sim = Sim(tmp_path, cash="250", qty="5", price="50", peak=1300.0)
    sim.fb.skew = 95.0                                                                # day one: the sale has to wait
    runner = sim.run_day(D1)
    assert sim.rails().floor_state() == "HALT" and sim.rails().liquidating() is True and sim.submits() == []
    sim.fb.skew = 0.0
    w = window(sim, D2)
    before = len(sim.alerts())

    def outage(t, r):
        if t == w.decide_from - 20 * SECONDS:
            sim.fb.down = True
        elif t == sim.session(D2).close + 10 * SECONDS:
            sim.fb.down = False
    sim.run_day(D2, runner, hook=outage)
    assert sim.submits() == [] and sim.fb.positions[PRODUCT] == Decimal("5")
    assert hold_text("Alpaca'ya bağlanamadım") in sim.alerts()[before:]               # the owner is told why
    assert sim.digests()[-1].split("\n")[6].startswith("Durum: Güvenlik satışı hala bitmedi.")


def test_a_sale_that_finished_unseen_is_reported_as_finished(tmp_path):
    sim = Sim(tmp_path, cash="250", qty="5", price="50", peak=800.0)                  # 37.5% down, and falling
    w = window(sim, D1)
    runner = sim.runner()

    def die(*args, **kwargs):
        raise Death()

    def hook(t, r):
        if t == w.decide_from - 20 * SECONDS:
            sim.fb.set_price(PRODUCT, "10")                                           # 300: 62.5% below the peak
        elif t == w.decide_from + 60 * SECONDS:
            r._floor_report = die                                                     # dies right after the sale filled
    with pytest.raises(Death):
        sim.run_day(D1, runner, hook=hook)
    assert PRODUCT not in sim.fb.positions and sim.rails().liquidating() is True
    assert not [t for t in sim.alerts() if SOLD_ALL in t]
    sim.run_day(D1, start=sim.session(D1).close + 10 * SECONDS)                       # back after the close
    done = [t for t in sim.alerts() if SOLD_ALL in t]
    assert len(done) == 1 and sim.rails().liquidating() is False                      # told the same evening
    assert sim.digests()[0].split("\n")[6] == "Durum: " + done[0]                     # not "the sale is not finished"
    assert not [t for t in sim.alerts() if "Kurala göre çalışmaya devam ediyorum" in t]


def test_one_pending_message_a_day_whatever_the_reason(tmp_path):
    sim = Sim(tmp_path, cash="250", qty="5", price="50", peak=1300.0)
    sim.fb.skew = 95.0
    sim.fb.script("never_fill")
    w = window(sim, D1)

    def hook(t, r):
        if t == w.decide_from + 2 * MINUTES:
            sim.fb.skew = 0.0                                                         # now the order goes out, unfilled
    sim.run_day(D1, hook=hook)
    assert len(sim.submits()) == 1 and sim.fb.positions[PRODUCT] == Decimal("5")
    assert len([t for t in sim.alerts() if PENDING in t]) == 1


# ============================================================================ 8. decide-now and replay
def test_decide_now_names_everything_that_would_stop_the_engine(tmp_path):
    def run(sim):
        sim.clock.now = window(sim, D1).decide_from - dt.timedelta(hours=2)
        return vo.cmd_decide_now(sim.settings, sim.fb, sim.clock.now)

    code, doc = run(Sim(tmp_path / "clean"))
    assert code == 0 and doc["blockers"] == [] and doc["breaker"] is False and doc["in_window"] is False
    assert doc["plan"]["side"] == "buy"
    cases = {
        "PEAK_MISSING": Sim(tmp_path / "peak", peak=None),
        "NOTIFY_MISSING": Sim(tmp_path / "topic", topic=""),
        "PAUSED": Sim(tmp_path / "paused"),
        "FLOOR_HALT": Sim(tmp_path / "halt", cash="250", qty="5", price="50", peak=1300.0),
        "RAIL_CONFIRMING": Sim(tmp_path / "breach", cash="250", qty="5", price="50", peak=1300.0),
        "FOREIGN_ASSET": Sim(tmp_path / "foreign"),
        "CLOCK_DOUBT": Sim(tmp_path / "skew"),
        "MARKET_CLOSED": Sim(tmp_path / "closed"),
        "UNRESOLVED": Sim(tmp_path / "open"),
    }
    cases["PAUSED"].rails().pause("owner", "bekle")
    halt = cases["FLOOR_HALT"]
    t0 = dt.datetime(2026, 11, 13, 20, 55, tzinfo=UTC)
    halt.rails().check_floor(reading_at(t0, 500.0))
    assert halt.rails().check_floor(reading_at(t0 + 61 * SECONDS, 500.0)) == "HALT"
    cases["FOREIGN_ASSET"].fb.positions["SPY"] = Decimal("0.5")
    cases["FOREIGN_ASSET"].fb.prices["SPY"] = Decimal("600")
    cases["CLOCK_DOUBT"].fb.skew = 95.0
    cases["MARKET_CLOSED"].fb.force_closed = True
    stuck = ex.SessionRecord.new(cases["UNRESOLVED"].settings.state_dir, FRIDAY, "paper")
    stuck.data.update(status=ex.SUBMITTED, target={"core_new": 0.3, "ovl_new": 0.0, "total_new": 0.3})
    stuck.save()
    for name, sim in cases.items():
        before = sorted((p.name, p.read_bytes()) for p in sim.settings.state_dir.rglob("*") if p.is_file())
        code, doc = run(sim)
        assert code == 1 and name in doc["blockers"], (name, doc["blockers"])
        assert not [c for c in sim.fb.calls if c[0] in ("submit_order", "cancel_order")]
        after = sorted((p.name, p.read_bytes()) for p in sim.settings.state_dir.rglob("*") if p.is_file())
        assert after == before, name                                                  # still strictly read-only


def test_decide_now_applies_the_breaker_and_fails_when_the_rule_is_not_ready(tmp_path):
    sim = Sim(tmp_path / "breaker")
    sim.ibs[D1] = 0.0
    sim.clock.now = window(sim, D1).decide_from - dt.timedelta(hours=2)
    sim.fb.last_equity = sim.fb.equity / Decimal("0.8")                               # down 20% on the day
    code, doc = vo.cmd_decide_now(sim.settings, sim.fb, sim.clock.now)
    assert doc["breaker"] is True and doc["decision"]["trade"] is False and doc["plan"] is None
    assert "OVERLAY_ENTER" not in doc["decision"]["reason_keys"]
    assert "BREAKER_REDUCE_ONLY" in doc["decision"]["reason_keys"]
    blind = Sim(tmp_path / "blind")
    blind.fb.no_price = True                                                          # the inputs cannot be built
    blind.clock.now = window(blind, D1).decide_from - dt.timedelta(hours=2)
    code, doc = vo.cmd_decide_now(blind.settings, blind.fb, blind.clock.now)
    assert code == 1 and doc["decision"] is None


def test_replay_checks_the_order_plan_and_is_strict_about_types(tmp_path):
    sim = Sim(tmp_path)
    sim.clock.now = window(sim, D1).decide_from - dt.timedelta(hours=2)
    code, stored = vo.cmd_decide_now(sim.settings, sim.fb, sim.clock.now)
    assert code == 0 and vo.cmd_replay(stored)[0] == 0
    for section, key, value in [("plan", "side", "sell"), ("plan", "notional", "1.00"), ("plan", "capped", True),
                                ("snapshot", "equity", 1000.0), ("snapshot", "spendable", 5.0),
                                ("decision", "trade", 1), ("decision", "core_new", 0.99)]:
        tampered = json.loads(json.dumps(stored))
        assert tampered[section][key] != value or type(tampered[section][key]) is not type(value)
        tampered[section][key] = value
        assert vo.cmd_replay(tampered)[0] == 1, (section, key)
    broken = json.loads(json.dumps(stored))
    broken["snapshot"]["equity"] = "a lot"
    assert vo.cmd_replay(broken) == (1, None)


# ============================================================================ 9. the drill and the test message
def test_the_drill_never_sells_a_position_it_did_not_buy(tmp_path):
    drill = load_tool("v5_tatbikat")
    sim = Sim(tmp_path, cash="9750", qty="5", price="50")
    sim.clock.now = sim.session(D1).open + dt.timedelta(hours=1)
    result = drill.run_steps(sim.settings, sim.fb, ["round_trip", "duplicate_id", "restart"],
                             sim.clock, sim.clock.sleep, sim.post)
    assert [result[step][0] for step in ("round_trip", "duplicate_id", "restart")] == [False, False, False]
    assert all("flatten it first" in result[step][1] for step in result)
    assert sim.fb.positions[PRODUCT] == Decimal("5") and sim.submits() == []


def test_the_drill_closes_short_positions_and_counts_them(tmp_path):
    drill = load_tool("v5_tatbikat")
    sim = Sim(tmp_path / "short", cash="10000")
    sim.fb.positions["SPY"] = Decimal("-2")
    sim.fb.prices["SPY"] = Decimal("600")
    sim.clock.now = sim.session(D1).open + dt.timedelta(hours=1)
    result = drill.run_steps(sim.settings, sim.fb, ["flatten_foreign"], sim.clock, sim.clock.sleep, sim.post, confirm=True)
    assert result["flatten_foreign"][0] is True, result
    assert sim.fb.positions.get("SPY", Decimal(0)) == 0
    assert [(c[1]["symbol"], c[1]["side"], c[1]["qty"]) for c in sim.submits()] == [("SPY", "buy", "2")]
    left = Sim(tmp_path / "left", cash="10000")
    left.fb.positions["SPY"] = Decimal("-2")
    left.fb.prices["SPY"] = Decimal("600")
    left.fb.script("never_fill")                                                      # the closing order does not fill
    left.clock.now = left.session(D1).open + dt.timedelta(hours=1)
    result = drill.run_steps(left.settings, left.fb, ["flatten_foreign"], left.clock, left.clock.sleep, left.post, confirm=True)
    assert result["flatten_foreign"][0] is False and "still held: SPY" in result["flatten_foreign"][1]


def test_the_test_message_goes_to_the_process_own_topic(tmp_path):
    sim = Sim(tmp_path / "real", mode="live")                                         # the real-money process, flag off
    posts = []

    def post(url, data, headers, timeout):
        posts.append((url, data.decode("utf-8")))
        return 200
    assert "notify-test" in vo.COMMANDS
    assert vo.cmd_notify_test(sim.settings, post, sim.clock) == 0
    assert posts == [("https://ntfy.sh/sim-topic",
                      "DENEME, gerçek emir gönderilmedi. Deneme bildirimi: bu mesajı görüyorsan bildirimler çalışıyor.")]
    assert not (sim.settings.state_dir / "outbox.jsonl").exists()                     # the bot's outbox is not touched
    assert vo.cmd_notify_test(sim.settings, lambda *a: 500, sim.clock) == 1           # refused: said, not hidden
    quiet = Sim(tmp_path / "quiet", topic="")
    assert vo.cmd_notify_test(quiet.settings, post, quiet.clock) == 1 and len(posts) == 1


# ============================================================================ 10. the watchdog
def watchdog(tmp_path):
    """A copy of the script with its three host paths redirected, a fake docker and a fake curl."""
    sh = shutil.which("sh")
    if sh is None:
        pytest.skip("no POSIX shell on this machine")
    work = tmp_path / "wd"
    for name in ("bin", "var", "fake"):
        (work / name).mkdir(parents=True)
    text = (ROOT / "ops" / "vps" / "v5_watchdog.sh").read_text(encoding="utf-8")
    for old, new in (("/root/.v5_watchdog_topic", (work / "topic").as_posix()),
                     ("/var/tmp/v5_watchdog_", (work / "var").as_posix() + "/stamp_"),
                     ("/var/log/v5_watchdog.log", (work / "var" / "log.txt").as_posix())):
        assert old in text
        text = text.replace(old, new)
    (work / "watchdog.sh").write_bytes(text.encode("utf-8"))
    fake_docker = ('#!/bin/sh\n'
                   'if [ "$1" = "ps" ]; then cat "$FAKE_DIR/ps" 2>/dev/null; exit 0; fi\n'
                   'if [ "$1" = "exec" ]; then\n'
                   '  [ -r "$FAKE_DIR/exec_$2" ] || exit 1\n'
                   '  cat "$FAKE_DIR/exec_$2"; exit 0\n'
                   'fi\n')
    fake_curl = ('#!/bin/sh\n'
                 'printf "%s\\n" "$*" >> "$FAKE_DIR/curl_calls"\n'
                 '[ -r "$FAKE_DIR/curl_fail" ] && exit 22\n'
                 'exit 0\n')
    for name, body in (("docker", fake_docker), ("curl", fake_curl)):
        path = work / "bin" / name
        path.write_bytes(body.encode("utf-8"))
        path.chmod(0o755)
    env = dict(os.environ, FAKE_DIR=(work / "fake").as_posix(), PATH=str(work / "bin") + os.pathsep + os.environ["PATH"])

    def run():
        done = subprocess.run([sh, (work / "watchdog.sh").as_posix()], env=env, capture_output=True, timeout=120)
        assert done.returncode == 0, done.stderr
        calls = work / "fake" / "curl_calls"
        log = work / "var" / "log.txt"
        return (calls.read_text(encoding="utf-8").splitlines() if calls.exists() else [],
                log.read_text(encoding="utf-8").splitlines() if log.exists() else [])

    def containers(live_age=None, paper_age=None):
        names = []
        for name, age in (("trading-live-abc-1", live_age), ("trading-paper-livecfg-def-1", paper_age)):
            target = work / "fake" / ("exec_" + name)
            if age is None:
                if target.exists():
                    target.unlink()
                continue
            names.append(name)
            target.write_text('{"heartbeat": {"states": []}, "heartbeat_age_seconds": %s, "level": 0}\n' % age, encoding="utf-8")
        (work / "fake" / "ps").write_text("".join(n + "\n" for n in names + ["something-else"]), encoding="utf-8")
    return work, run, containers


def test_the_watchdog_announces_every_new_outage(tmp_path):
    work, run, containers = watchdog(tmp_path)
    (work / "topic").write_text("secret-topic-xyz\n", encoding="utf-8")
    containers(live_age=12.3, paper_age=250.9)
    assert run() == ([], [])                                                          # healthy: nothing at all
    containers(live_age=301.2, paper_age=3.0)
    calls, log = run()
    assert len(calls) == 1 and "https://ntfy.sh/secret-topic-xyz" in calls[0] and "DENEME HESABI" not in calls[0]
    assert len(log) == 1 and "trading-live" in log[0] and "owner told" in log[0] and "secret-topic-xyz" not in log[0]
    assert len(run()[0]) == 1                                                         # same outage: quiet for four hours
    containers(live_age=5.0, paper_age=3.0)
    assert len(run()[0]) == 1                                                         # it recovered ...
    containers(live_age=None, paper_age=3.0)
    assert len(run()[0]) == 2                                                         # ... so the next outage is told again


def test_the_watchdog_labels_the_practice_service_and_logs_a_missing_topic(tmp_path):
    work, run, containers = watchdog(tmp_path)
    containers(live_age=None, paper_age=None)
    calls, log = run()                                                                # no topic file: nobody can be told,
    assert calls == [] and len(log) == 2 and all("no topic file" in line for line in log)   # but it is written down
    (work / "topic").write_text("secret-topic-xyz\n", encoding="utf-8")
    containers(live_age=4.0, paper_age="null")                                        # the practice bot has no heartbeat
    calls, log = run()
    assert len(calls) == 1 and "DENEME HESABI. Trading botu çalışmıyor görünüyor." in calls[0]
    (work / "fake" / "curl_fail").write_text("x", encoding="utf-8")
    containers(live_age=None, paper_age=4.0)
    calls, log = run()
    assert "push failed" in log[-1] and not (work / "var" / "stamp_trading-live").exists()   # so it is tried again


# ============================================================================ 11. the numbers and sentences of the digest
def test_the_first_day_counts_in_the_month_and_the_total(tmp_path):
    sim = Sim(tmp_path)
    w = window(sim, D1)

    def drop(t, r):
        if t == w.decide_from + 2 * MINUTES:
            sim.fb.set_price(PRODUCT, "45")                                           # the fund falls 10% after the buy
    sim.run_day(D1, hook=drop)
    lines = sim.digests()[0].split("\n")
    lost = 490.0 - float(sim.fb.equity)
    assert lost > 10 and lines[1] == "Hesap: %s dolar. Bugün yüzde %s azaldı (%s dolar)." % (
        vn.fmt_money(sim.fb.equity), vn.fmt_pct(lost / 490.0), vn.fmt_money(lost))
    assert lines[5] == "Bu ay: yüzde %s ekside. Başlangıçtan beri: yüzde %s ekside." % (
        vn.fmt_pct(lost / 490.0), vn.fmt_pct(lost / 490.0))                           # day one is part of both
    history = sim.rails().state()["equity_history"]
    assert [row["session"] for row in history] == [FRIDAY.isoformat(), D1.isoformat()] and history[0]["equity"] == 490.0


def test_a_breaker_day_never_says_the_extra_buy_was_made(tmp_path):
    sim = Sim(tmp_path, cash="49", qty="8.82", price="50", peak=490.0)

    def crash(t, r):
        if t == sim.session(D1).open + 30 * MINUTES:
            sim.fb.set_price(PRODUCT, "40")
    sim.ibs[D1] = 0.0
    sim.run_day(D1, hook=crash)
    keys = sim.record(D1).data["decision"]["reason_keys"]
    assert "OVERLAY_ENTER" not in keys and "BREAKER_REDUCE_ONLY" in keys
    reason = sim.digests()[0].split("\n")[4]
    assert "ek alım yaptım" not in reason and "Bugün sert düşüş olduğu için yeni alım yapmadım." in reason


def test_a_refused_order_names_the_share_of_the_whole_account(tmp_path):
    sim = Sim(tmp_path, cash="340", qty="1", price="50", owners="ANT")                # the fund: 50 of 490
    sim.fb.positions["ANT"] = Decimal("1")
    sim.fb.prices["ANT"] = Decimal("100")
    sim.fb.script(reject(422, 42210000, "refused"))
    sim.run_day(D1)
    assert sim.record(D1).status == ex.FAILED
    refused = [t for t in sim.alerts() if t.startswith("Alpaca emrimi kabul etmedi.")]
    assert len(refused) == 1 and "Şu an elimde yüzde 10 Nasdaq fonu var." in refused[0]   # 10% of 490, not 13% of 390


# ============================================================================ 12. error accounting and the edges
def test_a_broken_state_file_is_told_at_once(tmp_path):
    sim = Sim(tmp_path)
    runner = sim.runner()
    sim.clock.now = sim.session(D1).open - 40 * MINUTES
    runner.tick()
    (sim.settings.state_dir / "rails.json").write_text("{ broken", encoding="utf-8")
    sim.clock.now = sim.session(D1).open + 30 * MINUTES                               # the first tick that needs it
    runner.tick()
    assert sim.alerts() == [ERROR_TEXT] and "STATE_CORRUPT" in sim.heartbeat()["states"]


def test_a_fault_that_comes_and_goes_is_still_reported(tmp_path):
    sim = Sim(tmp_path)
    runner = sim.runner()
    t0 = sim.session(D1).open + 30 * MINUTES
    real, fail = runner._breaker_step, {"now": False}

    def flaky(now, session):
        if fail["now"]:
            raise RuntimeError("boom")
        return real(now, session)
    runner._breaker_step = flaky
    for index, bad in enumerate([True, False, True, False, False]):                   # never two failures in a row
        fail["now"] = bad
        sim.clock.now = t0 + index * 20 * SECONDS
        runner.tick()
    assert sim.alerts() == []
    fail["now"] = True
    sim.clock.now = t0 + 10 * MINUTES                                                 # the third inside half an hour
    runner.tick()
    assert sim.alerts() == [ERROR_TEXT]
    slow = Sim(tmp_path / "slow")
    runner = slow.runner()
    real_slow = runner._breaker_step
    runner._breaker_step = lambda now, session: (_ for _ in ()).throw(RuntimeError("boom"))
    for minutes in (0, 40, 80, 120):                                                  # rare hiccups are not a flood
        slow.clock.now = t0 + minutes * MINUTES
        runner.tick()
    assert slow.alerts() == [] and real_slow is not None


def test_turning_the_flag_off_with_an_order_out_does_not_break_the_loop(tmp_path):
    sim = Sim(tmp_path, mode="live", enabled=True)
    w = window(sim, D1)
    sim.fb.script("timeout_after_accept")
    real_by_id, real_list = sim.fb.get_order_by_client_id, sim.fb.list_orders
    sim.fb.get_order_by_client_id = lambda cid: None
    sim.fb.list_orders = lambda after, symbol=None: ([], False)
    sim.run_day(D1, stop=w.decide_from + 40 * SECONDS)
    assert live_record(sim, D1).status == ex.UNRESOLVED
    env = {"TRADING_MODE": "live", "ALPACA_LIVE_API_KEY": "lk", "ALPACA_LIVE_SECRET_KEY": "ls", "NTFY_TOPIC": "sim-topic"}
    dry = vcfg.load_settings(env=env, app_dir=tmp_path)                               # the owner turned the flag off
    assert dry.orders_enabled is False and dry.state_dir == sim.settings.state_dir
    runner = vrun.Runner(dry, sim.fb, sim.clock, sim.clock.sleep, sim.post)
    sent = len(sim.submits())
    for t in sim.times(D1):
        if t <= w.decide_from + 40 * SECONDS:
            continue
        if t >= sim.session(D1).close:
            sim.fb.get_order_by_client_id, sim.fb.list_orders = real_by_id, real_list   # the order shows up again
        sim.clock.now = t
        runner.tick()
        assert runner._error_state is None, t                                         # no section failed, on any tick
    assert len(sim.submits()) == sent and ERROR_TEXT not in " ".join(sim.texts())
    settled = live_record(sim, D1)
    assert settled.is_final and settled.status in (ex.DONE, ex.CAPPED)                # the real order was booked


def test_a_first_start_after_the_close_without_a_calendar_reports_nothing(tmp_path):
    sim = Sim(tmp_path)
    sim.fb.no_calendar = True
    runner = sim.runner()
    for minute in (5, 6, 30):
        sim.clock.now = dt.datetime(2026, 11, 16, 21, minute, tzinfo=UTC)             # 16:05 in New York
        runner.tick()
    assert sim.posts == [] and sim.record(D1) is None


def test_the_heartbeat_says_when_the_broker_calls_the_market_closed(tmp_path):
    sim = Sim(tmp_path)
    sim.fb.force_closed = True
    runner = sim.runner()
    sim.clock.now = sim.session(D1).open + 30 * MINUTES
    runner.tick()
    assert "MARKET_CLOSED" in sim.heartbeat()["states"]
    sim.fb.force_closed = False
    sim.clock.now += 20 * SECONDS
    runner.tick()
    assert "MARKET_CLOSED" not in sim.heartbeat()["states"]                           # asked again at once
    sim.clock.now = dt.datetime(2026, 11, 21, 17, 0, tzinfo=UTC)                      # a Saturday
    runner.tick()
    assert "MARKET_CLOSED" not in sim.heartbeat()["states"]


# ============================================================================ second pass over the fixes themselves
def test_any_fault_in_the_gate_blocks_new_orders_but_not_the_follow_up(tmp_path):
    sim = Sim(tmp_path)
    sim.fb.script("never_fill")
    w = window(sim, D1)

    def hook(t, runner):
        if t == w.decide_from + 20 * SECONDS:
            assert sim.record(D1).status == ex.SUBMITTED

            def broken():
                raise RuntimeError("the gate itself is broken")
            runner.rails.paused = broken
    sim.run_day(D1, hook=hook)
    rec = sim.record(D1)
    assert len(sim.submits()) == 1 and rec.is_final                                   # nothing new was sent ...
    assert sim.orders()[0].status == "canceled"                                       # ... the order out was still followed
    assert ERROR_TEXT in sim.alerts()                                                 # and the fault was reported


def test_a_dry_run_record_left_open_is_closed_not_called_an_unknown_order(tmp_path):
    sim = Sim(tmp_path, mode="live")                                                  # flag off
    w = window(sim, D1)
    runner = sim.run_day(D1, stop=w.decide_from - 20 * SECONDS)

    def die(*args, **kwargs):
        raise Death()
    runner.executor.run_chain = die
    sim.clock.now = w.decide_from
    with pytest.raises(Death):
        runner.tick()
    assert sim.record(D1).status == ex.NEW and sim.record(D1).data["target"] is not None
    sim.run_day(D1, start=sim.session(D1).close + 10 * SECONDS)                       # back after the close
    rec = sim.record(D1)
    assert rec.is_final and rec.status == ex.NOT_SUBMITTED and sim.submits() == []
    lines = sim.digests()[0].split("\n")
    assert lines[3] == "Bugün ne yaptım: İşlem yapmadım." and lines[4] == "Neden: Karar verdim ama emri gönderemedim."
    assert sim.alerts() == [not_sent("süre yetmedi")]


# ============================================================================ no session is left without its summary
MISSED_LINE = "Durum: Bugünkü karar saatini kaçırdım. Elimdekilere dokunmadım. Yarın normal devam ederim."


def test_two_missed_closes_are_both_reported_oldest_first(tmp_path):
    sim = Sim(tmp_path)
    w = window(sim, D1)
    sim.run_day(D1, stop=w.decide_from + 2 * MINUTES)                                 # trades, then the process is gone
    assert sim.record(D1).status == ex.DONE and sim.digests() == []
    sim.run_day(D2, start=sim.session(D2).close + 10 * SECONDS)                       # back only after the NEXT close
    stamps = [d.split("\n")[0] for d in sim.digests()]
    assert stamps == ["DENEME HESABI. Gün sonu özeti (16.11.2026)", "DENEME HESABI. Gün sonu özeti (17.11.2026)"]
    first, second = sim.digests()[0].split("\n"), sim.digests()[1].split("\n")
    assert first[3].endswith("dolarlık Nasdaq fonu aldım.") and first[6] == "Durum: Her şey normal."
    assert sim.record(D2).status == ex.MISSED and second[6] == MISSED_LINE
    assert {D1.isoformat(), D2.isoformat()} <= set(rows(sim))                         # both days have a closing value


def test_a_failed_reading_no_longer_drops_yesterdays_summary(tmp_path):
    sim = Sim(tmp_path)
    w1, w2 = window(sim, D1), window(sim, D2)
    sim.run_day(D1, stop=w1.decide_from + 2 * MINUTES)                                # dies before the close
    back = w2.decide_from + 40 * SECONDS
    break_call(sim, "get_account", until=back + 60 * SECONDS)                         # the account is unreadable for a minute
    sim.run_day(D2, start=back)                                                       # back inside the next decision window
    stamps = [d.split("\n")[0] for d in sim.digests()]
    assert stamps == ["DENEME HESABI. Gün sonu özeti (16.11.2026)", "DENEME HESABI. Gün sonu özeti (17.11.2026)"]
    assert sim.record(D2).is_final


def test_a_record_settled_a_day_later_is_told_in_its_own_dated_summary(tmp_path):
    sim = Sim(tmp_path)
    w = window(sim, D1)
    runner = sim.run_day(D1, stop=w.decide_from - 20 * SECONDS)

    def die(*args, **kwargs):
        raise Death()
    runner.executor.run_chain = die
    sim.clock.now = w.decide_from
    with pytest.raises(Death):
        runner.tick()
    sim.run_day(D2, start=sim.session(D2).open + 30 * MINUTES)                        # back the next day
    assert sim.record(D1).status == ex.NOT_SUBMITTED
    assert not [t for t in sim.alerts() if t.startswith("Bugün bir işlem yapmam gerekiyordu")]   # not "today", a day late
    first = sim.digests()[0].split("\n")
    assert first[0] == "DENEME HESABI. Gün sonu özeti (16.11.2026)"
    assert first[4] == "Neden: Karar verdim ama emri gönderemedim." and first[6] == "Durum: " + not_sent("süre yetmedi")
    assert sim.record(D2).is_final and len(sim.digests()) == 2


def test_an_account_unreadable_overnight_still_gets_its_summary(tmp_path):
    sim = Sim(tmp_path)

    def day_one(t, r):
        if t == sim.session(D1).close + 10 * SECONDS:
            sim.fb.down = True
    runner = sim.run_day(D1, hook=day_one)
    assert sim.digests() == [] and "DIGEST_PENDING" in sim.heartbeat()["states"]

    def day_two(t, r):
        if t == sim.session(D2).open + 30 * MINUTES:
            sim.fb.down = False
    sim.run_day(D2, runner, hook=day_two)
    stamps = [d.split("\n")[0] for d in sim.digests()]
    assert stamps == ["DENEME HESABI. Gün sonu özeti (16.11.2026)", "DENEME HESABI. Gün sonu özeti (17.11.2026)"]
    assert "DIGEST_PENDING" not in sim.heartbeat()["states"]
