"""Reviewer's suite for fix round R5d (written by Claude from the contract `rf6/fix-r5d.md`, before the builder's
code existed). The defects came from three reviews of 2026-11-19: the first reviewer's second pass, Codex's
whole-tree review and a six-reviewer sweep. Codex builds; these tests are the reviewer's own check.
"""
import dataclasses
import datetime as dt
import json
from decimal import Decimal

import pytest

from tests_v5.fakebroker import partial, reject
from tests_v5.test_r5_saldirgan import D1, D2, D3, MISSED_TEXT, PRODUCT, UNRESOLVED_TEXT, UTC, Sim, load_tool, window
from tests_v5.test_r5b_inceleme import FRIDAY, MINUTES, PENDING, SECONDS, SOLD_ALL, Death, break_call, not_sent, rows
from tests_v5.test_r5c_saldirgan import die_inside_the_submit
from v5 import broker as vb
from v5 import executor as ex
from v5 import health as vh
from v5 import notify as vn
from v5 import ops as vo
from v5 import rails as vr
from v5 import runner as vrun

HOURS = dt.timedelta(hours=1)
LIQUIDATING = "Güvenlik satışı hala bitmedi."
DOUBT = "Hesap değeri tutarsız göründü. Yanlış alarm olmasın diye güvenlik satışı yapmadım, izlemeye devam ediyorum."
WOULD = "Bugün sert bir düşüş oldu; gerçek emir açık olsaydı yarın için küçük bir ek alım yapardım."
UNFILLED_REASON = "Neden: Karar verdim ama emrim gerçekleşmedi."


def first_step_without_an_order(sim):
    return break_call(sim, "get_open_orders", after_calls=1)


def scaled_account(sim, **changes):
    """Wrap get_account so that chosen fields are replaced (a callable gets the real account)."""
    real = sim.fb.get_account

    def wrapped():
        account = real()
        return dataclasses.replace(account, **{k: (v(account) if callable(v) else v) for k, v in changes.items()})
    sim.fb.get_account = wrapped
    return lambda: setattr(sim.fb, "get_account", real)


def engine_submits(sim, prefix="v5-"):
    return [c for c in sim.submits() if str(c[1].get("client_order_id", "")).startswith(prefix)]


# ============================================================================ A1: an untrusted reading blocks
def test_a_chain_under_way_stops_when_the_account_reading_cannot_be_trusted(tmp_path):
    sim = Sim(tmp_path)
    w = window(sim, D1)
    undo = first_step_without_an_order(sim)

    def hook(t, runner):
        if t == w.decide_from + 20 * SECONDS:
            undo()
            scaled_account(sim, equity=lambda a: a.equity * 1.05)                     # equity no longer adds up
    sim.run_day(D1, hook=hook)
    rec = sim.record(D1)
    assert sim.submits() == [] and rec.status == ex.NOT_SUBMITTED
    assert DOUBT in sim.alerts() and not_sent("hesap değerini doğrulayamadım") in sim.alerts()


# ============================================================================ A2: the breaker and a re-planned order
def test_a_reduction_that_turns_into_a_purchase_is_stopped_by_the_breaker(tmp_path):
    sim = Sim(tmp_path, cash="90", qty="8", price="50", peak=490.0)                   # 82% in the fund: the rule sells
    sim.fb.script(partial("0.5"))                                                     # the sale fills by half
    w = window(sim, D1)

    def hook(t, runner):
        if t == w.decide_from + 20 * SECONDS:
            first = sim.orders()[0]
            assert first.side == "sell" and Decimal(first.filled_qty) > 0
            sim.fb.cancel_order(first.id)                                             # the rest is cancelled at the broker
            sim.fb.set_price(PRODUCT, "18")                                           # and the fund collapses
    sim.run_day(D1, hook=hook)
    rec = sim.record(D1)
    assert sim.rails().breaker_info() is not None                                     # the breaker did trip
    assert [c[1]["side"] for c in sim.submits()] == ["sell"]                          # never a purchase
    assert rec.status == ex.PARTIAL and rec.is_final
    target = rec.data["target"]["total_new"]
    assert sim.weight() < target                                                      # a re-plan WOULD have bought


def test_a_sale_is_never_stopped_by_the_breaker(tmp_path):
    sim = Sim(tmp_path, cash="390", qty="2", price="50", peak=490.0)                  # 20% in the fund: the rule buys
    w = window(sim, D1)
    undo = first_step_without_an_order(sim)

    def hook(t, runner):
        if t == w.decide_from + 20 * SECONDS:
            undo()
            assert sim.record(D1).data["target"]["total_new"] > 0.25
            sim.fb.set_price(PRODUCT, "400")                                          # the fund is now most of the account
            sim.fb.last_equity = sim.fb.equity / Decimal("0.8")                       # and the day's baseline says -20%
    sim.run_day(D1, hook=hook)
    assert sim.rails().breaker_info() is not None
    assert [c[1]["side"] for c in sim.submits()] == ["sell"]                          # the reduction still goes out
    assert sim.record(D1).is_final and sim.record(D1).status in (ex.DONE, ex.CAPPED)


