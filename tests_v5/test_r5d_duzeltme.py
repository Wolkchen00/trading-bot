"""Regression tests for fix round R5d."""

import dataclasses
import datetime as dt
import json
from decimal import Decimal

import pytest

from tests_v5.fakebroker import partial
from tests_v5.test_r5_saldirgan import D1, D2, D3, PRODUCT, UTC, Sim, load_tool, window
from tests_v5.test_r5b_inceleme import Death, break_call, not_sent, rows
from v5 import broker as vb
from v5 import clock as vc
from v5 import executor as ex
from v5 import health as vh
from v5 import notify as vn
from v5 import ops as vo
from v5 import rails as vr
from v5 import state as vs


SECONDS = dt.timedelta(seconds=1)


def _open_strategy_record(sim, target, side):
    """A chain whose first partial fill is already durably recorded."""
    record = ex.SessionRecord.new(sim.settings.state_dir, D1, sim.settings.mode, dry_run=False)
    record.data.update(
        status=ex.PARTIAL,
        closed=False,
        target={"core_new": target, "ovl_new": 0.0, "total_new": target},
        intents=[{
            "client_order_id": "v5-paper-20261116-dec-1",
            "attempt": 1,
            "side": side,
            "filled_qty": "0.5",
        }],
    )
    record.save()
    return record


# --------------------------------------------------------------------------- Group A
def test_a1_untrusted_account_blocks_a_chain_under_way(tmp_path):
    sim = Sim(tmp_path)
    w = window(sim, D1)
    undo_open = break_call(sim, "get_open_orders", after_calls=1)

    def hook(moment, runner):
        if moment == w.decide_from + 20 * SECONDS:
            undo_open()
            original = sim.fb.get_account

            def inconsistent():
                return dataclasses.replace(original(), equity=float(original().equity) * 1.05)

            sim.fb.get_account = inconsistent

    sim.run_day(D1, hook=hook)
    record = sim.record(D1)
    assert sim.submits() == []
    assert record.status == ex.NOT_SUBMITTED and record.is_final
    assert not_sent("hesap değerini doğrulayamadım") in sim.alerts()


def test_a2_breaker_blocks_a_replanned_buy_after_a_partial_sale(tmp_path):
    sim = Sim(tmp_path, cash="300", qty="4", price="20")
    sim.clock.now = window(sim, D1).decide_from
    record = _open_strategy_record(sim, 0.4, "sell")
    runner = sim.runner()

    runner.executor.run_chain(record, window(sim, D1), buys_blocked=True)
    assert sim.submits() == [] and record.data["hold"] == "BREAKER"
    assert len(record.data["intents"]) == 1

    sim.clock.now = window(sim, D1).submit_until + SECONDS
    runner.executor.run_chain(record, window(sim, D1), buys_blocked=True)
    assert record.status == ex.PARTIAL and record.is_final


def test_a2_breaker_allows_a_replanned_sale_after_a_partial_buy(tmp_path):
    sim = Sim(tmp_path, cash="200", qty="6", price="100")
    sim.clock.now = window(sim, D1).decide_from
    record = _open_strategy_record(sim, 0.6, "buy")

    sim.runner().executor.run_chain(record, window(sim, D1), buys_blocked=True)
    assert sim.submits() and sim.submits()[-1][1]["side"] == "sell"


def test_a3_floor_resume_is_gated_by_a_foreign_product_order(tmp_path):
    sim = Sim(tmp_path, cash="250", qty="5", price="50", peak=1300.0)
    w = window(sim, D1)
    runner = sim.run_day(D1, stop=w.decide_from + 40 * SECONDS)
    real_submit = sim.fb.submit_order

    def die(*args, **kwargs):
        raise Death()

    sim.fb.submit_order = die
    sim.clock.now = w.decide_from + 60 * SECONDS
    with pytest.raises(Death):
        runner.tick()
    sim.fb.submit_order = real_submit
    floor = sim.record(D1, "flr")
    assert floor.status == ex.PLANNED and floor.data["intents"][-1]["submit_count"] == 1

    sim.fb.script("never_fill")
    real_submit(PRODUCT, "buy", "foreign-client", notional="10")
    before = len(sim.submits())
    sim.run_day(D1, start=w.decide_from + 80 * SECONDS)
    assert len(sim.submits()) == before
    assert any("Satışa başladım ama henüz bitmedi." in text for text in sim.alerts())


