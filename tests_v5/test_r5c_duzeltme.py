"""R5c regressions: owner messages must describe only orders that really happened."""

import datetime as dt
from decimal import Decimal

import pytest

from tests_v5.fakebroker import reject
from tests_v5.test_r5_saldirgan import D1, D2, D3, PRODUCT, UTC, Sim, load_tool, window
from v5 import executor as ex
from v5 import health as vh
from v5 import notify as vn
from v5 import ops as vo
from v5 import rails as vr
from v5.data import DecisionInputs
from v5.strategy import Decision, READY


SECONDS = dt.timedelta(seconds=1)
FRIDAY = dt.date(2026, 11, 13)


class Death(BaseException):
    """The process vanishes during the broker call, after its durable counter was written."""


def _not_sent(cause):
    return (
        "Bugün bir işlem yapmam gerekiyordu ama emri gönderemedim, çünkü %s. "
        "Elimdekilere dokunmadım. Yarın yeniden karar veririm." % cause
    )


def _die_in_submit(sim, runner):
    original = sim.fb.submit_order

    def die(symbol, side, client_order_id, notional=None, qty=None):
        sim.fb.calls.append(("submit_order", {
            "symbol": symbol, "side": side, "client_order_id": client_order_id,
            "notional": notional, "qty": qty,
        }))
        raise Death()

    sim.fb.submit_order = die
    sim.clock.now = window(sim, D1).decide_from
    with pytest.raises(Death):
        runner.tick()
    sim.fb.submit_order = original
    record = sim.record(D1)
    assert record.status == ex.PLANNED
    assert record.data["intents"][-1]["submit_started"] is True
    assert record.data["intents"][-1]["submit_count"] == 1
    assert sim.orders() == []
    return record


def _runner_before_decision(sim):
    cutoff = window(sim, D1).decide_from - 20 * SECONDS
    return sim.run_day(D1, stop=cutoff)


def test_crashed_chain_is_stopped_by_a_new_foreign_asset(tmp_path):
    sim = Sim(tmp_path)
    runner = _runner_before_decision(sim)
    _die_in_submit(sim, runner)
    submits_before_foreign = len(sim.submits())

    sim.fb.positions["SPY"] = Decimal("1")
    sim.fb.prices["SPY"] = Decimal("100")
    sim.run_day(D1, sim.runner(), start=window(sim, D1).decide_from + 20 * SECONDS)

    assert len(sim.submits()) == submits_before_foreign
    assert any("benim almadığım bir varlık ya da emir var (SPY)" in text for text in sim.alerts())
    record = sim.record(D1)
    assert record.is_final and record.status == ex.FAILED and record.data["status_detail"] == "NOT_FOUND"
    lines = sim.digests()[0].split("\n")
    assert lines[3] == "Bugün ne yaptım: İşlem yapmadım."
    assert lines[4] == "Neden: Karar verdim ama emri gönderemedim."


def test_not_found_is_not_sent_but_a_real_refusal_is_still_refused(tmp_path):
    missing = Sim(tmp_path / "missing")
    runner = _runner_before_decision(missing)
    _die_in_submit(missing, runner)
    missing.clock.now = missing.session(D1).close + dt.timedelta(hours=3)
    missing.runner().tick()                 # late=True: the dated summary carries the news (round R5d, E2)

    record = missing.record(D1)
    assert record.status == ex.FAILED and record.data["status_detail"] == "NOT_FOUND"
    assert not [text for text in missing.alerts() if text.startswith("Bugün bir işlem yapmam gerekiyordu")]
    lines = missing.digests()[0].split("\n")
    assert lines[4] == "Neden: Karar verdim ama emri gönderemedim."
    assert lines[6] == "Durum: " + _not_sent("emrim Alpaca'ya ulaşmadı")

    refused = Sim(tmp_path / "refused")
    refused.fb.script(reject(422, 42210000, "refused"))
    refused.run_day(D1)
    assert refused.record(D1).status == ex.FAILED
    assert any(text.startswith("Alpaca emrimi kabul etmedi.") for text in refused.alerts())
    refused_lines = refused.digests()[0].split("\n")
    assert refused_lines[4] == "Neden: Karar verdim ama Alpaca emrimi kabul etmedi."
    assert refused_lines[6].startswith("Durum: Alpaca emrimi kabul etmedi.")