def test_the_executor_itself_refuses_a_purchase_when_buys_are_blocked(tmp_path):
    sim = Sim(tmp_path)
    w = window(sim, D1)
    sim.clock.now = w.decide_from
    record = ex.SessionRecord.new(sim.settings.state_dir, D1, "paper")
    record.data["target"] = {"core_new": 0.30, "ovl_new": 0.0, "total_new": 0.30}
    record.save()
    executor = sim.runner().executor
    executor.run_chain(record, w, buys_blocked=True)
    assert sim.submits() == [] and record.data["hold"] == "BREAKER" and record.data["intents"] == []
    executor.run_chain(record, w)                                                     # the flag does not stick
    assert len(sim.submits()) == 1 and record.status == ex.DONE


# ============================================================================ A3: a floor chain looks at the account
def test_a_floor_sale_that_died_is_not_resent_past_a_foreign_order(tmp_path):
    sim = Sim(tmp_path, cash="250", qty="5", price="50", peak=1300.0)
    w = window(sim, D1)
    runner = sim.run_day(D1, stop=w.decide_from + 40 * SECONDS)                       # confirming, not yet halted
    real = sim.fb.submit_order

    def die(*args, **kwargs):
        raise Death()
    sim.fb.submit_order = die
    sim.clock.now = w.decide_from + 60 * SECONDS                                      # the second reading: halt and sale
    with pytest.raises(Death):
        runner.tick()
    sim.fb.submit_order = real
    floor = sim.record(D1, "flr")
    assert floor.status == ex.PLANNED and floor.data["intents"][-1]["submit_count"] == 1 and sim.orders() == []
    sim.fb.script("never_fill")
    sim.fb.submit_order(PRODUCT, "sell", "someone-else-1", qty="1")                   # an unknown order in the fund
    sim.run_day(D1, start=w.decide_from + 80 * SECONDS)                               # a new process
    assert engine_submits(sim) == []                                                  # the floor sale was not re-sent
    assert sim.fb.positions[PRODUCT] == Decimal("5")
    assert [t for t in sim.alerts() if PENDING in t]


def test_the_executor_looks_at_the_account_before_it_resends(tmp_path):
    sim = Sim(tmp_path)
    w = window(sim, D1)
    die_inside_the_submit(sim, D1)
    sim.fb.positions["SPY"] = Decimal("1")
    sim.fb.prices["SPY"] = Decimal("100")
    sim.clock.now = w.decide_from + 20 * SECONDS
    record = sim.record(D1)
    sim.runner().executor.run_chain(record, w)                                        # no runner, no gate argument
    assert sim.submits() == [] and record.data["hold"] == "FOREIGN_ASSET" and not record.is_final


# ============================================================================ B1: a halt never says "all sold" while holding
def test_a_halt_summary_does_not_say_everything_is_sold_while_the_fund_is_held(tmp_path):
    sim = Sim(tmp_path, cash="250", qty="5", price="50", peak=1300.0)
    close = sim.session(D1).close

    def hook(t, runner):
        if t == close + 10 * SECONDS:                                                 # after the day's cutoff
            assert sim.rails().liquidating() is False and PRODUCT not in sim.fb.positions
            sim.fb.positions[PRODUCT] = Decimal("1")                                  # the fund is back in the account
            sim.fb.cash -= Decimal("50")
    sim.run_day(D1, hook=hook)
    lines = sim.digests()[0].split("\n")
    assert lines[2].startswith("Elimde: yüzde ") and not lines[2].startswith("Elimde: yüzde 0 ")
    assert lines[6].startswith("Durum: " + LIQUIDATING) and SOLD_ALL not in lines[6]
    assert sim.rails().liquidating() is True


# ============================================================================ B2: the owner's status text
def test_the_status_text_does_not_call_a_future_heartbeat_running(tmp_path):
    durum = load_tool("v5_durum")
    sim = Sim(tmp_path)
    now = dt.datetime(2026, 11, 19, 20, 0, tzinfo=UTC)
    path = sim.settings.state_dir / "heartbeat.json"

    def render(moment):
        path.write_text(json.dumps({"time": moment.isoformat(), "states": [], "readiness": {"ok": True, "problems": []}}),
                        encoding="utf-8")
        return durum.render(sim.settings.state_dir, now).split("\n")
    future = render(now + dt.timedelta(days=1))
    assert future[0] == "Bot çalışmıyor görünüyor." and future[1] == "Kalp atışı kaydının saati ileride görünüyor."
    assert "0 dakika önce" not in "\n".join(future)
    assert render(now + 30 * SECONDS)[0] == "Bot çalışıyor."                          # ordinary clock noise
    assert render(now - 2 * MINUTES)[:2] == ["Bot çalışıyor.", "Son kalp atışı: 2 dakika önce."]
    assert render(now - 10 * MINUTES)[0] == "Bot çalışmıyor görünüyor."