def test_a3_executor_resume_inspects_for_a_foreign_position(tmp_path):
    sim = Sim(tmp_path)
    w = window(sim, D1)
    runner = sim.run_day(D1, stop=w.decide_from - 20 * SECONDS)
    real_submit = sim.fb.submit_order
    sim.fb.submit_order = lambda *args, **kwargs: (_ for _ in ()).throw(Death())
    sim.clock.now = w.decide_from
    with pytest.raises(Death):
        runner.tick()
    sim.fb.submit_order = real_submit

    sim.fb.positions["SPY"] = Decimal("1")
    sim.fb.prices["SPY"] = Decimal("100")
    record = sim.record(D1)
    runner.executor.run_chain(record, w)
    assert sim.submits() == []
    assert record.data["hold"] == "FOREIGN_ASSET"


# --------------------------------------------------------------------------- Group B
def test_b1_a_reappeared_fund_position_is_not_called_sold_during_halt(tmp_path):
    sim = Sim(tmp_path, cash="250", qty="5", price="50", peak=1300.0)
    runner = sim.run_day(D1, stop=sim.session(D1).close - 20 * SECONDS)
    assert sim.rails().floor_state() == "HALT" and sim.rails().liquidating() is False

    sim.fb.positions[PRODUCT] = Decimal("5")
    sim.run_day(D1, runner, start=sim.session(D1).close + 10 * SECONDS)
    lines = sim.digests()[-1].split("\n")
    assert lines[2].startswith("Elimde: yüzde ") and "yüzde 0 Nasdaq" not in lines[2]
    assert lines[6] == "Durum: " + vn.build_alert("RAIL_LIQUIDATING", product_pct=sim.weight())
    assert sim.rails().liquidating() is True


def test_b2_status_rejects_a_heartbeat_far_in_the_future(tmp_path):
    durum = load_tool("v5_durum")
    now = dt.datetime(2026, 11, 19, 20, 0, tzinfo=UTC)

    def shown(offset):
        vs.atomic_write_json(
            tmp_path / "heartbeat.json",
            {"time": (now + offset).isoformat(), "states": [], "readiness": {}},
        )
        return durum.render(tmp_path, now).splitlines()

    future = shown(dt.timedelta(days=1))
    assert future[:2] == ["Bot çalışmıyor görünüyor.", "Kalp atışı kaydının saati ileride görünüyor."]
    assert shown(dt.timedelta(seconds=30))[0] == "Bot çalışıyor."
    assert shown(dt.timedelta(seconds=-30))[0] == "Bot çalışıyor."


def test_b3_dry_run_keeps_the_overlay_reason_and_uses_the_planned_buy(tmp_path):
    sim = Sim(tmp_path, mode="live", cash="40", peak=40.0)
    sim.ibs[D1] = 0.0
    sim.run_day(D1)
    record = sim.record(D1)
    intent = record.data["intents"][-1]
    assert record.status == ex.DRY_RUN and float(intent["notional"]) < float(intent["desired_usd"])
    lines = sim.digests()[0].split("\n")
    assert vn.REASON_SENTENCES["OVERLAY_WOULD"] in lines[4]
    assert vn.fmt_money(intent["notional"]) in lines[3]
    assert vn.fmt_money(intent["desired_usd"]) not in lines[3]


# --------------------------------------------------------------------------- Group C
def test_c1_account_balance_asof_is_optional_and_strict():
    base = {
        "status": "ACTIVE", "equity": "100", "last_equity": "99", "cash": "100",
        "buying_power": "100", "trading_blocked": False, "account_blocked": False,
    }
    assert vb.AlpacaBroker._account({**base, "balance_asof": "2026-11-13"}).balance_asof == dt.date(2026, 11, 13)
    for bad in (None, "", "20261113", "2026-02-30", 20261113):
        assert vb.AlpacaBroker._account({**base, "balance_asof": bad}).balance_asof is None


def _dated_reading(sim, asof, equity=490.0, last=490.0):
    return vr.EquityReading(
        sim.clock.now, equity, last, equity, 0.0, True, None, asof,
    )