def _inputs(now):
    return DecisionInputs(
        closes=(100.0,) * 249,
        close_dates=tuple(D1 - dt.timedelta(days=index + 1) for index in range(249)),
        px=50.0,
        px_time=now,
        px_source="test",
        session_high=51.0,
        session_low=49.0,
        range_known=True,
        range_reason=None,
        range_bars=10,
        range_coverage=1.0,
    )


def _netted_decision():
    return Decision(
        status=READY,
        reason_code=None,
        trend_score=0.0,
        vol=0.2,
        exposure=0.0,
        core_target=0.0,
        ibs=0.0,
        wanted=True,
        overlay_data_missing=False,
        core_trade=True,
        core_held=0.4,
        ovl_held=0.0,
        core_new=0.0,
        ovl_new=0.4,
        total_new=0.4,
        trade=True,
        overlay_fraction_new=1.0,
        reason_keys=("TREND_DOWN", "OVERLAY_ENTER"),
    )


def test_netted_overlay_entry_says_held_without_claiming_a_buy(tmp_path):
    sim = Sim(tmp_path, cash="300", qty="4", price="50")
    runner = _runner_before_decision(sim)
    sim.clock.now = window(sim, D1).decide_from
    record = ex.SessionRecord.new(sim.settings.state_dir, D1, "paper")
    runner.executor.attach_decision(record, _inputs(sim.clock.now), _netted_decision())
    runner.executor.run_chain(record, window(sim, D1))
    assert record.status == ex.NETTED and sim.submits() == []
    assert record.data["before"]["overlay_fraction_committed"] <= 1e-12
    assert record.data["overlay_fraction_committed"] > 1e-12

    sim.run_day(D1, runner, start=sim.session(D1).close + 10 * SECONDS)
    digest = sim.digests()[0]
    assert "ek alım yaptım" not in digest
    assert vn.REASON_SENTENCES["OVERLAY_HELD"] in digest
    assert digest.split("\n")[3] == "Bugün ne yaptım: İşlem yapmadım."


def test_real_overlay_buy_and_next_days_sale_keep_their_sentences(tmp_path):
    sim = Sim(tmp_path)
    runner = sim.run_day(D1)
    sim.ibs[D2] = 0.0
    runner = sim.run_day(D2, runner)
    assert "ek alım yaptım" in sim.digests()[1]
    assert sim.record(D2).status == ex.DONE and sim.orders()[-1].side == "buy"

    sim.run_day(D3, runner)
    assert "Dünkü düşüş için tuttuğum ek payı sattım." in sim.digests()[2]
    assert sim.record(D3).status == ex.DONE and sim.orders()[-1].side == "sell"
    assert "OVERLAY_HELD" in vn.REASON_SENTENCES


def test_dry_run_digest_says_what_the_order_would_have_done(tmp_path):
    sim = Sim(tmp_path, mode="live")
    sim.ibs[D1] = 0.0
    sim.run_day(D1)
    record = sim.record(D1)
    intent = record.data["intents"][-1]
    assert record.status == ex.DRY_RUN and intent["kind"] == "buy"
    line = sim.digests()[0].split("\n")[3]
    assert line == (
        "Bugün ne yaptım: Gerçek emir açık olsaydı %s dolarlık Nasdaq fonu alırdım."
        % vn.fmt_money(float(intent["notional"]))
    )
    assert "ek alım yaptım" not in sim.digests()[0]


def _digest_context(action, reason_keys=()):
    return {
        "date": D1,
        "prefix": "",
        "equity": 500.0,
        "day_usd": 0.0,
        "day_pct": 0.0,
        "product_weight": 0.4,
        "action": action,
        "action_usd": 123.45,
        "reason_keys": list(reason_keys),
        "month_pct": 0.0,
        "total_pct": 0.0,
        "status_text": None,
    }