# ============================================================================ B3: a dry-run keeps its reason and its amount
def test_a_dry_run_says_why_it_would_have_bought_and_how_much(tmp_path):
    sim = Sim(tmp_path, mode="live")
    sim.ibs[D1] = 0.0
    sim.run_day(D1)
    lines = sim.digests()[0].split("\n")
    assert WOULD in lines[4] and "ek alım yaptım" not in lines[4] and "tutuyorum" not in lines[4]
    capped = Sim(tmp_path / "capped", mode="live")
    capped.ibs[D1] = 0.0
    scaled_account(capped, buying_power=90.0)                                         # only 90 dollars may be spent
    capped.run_day(D1)
    intent = capped.record(D1).data["intents"][-1]
    assert intent["capped"] is True and float(intent["notional"]) < 90.0 < intent["desired_usd"]
    line = capped.digests()[0].split("\n")[3]
    assert line == "Bugün ne yaptım: Gerçek emir açık olsaydı %s dolarlık Nasdaq fonu alırdım." % vn.fmt_money(float(intent["notional"]))


# ============================================================================ C: last_equity and the date the broker gives it
def test_the_broker_date_decides_whether_last_equity_is_yesterdays_close(tmp_path):
    reading = vr.read_equity(Sim(tmp_path).fb, dt.datetime(2026, 11, 16, 15, 0, tzinfo=UTC))
    assert reading.last_equity_asof is None and vr.last_equity_is_close_of(reading, FRIDAY) is None
    dated = dataclasses.replace(reading, last_equity_asof=FRIDAY)
    assert vr.last_equity_is_close_of(dated, FRIDAY) is True and vr.last_equity_is_close_of(dated, D1) is False
    account = vb.AlpacaBroker._account({"status": "ACTIVE", "equity": "1", "last_equity": "1", "cash": "1", "buying_power": "1",
                                        "trading_blocked": False, "account_blocked": False, "balance_asof": "2026-11-13"})
    assert account.balance_asof == FRIDAY
    for odd in (None, "", "yesterday", 20261113, "2026-13-45"):
        row = {"status": "ACTIVE", "equity": "1", "last_equity": "1", "cash": "1", "buying_power": "1",
               "trading_blocked": False, "account_blocked": False, "balance_asof": odd}
        assert vb.AlpacaBroker._account(row).balance_asof is None                     # never an error


def test_a_broker_that_has_not_turned_over_changes_nothing_when_the_engine_has_its_own_row(tmp_path):
    sim = Sim(tmp_path)
    runner = sim.run_day(D1)
    close_one = rows(sim)[D1.isoformat()]

    def stale(t, r):
        sim.fb.balance_asof = FRIDAY                                                  # still the close before D1
        sim.fb.last_equity = Decimal("400")                                           # a different number altogether
    sim.run_day(D2, runner, hook=stale)
    assert rows(sim)[D1.isoformat()] == close_one and rows(sim)[FRIDAY.isoformat()] == 490.0
    assert not [t for t in sim.alerts() if "tutarsız" in t] and sim.rails().breaker_info() is None
    assert sim.record(D2).is_final


def test_without_its_own_row_the_engine_waits_for_the_broker_to_name_the_day(tmp_path):
    sim = Sim(tmp_path)
    w1 = window(sim, D1)
    sim.run_day(D1, stop=w1.decide_from + 2 * MINUTES)                                # away at D1's close: no row
    close_one = sim.fb.equity
    assert D1.isoformat() not in rows(sim)
    turned = sim.session(D2).open + 2 * HOURS

    def hook(t, r):
        if t < turned:
            sim.fb.balance_asof, sim.fb.last_equity = FRIDAY, Decimal("400")          # not turned over yet
        else:
            sim.fb.balance_asof, sim.fb.last_equity = D1, close_one                   # now it names D1
    sim.run_day(D2, start=sim.session(D2).open + 30 * MINUTES, hook=hook)
    assert rows(sim)[D1.isoformat()] == pytest.approx(float(close_one))               # taken only once it was D1's
    assert 400.0 not in rows(sim).values()
    assert not [t for t in sim.alerts() if "tutarsız" in t] and sim.rails().breaker_info() is None