def test_c2_c4_dated_last_equity_uses_the_engine_row_without_cross_check(tmp_path):
    sim = Sim(tmp_path)
    sim.rails().record_close(D1, 490.0)
    sim.clock.now = sim.session(D2).open + dt.timedelta(minutes=30)
    reading = _dated_reading(sim, dt.date(2026, 11, 13), equity=450.0, last=600.0)
    before = list(sim.rails().state()["equity_history"])
    assert sim.rails().check_breaker(reading, D2, D1, decisive=False) is False
    assert sim.rails().state()["equity_history"] == before
    assert sim.rails().breaker_info() is None


def test_c4_c5_pending_baseline_waits_then_accepts_the_named_close(tmp_path):
    sim = Sim(tmp_path)
    runner = sim.runner()
    runner.calendar = vc.SessionCalendar(vc.parse_calendar(sim.rows))
    sim.clock.now = sim.session(D2).open + dt.timedelta(minutes=30)

    old = _dated_reading(sim, dt.date(2026, 11, 13), equity=490.0, last=480.0)
    assert sim.rails().check_breaker(old, D2, D1, decisive=False) is False
    runner._backfill_close(old, sim.session(D2))
    assert D1.isoformat() not in rows(sim) and sim.rails().breaker_info() is None

    named = dataclasses.replace(old, last_equity=485.0, last_equity_asof=D1)
    runner._backfill_close(named, sim.session(D2))
    assert rows(sim)[D1.isoformat()] == 485.0

    blind = Sim(tmp_path / "blind")
    blind.clock.now = blind.session(D2).close - dt.timedelta(minutes=10)
    doubt = _dated_reading(blind, dt.date(2026, 11, 13), equity=490.0, last=480.0)
    assert blind.rails().check_breaker(doubt, D2, D1, decisive=True) is True
    assert blind.rails().breaker_info()["cause"] == "BASELINE_DOUBT"


@pytest.mark.parametrize("asof,closing", [(D1, True), (dt.date(2026, 11, 13), False)])
def test_c6_late_summary_uses_last_equity_only_when_it_names_that_day(tmp_path, asof, closing):
    sim = Sim(tmp_path)
    w1 = window(sim, D1)
    runner = sim.run_day(D1, stop=w1.decide_from + 2 * dt.timedelta(minutes=1))
    def dated_close(moment, current):
        sim.fb.last_equity = Decimal("475")
        sim.fb.balance_asof = asof

    sim.run_day(
        D2, runner,
        hook=dated_close,
        start=sim.session(D2).open + dt.timedelta(minutes=30),
        stop=sim.session(D2).open + dt.timedelta(minutes=30),
    )
    lines = sim.digests()[0].split("\n")
    if closing:
        assert lines[1].startswith("Hesap: 475,00 dolar.")
        assert rows(sim)[D1.isoformat()] == 475.0
    else:
        assert lines[1].startswith("Hesap şu an ")
        assert D1.isoformat() not in rows(sim)


def test_c7_twelve_minutes_after_close_is_not_a_closing_value(tmp_path):
    sim = Sim(tmp_path)
    runner = sim.run_day(D1, stop=window(sim, D1).decide_from + 2 * dt.timedelta(minutes=1))
    sim.clock.now = sim.session(D1).close + dt.timedelta(minutes=12)
    runner.tick()
    assert D1.isoformat() not in rows(sim)
    assert sim.digests()[0].split("\n")[1].startswith("Hesap şu an ")


# --------------------------------------------------------------------------- Group D
def _runner_owed_d1_and_d2(sim):
    runner = sim.runner()
    vs.atomic_write_json(
        runner.state_dir / "runner.json",
        {
            "started_at": (sim.session(D1).open - dt.timedelta(minutes=30)).isoformat(),
            "closed_session": None,
            "closed_sessions": [],
            "cutoff_alerted": None,
            "overlay_missing_streak": 0,
        },
    )
    return runner


def _history_is_ordered_and_unique(sim):
    sessions = [row["session"] for row in sim.rails().state()["equity_history"]]
    assert sessions == sorted(set(sessions))