@pytest.mark.parametrize("action,expected", [
    ("would_buy", "Gerçek emir açık olsaydı 123,45 dolarlık Nasdaq fonu alırdım."),
    ("would_sell", "Gerçek emir açık olsaydı 123,45 dolarlık Nasdaq fonu satardım."),
    ("would_sell_all", "Gerçek emir açık olsaydı elimdeki fonun tamamını satardım."),
])
def test_build_digest_supports_dry_run_actions(action, expected):
    lines = vn.build_digest(_digest_context(action)).split("\n")
    assert lines[3] == "Bugün ne yaptım: " + expected


def test_build_digest_supports_the_overlay_held_reason():
    lines = vn.build_digest(_digest_context("none", ["OVERLAY_HELD"])).split("\n")
    assert lines[4] == "Neden: " + vn.REASON_SENTENCES["OVERLAY_HELD"]


def test_late_prior_digest_marks_current_equity_but_current_day_does_not(tmp_path):
    sim = Sim(tmp_path)
    close_one = sim.session(D1).close

    def lose_account(t, runner):
        if t >= close_one + 10 * SECONDS:
            sim.fb.down = True

    runner = sim.run_day(D1, hook=lose_account)
    assert sim.digests() == []

    close_two = sim.session(D2).close

    def recover_after_close(t, current):
        if t >= close_two + 10 * SECONDS:
            sim.fb.down = False

    sim.run_day(D2, runner, hook=recover_after_close)
    assert len(sim.digests()) == 2
    first, second = (digest.split("\n") for digest in sim.digests())
    value = vn.fmt_money(sim.fb.equity)
    assert first[0].endswith("(16.11.2026)") and first[1] == f"Hesap şu an {value} dolar."
    assert second[0].endswith("(17.11.2026)") and second[1].startswith(f"Hesap: {value} dolar.")


def test_decide_now_uses_the_same_baseline_cross_check_as_the_engine(tmp_path):
    sim = Sim(tmp_path)
    sim.ibs[D1] = 0.0
    sim.clock.now = window(sim, D1).decide_from + dt.timedelta(minutes=1)             # inside the decision window
    vh.write_heartbeat(sim.settings.state_dir, {"time": sim.clock.now.isoformat(), "states": []})
    sim.rails().record_close(FRIDAY, 600.0)
    sim.fb.last_equity = Decimal("490")

    code, document = vo.cmd_decide_now(sim.settings, sim.fb, sim.clock.now)
    assert code == 0
    assert document["breaker"] is True
    assert document["decision"]["trade"] is False and document["plan"] is None
    assert "BREAKER_REDUCE_ONLY" in document["decision"]["reason_keys"]

    reading = vr.read_equity(sim.fb, sim.clock.now)
    assert sim.rails().check_breaker(reading, D1, FRIDAY) is True
    assert sim.rails().breaker_info()["cause"] == "BASELINE_DOUBT"


def test_breaker_cause_has_all_three_results_and_distinguishes_no_row():
    now = dt.datetime(2026, 11, 16, 20, 50, tzinfo=UTC)
    clear = vr.EquityReading(now, 99.0, 100.0, 99.0, 0.0, True, None)
    breach = vr.EquityReading(now, 90.0, 100.0, 90.0, 0.0, True, None)
    bad_baseline = vr.EquityReading(now, 99.0, None, 99.0, 0.0, True, None)
    assert vr.breaker_cause(clear, vr.NO_ROW, 0.05) is None
    assert vr.breaker_cause(clear, 90.0, 0.05) == "BASELINE_DOUBT"
    assert vr.breaker_cause(bad_baseline, vr.NO_ROW, 0.05) == "BASELINE_DOUBT"
    assert vr.breaker_cause(breach, 100.0, 0.05) == "BREACH"


def test_health_marks_incomplete_and_future_heartbeats():
    now = dt.datetime(2026, 11, 19, 20, 0, tzinfo=UTC)
    incomplete = {"time": now.isoformat()}
    level, lines = vh.assess(incomplete, now)
    assert level >= 1 and "Heartbeat is incomplete." in lines

    future = {
        "time": (now + dt.timedelta(days=1)).isoformat(),
        "states": [],
        "readiness": {"ok": True, "problems": []},
    }
    level, lines = vh.assess(future, now)
    assert level == 2 and "Heartbeat time is in the future (86400 seconds)." in lines