def test_a_baseline_still_unknown_at_decision_time_means_no_purchases(tmp_path):
    sim = Sim(tmp_path)
    w1 = window(sim, D1)
    sim.run_day(D1, stop=w1.decide_from + 2 * MINUTES)

    def never_turns(t, r):
        sim.fb.balance_asof, sim.fb.last_equity = FRIDAY, Decimal("400")
    sim.ibs[D2] = 0.0                                                                 # the rule would buy more
    before = len(sim.submits())
    sim.run_day(D2, start=sim.session(D2).open + 30 * MINUTES, hook=never_turns)
    info = sim.rails().breaker_info()
    assert info is not None and info["cause"] == "BASELINE_DOUBT"
    assert all(c[1]["side"] == "sell" for c in sim.submits()[before:])
    assert DOUBT in sim.alerts()


def test_a_reading_ten_minutes_late_is_not_a_closing_value(tmp_path):
    sim = Sim(tmp_path)
    w = window(sim, D1)
    runner = sim.run_day(D1, stop=w.decide_from + 2 * MINUTES)
    sim.clock.now = sim.session(D1).close + 12 * MINUTES
    runner.tick()
    assert D1.isoformat() not in rows(sim)
    assert sim.digests()[0].split("\n")[1].startswith("Hesap şu an ")
    on_time = Sim(tmp_path / "on_time")
    on_time.run_day(D1)
    assert D1.isoformat() in rows(on_time) and on_time.digests()[0].split("\n")[1].startswith("Hesap: ")


# ============================================================================ D: every session gets its summary
def stamps(sim):
    return [d.split("\n")[0].split("(")[1][:10] for d in sim.digests()]


def test_one_failed_read_in_the_evening_no_longer_drops_the_older_summary(tmp_path):
    sim = Sim(tmp_path)
    w1 = window(sim, D1)
    sim.run_day(D1, stop=w1.decide_from + 2 * MINUTES)                                # a real purchase, then away
    back = sim.session(D2).close + 2 * HOURS
    sim.clock.now = back
    runner = sim.runner()
    undo = break_call(sim, "get_account")
    runner.tick()                                                                     # the first read fails
    assert sim.digests() == []
    undo()
    for minutes in (1, 2, 3):
        sim.clock.now = back + minutes * MINUTES
        runner.tick()
    assert sorted(stamps(sim)) == ["16.11.2026", "17.11.2026"]                        # both days, none dropped
    history = sim.rails().state()["equity_history"]
    days = [row["session"] for row in history]
    assert days == sorted(days) and len(days) == len(set(days))


def test_todays_summary_does_not_wait_for_an_older_one(tmp_path):
    sim = Sim(tmp_path)
    w1 = window(sim, D1)
    sim.run_day(D1, stop=w1.decide_from - 2 * MINUTES)                                # away before D1's decision: MISSED
    w2 = window(sim, D2)
    fail = {"on": False}
    real = sim.fb.get_account

    def flaky():
        if fail["on"]:
            raise vb.BrokerUnavailable("account endpoint is flaky")
        return real()
    sim.fb.get_account = flaky

    def hook(t, r):
        fail["on"] = w2.decide_from - MINUTES <= t < w2.decide_from or t == w2.reconcile_from + 70 * SECONDS
    runner = sim.run_day(D2, start=w2.decide_from - 2 * MINUTES, hook=hook)
    assert "17.11.2026" in stamps(sim)                                                # today's own summary came on time
    sim.clock.now = sim.session(D2).close + 3 * HOURS
    runner.tick()
    assert sorted(stamps(sim)) == ["16.11.2026", "17.11.2026"]
    first = [d for d in sim.digests() if "(16.11.2026)" in d][0].split("\n")
    assert first[6] == "Durum: " + MISSED_TEXT                                        # the withheld news is in it


def test_a_state_file_with_only_the_old_marker_is_understood(tmp_path):
    sim = Sim(tmp_path)
    runner = sim.run_day(D1)
    path = sim.settings.state_dir / "runner.json"
    state = json.loads(path.read_text(encoding="utf-8"))
    assert state["closed_session"] == D1.isoformat() and D1.isoformat() in state["closed_sessions"]
    state.pop("closed_sessions")                                                      # as the deployed version wrote it
    path.write_text(json.dumps(state), encoding="utf-8")
    sim.run_day(D2, sim.runner())
    assert stamps(sim) == ["16.11.2026", "17.11.2026"]                                # no second summary for D1
    state = json.loads(path.read_text(encoding="utf-8"))
    assert state["closed_session"] == D2.isoformat() and state["closed_sessions"][-2:] == [D1.isoformat(), D2.isoformat()]


def test_history_rows_stay_ordered_whatever_is_recorded(tmp_path):
    rails = Sim(tmp_path).rails()
    rails.record_close(D1, 500.0)
    rails.record_close(D2, 510.0)
    rails.record_close(FRIDAY, 480.0)                                                 # an older day arrives late: refused
    rails.record_close(D1, 999.0)                                                     # a second value for a stored day: refused
    assert [(r["session"], r["equity"]) for r in rails.state()["equity_history"]] == [(D1.isoformat(), 500.0), (D2.isoformat(), 510.0)]
    assert rails.peak() == 510.0