def test_d1_d4_one_owed_session_does_not_delay_the_newer_summary(tmp_path):
    sim = Sim(tmp_path)
    runner = _runner_owed_d1_and_d2(sim)
    sim.clock.now = sim.session(D2).close + 70 * SECONDS
    original = sim.fb.get_account
    calls = {"count": 0}

    def first_read_fails():
        calls["count"] += 1
        if calls["count"] == 1:
            raise vb.BrokerUnavailable("one failed account read")
        return original()

    sim.fb.get_account = first_read_fails
    runner.tick()
    assert len(sim.digests()) == 1
    assert sim.digests()[0].splitlines()[0].endswith("(17.11.2026)")

    sim.clock.now += 60 * SECONDS
    runner.tick()
    assert len(sim.digests()) == 2
    assert sim.digests()[1].splitlines()[0].endswith("(16.11.2026)")
    state = runner._load_state()
    assert state["closed_session"] == D2.isoformat()
    assert {D1.isoformat(), D2.isoformat()} <= set(state["closed_sessions"])
    assert state["closed_sessions"] == sorted(state["closed_sessions"])
    _history_is_ordered_and_unique(sim)


def test_d2_d3_a_quiet_missed_session_survives_an_account_outage(tmp_path):
    sim = Sim(tmp_path)
    runner = _runner_owed_d1_and_d2(sim)
    cutoff = sim.session(D2).close + 130 * SECONDS
    break_call(sim, "get_account", until=cutoff)

    sim.clock.now = sim.session(D2).close + 70 * SECONDS
    runner.tick()
    assert sim.record(D1).status == ex.MISSED and sim.digests() == []

    sim.clock.now = cutoff + 20 * SECONDS
    runner.tick()
    assert {text.splitlines()[0][-11:-1] for text in sim.digests()} == {"16.11.2026", "17.11.2026"}
    _history_is_ordered_and_unique(sim)


def test_d1_old_runner_state_is_migrated_to_closed_sessions(tmp_path):
    sim = Sim(tmp_path)
    runner = sim.runner()
    vs.atomic_write_json(
        runner.state_dir / "runner.json",
        {"started_at": sim.clock.now.isoformat(), "closed_session": D1.isoformat()},
    )
    state = runner._load_state()
    assert state["closed_sessions"] == [D1.isoformat()]
    assert state["closed_session"] == D1.isoformat()


def test_d5_close_history_rejects_duplicate_and_out_of_order_rows(tmp_path):
    sim = Sim(tmp_path)
    sim.rails().record_close(D2, 500.0)
    sim.rails().record_close(D1, 490.0)
    sim.rails().record_close(D2, 510.0)
    sim.rails().record_close(D3, 520.0)
    assert sim.rails().state()["equity_history"] == [
        {"session": D2.isoformat(), "equity": 500.0},
        {"session": D3.isoformat(), "equity": 520.0},
    ]


# --------------------------------------------------------------------------- Group E
def test_e1_an_accepted_but_unfilled_order_is_not_called_refused(tmp_path):
    sim = Sim(tmp_path)
    sim.fb.script("never_fill")
    sim.run_day(D1)
    record = sim.record(D1)
    assert record.status == ex.FAILED
    assert sim.runner()._failure_kind(record) == "UNFILLED"
    assert vn.build_alert("ORDER_UNFILLED", product_pct=0.0) in sim.alerts()
    lines = sim.digests()[0].splitlines()
    assert lines[4] == "Neden: " + vn.OUTCOME_SENTENCES["UNFILLED"]
    assert lines[6] == "Durum: " + vn.build_alert("ORDER_UNFILLED", product_pct=0.0)


def test_e2_a_late_not_found_record_sends_no_today_alert(tmp_path):
    sim = Sim(tmp_path)
    record = ex.SessionRecord.new(sim.settings.state_dir, D1, sim.settings.mode, dry_run=False)
    record.data.update(status=ex.FAILED, closed=True, status_detail="NOT_FOUND", target={"total_new": 0.5})
    record.save()
    sim.runner()._after_chain(record, sim.session(D1), late=True)
    assert sim.alerts() == []


def test_e3_an_unknown_summary_gets_one_correction_with_the_real_fill(tmp_path):
    sim = Sim(tmp_path)
    until = sim.session(D1).close + dt.timedelta(hours=1)
    break_call(sim, "get_order", until=until)
    runner = sim.run_day(D1)
    first = sim.digests()
    assert len(first) == 1
    assert "sonucunu henüz öğrenemedim" in first[0].splitlines()[3]

    sim.clock.now = until + dt.timedelta(minutes=1)
    runner.tick()
    digests = sim.digests()
    assert len(digests) == 2
    correction = digests[-1].splitlines()
    assert correction[0].endswith("(16.11.2026), düzeltme")
    assert "Nasdaq fonu aldım." in correction[3]
    runner.tick()
    assert len(sim.digests()) == 2
    assert D1.isoformat() not in runner._load_state()["unknown_sessions"]