def test_snapshot_fault_still_follows_an_accepted_order(tmp_path):
    sim = Sim(tmp_path)
    sim.fb.script("never_fill")
    decision = window(sim, D1).decide_from

    def break_snapshot_after_submit(t, runner):
        if t >= decision + 20 * SECONDS:
            def broken():
                raise RuntimeError("record scan failed")
            runner._snapshot = broken

    sim.run_day(D1, hook=break_snapshot_after_submit)
    record = sim.record(D1)
    assert len(sim.submits()) == 1
    assert sim.orders()[0].status == "canceled"
    assert record.is_final


def test_dry_run_record_without_an_intent_still_gets_a_digest(tmp_path):
    sim = Sim(tmp_path, mode="live")
    runner = _runner_before_decision(sim)
    record = ex.SessionRecord.new(sim.settings.state_dir, D1, "live", dry_run=True)
    record.data.update(status=ex.DRY_RUN, closed=True, intents=[])
    record.save()

    sim.run_day(D1, runner, start=sim.session(D1).close + 10 * SECONDS)
    assert len(sim.digests()) == 1
    assert sim.digests()[0].split("\n")[3] == "Bugün ne yaptım: İşlem yapmadım."


def test_missing_before_does_not_claim_that_an_overlay_was_entered(tmp_path):
    sim = Sim(tmp_path)
    runner = _runner_before_decision(sim)
    record = ex.SessionRecord.new(sim.settings.state_dir, D1, "paper")
    record.data.update(
        status=ex.NETTED,
        closed=True,
        decision={"reason_keys": ["OVERLAY_ENTER"]},
        overlay_fraction_committed=0.5,
        before=None,
        intents=[],
    )
    record.save()

    sim.run_day(D1, runner, start=sim.session(D1).close + 10 * SECONDS)
    digest = sim.digests()[0]
    assert "ek alım yaptım" not in digest
    assert vn.REASON_SENTENCES["OVERLAY_HELD"] not in digest


def test_flatten_foreign_ignores_sub_dollar_dust(tmp_path):
    sim = Sim(tmp_path, cash="10000")
    sim.fb.positions.update({"SPY": Decimal("2"), "XRPUSD": Decimal("0.000000001")})
    sim.fb.prices.update({"SPY": Decimal("600"), "XRPUSD": Decimal("0.5")})
    drill = load_tool("v5_tatbikat")

    result = drill.run_steps(
        sim.settings, sim.fb, ["flatten_foreign"], sim.clock, sim.clock.sleep, sim.post, confirm=True,
    )["flatten_foreign"]
    assert result[0] is True, result
    assert "SPY" not in sim.fb.positions
    assert sim.fb.positions["XRPUSD"] == Decimal("0.000000001")
    assert [call[1]["symbol"] for call in sim.submits()] == ["SPY"]


def _sim_with_reserved_spy(tmp_path):
    sim = Sim(tmp_path, cash="10000")
    sim.fb.positions["SPY"] = Decimal("2")
    sim.fb.prices["SPY"] = Decimal("600")
    sim.fb.script("never_fill")
    sim.fb.submit_order("SPY", "sell", "old-spy-order", qty="1")
    return sim


def test_flatten_foreign_waits_for_cancellations_before_closing(tmp_path):
    drill = load_tool("v5_tatbikat")
    stuck = _sim_with_reserved_spy(tmp_path / "stuck")
    stuck.fb.cancel_order = lambda order_id: None
    result = drill.run_steps(
        stuck.settings, stuck.fb, ["flatten_foreign"],
        stuck.clock, stuck.clock.sleep, stuck.post, confirm=True,
    )["flatten_foreign"]
    assert result == (False, "open orders did not cancel: SPY")
    assert len(stuck.submits()) == 1                       # only the already-open order; no close was attempted

    cleared = _sim_with_reserved_spy(tmp_path / "cleared")
    result = drill.run_steps(
        cleared.settings, cleared.fb, ["flatten_foreign"],
        cleared.clock, cleared.clock.sleep, cleared.post, confirm=True,
    )["flatten_foreign"]
    assert result[0] is True, result
    assert "SPY" not in cleared.fb.positions
    assert len(cleared.submits()) == 2                     # the old order and the closing order