# ============================================================================ E: outcomes and wording
def test_an_order_that_was_accepted_but_never_filled_is_not_called_refused(tmp_path):
    sim = Sim(tmp_path)
    sim.fb.script("never_fill")
    sim.run_day(D1)
    rec = sim.record(D1)
    assert rec.status == ex.FAILED and sim.orders()[0].status == "canceled"
    unfilled = [t for t in sim.alerts() if t.startswith("Emrim gerçekleşmedi ve iptal oldu.")]
    assert len(unfilled) == 1 and not [t for t in sim.alerts() if t.startswith("Alpaca emrimi kabul etmedi")]
    lines = sim.digests()[0].split("\n")
    assert lines[3] == "Bugün ne yaptım: İşlem yapmadım." and lines[4] == UNFILLED_REASON
    assert lines[6] == "Durum: " + unfilled[0]
    refused = Sim(tmp_path / "refused")
    refused.fb.script(reject(422, 42210000, "refused"))
    refused.run_day(D1)
    assert [t for t in refused.alerts() if t.startswith("Alpaca emrimi kabul etmedi.")]
    assert refused.digests()[0].split("\n")[4] == "Neden: Karar verdim ama Alpaca emrimi kabul etmedi."


def test_a_record_settled_a_day_later_never_sends_a_message_that_says_today(tmp_path):
    sim = Sim(tmp_path)
    die_inside_the_submit(sim, D1)                                                    # the order never reached the broker
    sim.run_day(D2, start=sim.session(D2).open + 30 * MINUTES)                        # back the next day
    rec = sim.record(D1)
    assert rec.status == ex.FAILED and rec.data["status_detail"] == "NOT_FOUND"
    assert not [t for t in sim.alerts() if t.startswith("Bugün bir işlem yapmam gerekiyordu")]
    first = [d for d in sim.digests() if "(16.11.2026)" in d][0].split("\n")
    assert first[4] == "Neden: Karar verdim ama emri gönderemedim."                   # the dated summary says it


def test_an_unknown_outcome_is_followed_by_a_correction(tmp_path):
    sim = Sim(tmp_path)
    close = sim.session(D1).close
    sim.fb.script("timeout_after_accept")
    break_call(sim, "get_order_by_client_id", until=close + 2 * HOURS)
    break_call(sim, "get_order", until=close + 2 * HOURS)
    runner = sim.run_day(D1)
    assert sim.digests()[0].split("\n")[3] == "Bugün ne yaptım: Bir emir gönderdim ama sonucunu henüz öğrenemedim."
    for minutes in (1, 2, 3):
        sim.clock.now = close + 2 * HOURS + minutes * MINUTES
        runner.tick()
    assert sim.record(D1).is_final and len(sim.digests()) == 2                        # exactly one correction
    fix = sim.digests()[1].split("\n")
    assert fix[0] == "DENEME HESABI. Gün sonu özeti (16.11.2026), düzeltme"
    assert fix[1].startswith("Hesap şu an ") and fix[2].startswith("Elimde şu an: yüzde ")
    assert fix[3].startswith("Bugün ne yaptım: ") and fix[3].endswith(" dolarlık Nasdaq fonu aldım.")
    assert fix[6] == "Durum: Her şey normal."
    sim.run_day(D2, runner)
    assert len([d for d in sim.digests() if "(16.11.2026)" in d]) == 2                # and never a third one


def test_a_late_summary_describes_the_holdings_as_of_now(tmp_path):
    sim = Sim(tmp_path, cash="250", qty="5", price="50")
    sim.set_return(D1, -0.20)                                                         # the trend breaks: everything is sold
    w = window(sim, D1)
    sim.run_day(D1, stop=w.decide_from + 2 * MINUTES)                                 # sold, then the process is gone
    assert PRODUCT not in sim.fb.positions and sim.digests() == []
    sim.fb.positions[PRODUCT] = Decimal("1")                                          # days later the account holds the fund again
    sim.fb.cash -= Decimal("50")
    sim.clock.now = sim.session(D1).close + 5 * HOURS
    runner = sim.runner()
    runner.tick()
    runner.tick()
    lines = sim.digests()[0].split("\n")
    assert lines[1].startswith("Hesap şu an ") and lines[2].startswith("Elimde şu an: yüzde ")
    assert lines[3] == "Bugün ne yaptım: Elimdeki fonun tamamını sattım."             # from the record, not from the account now


def test_reason_sentences_describe_a_state_not_an_action():
    assert vn.REASON_SENTENCES["VOL_HIGH"] == "Piyasa çok oynak, bu yüzden payı küçük tutuyorum."
    assert vn.REASON_SENTENCES["BAND_HOLD"] == "Ana pay hedefe yeterince yakın."
    for key in ("TREND_UP", "TREND_MIXED", "TREND_DOWN", "VOL_HIGH", "VOL_CALM", "BAND_HOLD"):
        text = vn.REASON_SENTENCES[key]
        assert not any(word in text for word in ("aldım", "sattım", "azalttım", "yaptım", "gerekmedi")), key