def test_e4_late_holding_text_and_sell_all_action_come_from_the_record(tmp_path):
    base = dict(
        date=D1, prefix="", equity=500.0, day_usd=None, day_pct=None,
        product_weight=0.4, owner_weight=0.0, owner_label="", action="none", action_usd=0.0,
        reason_keys=None, outcome=None, month_pct=None, total_pct=None, status_text=None,
        as_of_now=True,
    )
    assert vn.build_digest(base).splitlines()[2].startswith("Elimde şu an: yüzde 40")

    sim = Sim(tmp_path / "action", cash="300", qty="4", price="50")
    record = ex.SessionRecord.new(sim.settings.state_dir, D1, sim.settings.mode, dry_run=False)
    record.data.update(
        status=ex.DONE, closed=True, target={"total_new": 0.0},
        intents=[{"kind": "sell_all", "side": "sell", "filled_qty": "1", "filled_avg_price": 50.0}],
    )
    record.save()
    runner = sim.runner()
    reading = vr.read_equity(sim.fb, sim.clock.now)
    ctx = runner._digest_context(sim.clock.now, D1, reading, reading.equity, None, record, None)
    assert ctx["action"] == "sell_all"


def test_e5_state_reasons_do_not_claim_an_action():
    assert vn.REASON_SENTENCES["VOL_HIGH"] == "Piyasa çok oynak, bu yüzden payı küçük tutuyorum."
    assert vn.REASON_SENTENCES["BAND_HOLD"] == "Ana pay hedefe yeterince yakın."


def test_e6_attaching_a_decision_clears_an_earlier_not_ready_detail(tmp_path):
    sim = Sim(tmp_path)
    w = window(sim, D1)
    runner = sim.run_day(D1, stop=w.decide_from - 20 * SECONDS)
    sim.fb.no_price = True
    sim.clock.now = w.decide_from
    runner.tick()
    assert sim.record(D1).data["status_detail"]
    sim.fb.no_price = False
    runner.executor.run_chain = lambda *args, **kwargs: (_ for _ in ()).throw(Death())
    sim.clock.now += 20 * SECONDS
    with pytest.raises(Death):
        runner.tick()
    assert sim.record(D1).data["target"] is not None
    assert sim.record(D1).data["status_detail"] == ""


def test_e7_a_late_dry_run_settle_sends_no_today_not_sent_message(tmp_path):
    sim = Sim(tmp_path, mode="live")
    w = window(sim, D1)
    runner = sim.run_day(D1, stop=w.decide_from - 20 * SECONDS)
    runner.executor.run_chain = lambda *args, **kwargs: (_ for _ in ()).throw(Death())
    sim.clock.now = w.decide_from
    with pytest.raises(Death):
        runner.tick()
    assert sim.record(D1).data["target"] is not None

    sim.clock.now = sim.session(D1).close + dt.timedelta(minutes=12)
    sim.runner().tick()
    assert not [text for text in sim.alerts() if "emri gönderemedim" in text]


# --------------------------------------------------------------------------- Group F
def test_f1_decide_now_reports_window_and_existing_decision_blockers(tmp_path):
    sim = Sim(tmp_path)
    w = window(sim, D1)
    sim.clock.now = w.decide_from
    vh.write_heartbeat(sim.settings.state_dir, {"time": sim.clock.now.isoformat(), "states": []})
    code, document = vo.cmd_decide_now(sim.settings, sim.fb, sim.clock.now)
    assert code == 0 and document["blockers"] == []
    assert document["decision"] is not None and "plan" in document

    sim.clock.now = w.decide_from - dt.timedelta(hours=2)
    code, document = vo.cmd_decide_now(sim.settings, sim.fb, sim.clock.now)
    assert code == 1 and document["blockers"] == ["OUTSIDE_DECISION_WINDOW"]
    assert document["decision"] is not None and "plan" in document

    sim.run_day(D1, start=w.decide_from, stop=w.decide_from)
    sim.clock.now = w.decide_from + 20 * SECONDS
    code, document = vo.cmd_decide_now(sim.settings, sim.fb, sim.clock.now)
    assert code == 1 and "ALREADY_DECIDED" in document["blockers"]
    assert document["decision"] is not None and "plan" in document