def test_an_earlier_hold_is_not_reported_as_the_cause_of_a_later_failure(tmp_path):
    sim = Sim(tmp_path)
    w = window(sim, D1)
    sim.fb.no_price = True                                                            # first ticks: no price
    runner = sim.run_day(D1, stop=w.decide_from + 20 * SECONDS)
    assert sim.record(D1).status == ex.NOT_READY
    sim.fb.no_price = False

    def die(*args, **kwargs):
        raise Death()
    runner.executor.run_chain = die                                                   # decides, then dies before sending
    sim.clock.now = w.decide_from + 40 * SECONDS
    with pytest.raises(Death):
        runner.tick()
    assert sim.record(D1).data["target"] is not None and sim.record(D1).data["status_detail"] == ""
    sim.run_day(D1, start=w.submit_until + 20 * SECONDS)
    assert sim.alerts() == [not_sent("süre yetmedi")]                                 # not "fiyat bilgisine ulaşamadım"


def test_a_dry_run_record_closed_a_day_later_says_nothing_about_today(tmp_path):
    sim = Sim(tmp_path, mode="live")
    w = window(sim, D1)
    runner = sim.run_day(D1, stop=w.decide_from - 20 * SECONDS)

    def die(*args, **kwargs):
        raise Death()
    runner.executor.run_chain = die
    sim.clock.now = w.decide_from
    with pytest.raises(Death):
        runner.tick()
    sim.run_day(D2, start=sim.session(D2).open + 30 * MINUTES)
    assert sim.record(D1).status == ex.NOT_SUBMITTED
    assert not [t for t in sim.alerts() if t.startswith("Bugün bir işlem yapmam gerekiyordu")]


# ============================================================================ F: decide-now
def test_decide_now_is_only_ready_when_the_engine_itself_would_decide(tmp_path):
    def at(sim, moment):
        sim.clock.now = moment                                                        # the broker's clock follows
        vh.write_heartbeat(sim.settings.state_dir, {"time": moment.isoformat(), "states": []})
        return vo.cmd_decide_now(sim.settings, sim.fb, moment)
    sim = Sim(tmp_path)
    w = window(sim, D1)
    sim.fb.last_equity = sim.fb.equity
    code, doc = at(sim, w.decide_from + MINUTES)
    assert code == 0 and doc["blockers"] == [] and doc["in_window"] is True and doc["plan"]["side"] == "buy"
    code, doc = at(sim, w.decide_from - 2 * HOURS)
    assert code == 1 and doc["blockers"] == ["OUTSIDE_DECISION_WINDOW"] and doc["decision"]["status"] == "READY"
    code, doc = at(sim, w.decide_until + MINUTES)
    assert code == 1 and "OUTSIDE_DECISION_WINDOW" in doc["blockers"]
    done = Sim(tmp_path / "done")
    done.run_day(D1, stop=window(done, D1).decide_from + MINUTES)
    assert done.record(D1).is_final
    code, doc = at(done, window(done, D1).decide_from + 2 * MINUTES)
    assert code == 1 and "ALREADY_DECIDED" in doc["blockers"]


# ============================================================================ second part (written by the reviewer)
ERROR_SENTENCE = "Bot beklenmeyen bir sorunla karşılaştı. Claude'a 'trading botuna bak' yaz."


def test_a_state_file_of_the_wrong_shape_is_corrupt_not_empty(tmp_path):
    sim = Sim(tmp_path)
    w1 = window(sim, D1)
    sim.run_day(D1, stop=w1.decide_from + 2 * MINUTES)                                # trades; the summary is still owed
    path = sim.settings.state_dir / "runner.json"
    path.write_text("[]", encoding="utf-8")                                           # valid JSON, wrong shape
    before = len(sim.submits())
    sim.ibs[D2] = 0.0
    sim.run_day(D2, sim.runner())
    assert path.read_text(encoding="utf-8") == "[]"                                   # never written over
    assert sim.digests() == [] and len(sim.submits()) == before                       # nothing reported as done, no order
    assert ERROR_SENTENCE in sim.alerts() and "STATE_CORRUPT" in sim.heartbeat()["states"]
    for bad in ('{"started_at": 5}', '{"closed_sessions": "2026-11-16"}', '{"closed_sessions": ["not a date"]}',
                '{"overlay_missing_streak": true}', '{"closed_session": "soon"}', '{"unknown_sessions": [1]}'):
        path.write_text(bad, encoding="utf-8")
        with pytest.raises(vrun.StateCorrupt):
            sim.runner()._load_state()
    path.write_text('{"closed_session": "2026-11-13", "started_at": "2026-11-12T00:00:00+00:00"}', encoding="utf-8")
    state = sim.runner()._load_state()                                                # an older, valid file still loads
    assert state["closed_sessions"] == ["2026-11-13"] and state["unknown_sessions"] == []


def test_a_record_of_another_mode_is_never_driven(tmp_path):
    sim = Sim(tmp_path, mode="live", enabled=True)
    w = window(sim, D1)
    stray = ex.SessionRecord.new(sim.settings.state_dir, D1, "paper")                 # copied from a practice folder
    stray.data["target"] = {"core_new": 0.5, "ovl_new": 0.0, "total_new": 0.5}
    stray.save()
    sim.run_day(D1)
    assert sim.submits() == [] and sim.orders() == []                                 # no real order from a paper record
    assert ERROR_SENTENCE in sim.alerts() and "STATE_CORRUPT" in sim.heartbeat()["states"]
    executor = sim.runner().executor
    with pytest.raises(vrun.StateCorrupt):
        executor.run_chain(ex.SessionRecord.load(sim.settings.state_dir, D1), w)
    with pytest.raises(vrun.StateCorrupt):
        executor.reconcile(ex.SessionRecord.load(sim.settings.state_dir, D1), sim.session(D1))
    forged = Sim(tmp_path / "forged")
    record = ex.SessionRecord.new(forged.settings.state_dir, D1, "paper")
    record.data["target"] = {"core_new": 0.3, "ovl_new": 0.0, "total_new": 0.3}
    record.data["intents"] = [{"client_order_id": "v5-paper-20261117-dec-1", "attempt": 1}]   # another day's id
    record.save()
    with pytest.raises(vrun.StateCorrupt):
        forged.runner().executor.run_chain(record, window(forged, D1))
    assert forged.submits() == []


def test_the_drill_only_sells_what_it_bought(tmp_path):
    drill = load_tool("v5_tatbikat")
    sim = Sim(tmp_path, cash="10000")
    sim.clock.now = sim.session(D1).open + HOURS
    real = sim.fb.submit_order
    seen = {"n": 0}

    def submit(symbol, side, client_order_id, notional=None, qty=None):
        order = real(symbol, side, client_order_id, notional=notional, qty=qty)
        seen["n"] += 1
        if seen["n"] == 1:                                                            # while the drill's entry is out
            sim.fb.positions[PRODUCT] = sim.fb.positions.get(PRODUCT, Decimal("0")) + Decimal("0.001")
            sim.fb.cash -= Decimal("0.05")                                            # someone else's dust arrives
        return order
    sim.fb.submit_order = submit
    result = drill.run_steps(sim.settings, sim.fb, ["round_trip"], sim.clock, sim.clock.sleep, sim.post)
    passed, evidence = result["round_trip"]
    assert passed is False and "the account changed during the drill" in evidence and "nothing was sold" in evidence
    assert [c[1]["side"] for c in sim.submits()] == ["buy"]                           # no sell at all
    assert sim.fb.positions[PRODUCT] > Decimal("0.001")                               # everything is still there
    clean = Sim(tmp_path / "clean", cash="10000")
    clean.clock.now = clean.session(D1).open + HOURS
    result = drill.run_steps(clean.settings, clean.fb, ["round_trip", "duplicate_id", "restart"],
                             clean.clock, clean.clock.sleep, clean.post)
    assert all(value[0] for value in result.values()), result                         # the undisturbed drill still passes
    assert PRODUCT not in clean.fb.positions


def test_an_unknown_month_or_total_is_not_called_no_change():
    base = dict(date=D1, prefix="", equity=490.0, day_usd=0.0, day_pct=0.0, product_weight=0.0, action="none",
                action_usd=0.0, reason_keys=["TREND_UP"], status_text=None)
    line = lambda **kw: vn.build_digest(dict(base, **kw)).split("\n")[5]
    assert line(month_pct=None, total_pct=None) == "Bu ay: bilgi yok. Başlangıçtan beri: bilgi yok."
    assert line(month_pct=0.0, total_pct=0.00004) == "Bu ay: değişim yok. Başlangıçtan beri: değişim yok."
    assert line(month_pct=-0.02, total_pct=None) == "Bu ay: yüzde 2,0 ekside. Başlangıçtan beri: bilgi yok."


def test_a_typed_peak_is_checked_against_the_account(tmp_path, capsys):
    import argparse
    sim = Sim(tmp_path, cash="250", qty="5", price="50", peak=1300.0)                 # worth 500
    rails = sim.rails()
    t0 = dt.datetime(2026, 11, 13, 20, 55, tzinfo=UTC)
    reading = lambda moment: vr.EquityReading(moment, 500.0, 500.0, 500.0, 0.0, True, None)
    rails.check_floor(reading(t0))
    assert rails.check_floor(reading(t0 + 61 * SECONDS)) == "HALT"
    sim.clock.now = dt.datetime(2026, 11, 16, 15, 0, tzinfo=UTC)

    def run(new_peak, force=False):
        namespace = argparse.Namespace(by="owner", note="devam", new_peak=new_peak, force=force)
        return vo._run_clear_halt(namespace, sim.settings, broker=sim.fb)
    before = (sim.settings.state_dir / "rails.json").read_bytes()
    for wrong in (5.0, 499.0, 1501.0, 50000.0):                                       # a slip of the keyboard
        assert run(wrong) == 1 and (sim.settings.state_dir / "rails.json").read_bytes() == before
    assert "clear-halt refused" in capsys.readouterr().out and rails.floor_state() == "HALT"
    sim.fb.down = True                                                                # unreadable account: refused too
    assert run(500.0) == 1 and rails.floor_state() == "HALT"
    sim.fb.down = False
    assert run(500.0) == 0 and rails.floor_state() == "OK" and rails.peak() == 500.0
    again = Sim(tmp_path / "forced", cash="250", qty="5", price="50", peak=1300.0)
    again.rails().check_floor(reading(t0))
    again.rails().check_floor(reading(t0 + 61 * SECONDS))
    namespace = argparse.Namespace(by="owner", note="biliyorum", new_peak=50000.0, force=True)
    assert vo._run_clear_halt(namespace, again.settings, broker=again.fb) == 0 and again.rails().peak() == 50000.0
    assert vo.clear_halt_refusal(None, 500.0) is not None and vo.clear_halt_refusal(reading(t0), "x") is not None
    assert vo.clear_halt_refusal(reading(t0), 500.0) is None and vo.clear_halt_refusal(reading(t0), 1500.0) is None


def test_an_unfilled_order_settled_a_day_later_sends_no_message_about_tomorrow(tmp_path):
    sim = Sim(tmp_path)
    sim.fb.script("never_fill")
    w = window(sim, D1)
    sim.run_day(D1, stop=w.decide_from + 2 * MINUTES)                                 # the order is out, the process dies
    assert sim.record(D1).status == ex.SUBMITTED
    sim.fb.cancel_order(sim.orders()[0].id)                                           # the broker lets it lapse at the close
    sim.run_day(D2, start=sim.session(D2).open + 30 * MINUTES)
    rec = sim.record(D1)
    assert rec.status == ex.FAILED and vrun.Runner._failure_kind(rec) == "UNFILLED"
    assert not [t for t in sim.alerts() if t.startswith("Emrim gerçekleşmedi")]       # not "... yarın yeniden karar veririm"
    first = [d for d in sim.digests() if "(16.11.2026)" in d][0].split("\n")
    assert first[4] == UNFILLED_REASON                                                # the dated summary says it


def test_the_watchdog_finds_a_container_by_its_service_label(tmp_path):
    from tests_v5.test_r5b_inceleme import watchdog
    work, run, containers = watchdog(tmp_path)
    (work / "topic").write_text("secret-topic-xyz\n", encoding="utf-8")
    fake = work / "fake"
    beat = '{"heartbeat": {"states": []}, "heartbeat_age_seconds": %s, "level": 0}\n'
    # The platform renamed both containers: no name starts with the old prefix any more.
    (fake / "ps").write_text("app-live-1\napp-paper-1\nsomething-else\n", encoding="utf-8")
    (fake / "labels").write_text("trading-live app-live-1\ntrading-paper-livecfg app-paper-1\n", encoding="utf-8")
    (fake / "exec_app-live-1").write_text(beat % 4.0, encoding="utf-8")
    (fake / "exec_app-paper-1").write_text(beat % 5.0, encoding="utf-8")
    assert run() == ([], [])                                                          # both found and healthy
    (fake / "exec_app-paper-1").write_text(beat % 900.0, encoding="utf-8")
    calls, log = run()
    assert len(calls) == 1 and "DENEME HESABI" in calls[0]                            # the right one is blamed
    assert len(log) == 1 and "trading-paper-livecfg" in log[0] and "900" in log[0]
    # A stale container that only has the old name must not hide the labelled one.
    (fake / "ps").write_text("trading-live-old-1\napp-live-1\napp-paper-1\n", encoding="utf-8")
    (fake / "exec_trading-live-old-1").write_text(beat % 5000.0, encoding="utf-8")
    (fake / "exec_app-paper-1").write_text(beat % 5.0, encoding="utf-8")
    calls, log = run()
    assert len(calls) == 1                                                            # live is fine: nothing new
    # No label anywhere: the name prefix is still the fallback.
    (fake / "labels").unlink()
    containers(live_age=3.0, paper_age=4.0)
    assert len(run()[0]) == 1
    containers(live_age=None, paper_age=4.0)
    calls, log = run()
    assert len(calls) == 2 and "DENEME HESABI" not in calls[1] and "trading-live: no running container" in log[-1]
