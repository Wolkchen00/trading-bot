"""Reviewer's suite for fix round R5c (written by Claude from the contract `rf6/fix-r5c.md`, before reading the
builder's code). The five defects were found by Codex's partial whole-tree review; Codex also built the fixes,
so these tests are the reviewer's own check of the same contract.
"""
import dataclasses
import datetime as dt
from decimal import Decimal

import pytest

from tests_v5.fakebroker import reject
from tests_v5.test_r5_saldirgan import D1, D2, D3, PRODUCT, UNRESOLVED_TEXT, UTC, Sim, window
from tests_v5.test_r5b_inceleme import FRIDAY, MINUTES, SECONDS, Death, break_call, not_sent, rows
from v5 import executor as ex
from v5 import health as vh
from v5 import notify as vn
from v5 import ops as vo
from v5 import rails as vr

FOREIGN_SPY = ("Hesapta benim almadığım bir varlık ya da emir var (SPY). Karışmamak için işlem yapmıyorum. "
               "Bunu sen yaptıysan Claude'a haber ver.")
HELD = "Bugün sert bir düşüş oldu, bu yüzden elimdekinin bir kısmını sonraki işlem gününe kadar tutuyorum."
BOUGHT = "Bugün sert bir düşüş oldu, sonraki işlem günü için küçük bir ek alım yaptım."
SOLD = "Düşüş için tuttuğum ek payı sattım."


def die_inside_the_submit(sim, day):
    """The process dies inside the broker call of the day's first order: the record is PLANNED, counted once,
    and the broker never saw the order. Returns nothing; the fake broker is whole again afterwards."""
    w = window(sim, day)
    runner = sim.run_day(day, stop=w.decide_from - 20 * SECONDS)
    real = sim.fb.submit_order

    def die(*args, **kwargs):
        raise Death()
    sim.fb.submit_order = die
    sim.clock.now = w.decide_from
    with pytest.raises(Death):
        runner.tick()
    sim.fb.submit_order = real
    rec = sim.record(day)
    intent = rec.data["intents"][-1]
    assert rec.status == ex.PLANNED and intent["submit_started"] is True and intent["submit_count"] == 1
    assert sim.orders() == [] and sim.submits() == []


# ============================================================================ D1 and D2
def test_a_resumed_chain_does_not_send_once_a_foreign_asset_is_in_the_account(tmp_path):
    sim = Sim(tmp_path)
    w = window(sim, D1)
    die_inside_the_submit(sim, D1)
    sim.fb.positions["SPY"] = Decimal("1")                                            # appears while the process is away
    sim.fb.prices["SPY"] = Decimal("100")
    sim.run_day(D1, start=w.decide_from + 20 * SECONDS)                               # a new process
    assert sim.submits() == [] and sim.orders() == []                                 # nothing was ever sent
    assert FOREIGN_SPY in sim.alerts()
    rec = sim.record(D1)
    assert rec.is_final and rec.status == ex.FAILED and rec.data["status_detail"] == "NOT_FOUND"
    told = not_sent("emrim Alpaca'ya ulaşmadı")
    assert told in sim.alerts() and not [t for t in sim.alerts() if t.startswith("Alpaca emrimi kabul etmedi")]
    lines = sim.digests()[0].split("\n")
    assert lines[3] == "Bugün ne yaptım: İşlem yapmadım." and lines[4] == "Neden: Karar verdim ama emri gönderemedim."
    assert lines[6] == "Durum: " + told


def test_the_same_resumed_chain_goes_on_when_the_account_is_clean(tmp_path):
    sim = Sim(tmp_path)
    w = window(sim, D1)
    die_inside_the_submit(sim, D1)
    sim.run_day(D1, start=w.decide_from + 20 * SECONDS)
    assert sim.record(D1).status == ex.DONE and len(sim.orders()) == 1                # same id, sent once
    assert len({c[1]["client_order_id"] for c in sim.submits()}) == 1 and sim.alerts() == []


@pytest.mark.parametrize("fault,code", [("down", "BROKER_DOWN"), ("status", "SNAPSHOT_INVALID")])
def test_a_resumed_chain_does_not_send_when_the_account_cannot_be_trusted(tmp_path, fault, code):
    sim = Sim(tmp_path)
    w = window(sim, D1)
    die_inside_the_submit(sim, D1)
    if fault == "down":
        break_call(sim, "get_open_orders")
    else:
        real = sim.fb.get_account
        sim.fb.get_account = lambda: dataclasses.replace(real(), trading_blocked=True)   # the account is blocked
    sim.run_day(D1, start=w.decide_from + 20 * SECONDS, stop=w.submit_until - 20 * SECONDS)
    assert sim.submits() == []
    assert sim.record(D1).data["hold"] == code


def test_a_real_refusal_is_still_called_a_refusal(tmp_path):
    sim = Sim(tmp_path)
    sim.fb.script(reject(422, 42210000, "refused"))
    sim.run_day(D1)
    assert sim.record(D1).status == ex.FAILED and sim.record(D1).data["status_detail"] != "NOT_FOUND"
    assert [t for t in sim.alerts() if t.startswith("Alpaca emrimi kabul etmedi.")]
    lines = sim.digests()[0].split("\n")
    assert lines[4] == "Neden: Karar verdim ama Alpaca emrimi kabul etmedi." and lines[6].startswith("Durum: Alpaca emrimi kabul etmedi.")
    assert vn.cause_for("NOT_FOUND") == "emrim Alpaca'ya ulaşmadı"


# ============================================================================ D3
def test_a_position_kept_because_of_the_dip_is_not_called_a_purchase(tmp_path):
    sim = Sim(tmp_path, cash="326.665", qty="3.2667", price="50", peak=490.0)         # one third in the fund
    sim.set_return(D1, -0.20)                                                         # the trend breaks: core goes to zero
    sim.ibs[D1] = 0.0                                                                 # and the dip rule keeps one third
    runner = sim.run_day(D1)
    rec = sim.record(D1)
    assert rec.status == ex.NETTED and sim.submits() == []                            # no order was needed
    assert "OVERLAY_ENTER" in rec.data["decision"]["reason_keys"]
    lines = sim.digests()[0].split("\n")
    assert lines[3] == "Bugün ne yaptım: İşlem yapmadım."
    assert BOUGHT not in lines[4] and HELD in lines[4]
    assert lines[2] == "Elimde: yüzde 33 Nasdaq fonu, yüzde 67 nakit."
    sim.run_day(D2, runner)                                                           # the next day it is really sold
    assert sim.orders()[-1].side == "sell" and PRODUCT not in sim.fb.positions
    second = sim.digests()[1].split("\n")
    assert second[3] == "Bugün ne yaptım: Elimdeki fonun tamamını sattım." and SOLD in second[4]


def test_a_real_extra_buy_and_its_real_sale_keep_their_sentences(tmp_path):
    sim = Sim(tmp_path)
    runner = sim.run_day(D1)
    sim.ibs[D2] = 0.0
    runner = sim.run_day(D2, runner)
    assert sim.orders()[-1].side == "buy" and BOUGHT in sim.digests()[1].split("\n")[4]
    runner = sim.run_day(D3, runner)
    assert sim.orders()[-1].side == "sell" and SOLD in sim.digests()[2].split("\n")[4]
    assert HELD not in "\n".join(sim.digests())


def test_a_dry_run_says_what_it_would_have_done(tmp_path):
    sim = Sim(tmp_path, mode="live")                                                  # the real account, flag off
    sim.ibs[D1] = 0.0
    sim.run_day(D1)
    rec = sim.record(D1)
    assert rec.status == ex.DRY_RUN and sim.submits() == []
    planned = float(rec.data["intents"][-1]["notional"])                              # the order that would have gone out
    lines = sim.digests()[0].split("\n")
    assert lines[0].startswith("DENEME, gerçek emir gönderilmedi. Gün sonu özeti (")
    assert lines[3] == "Bugün ne yaptım: Gerçek emir açık olsaydı %s dolarlık Nasdaq fonu alırdım." % vn.fmt_money(planned)
    assert "ek alım yaptım" not in sim.digests()[0] and HELD not in sim.digests()[0]  # nothing was bought or re-labelled
    assert lines[2] == "Elimde: yüzde 0 Nasdaq fonu, yüzde 100 nakit."
    flat = Sim(tmp_path / "nothing", mode="live", cash="318.5", qty="3.43", price="50")   # already on target
    flat.run_day(D1)
    assert flat.digests()[0].split("\n")[3] == "Bugün ne yaptım: İşlem yapmadım."
    halt = Sim(tmp_path / "halt", mode="live", cash="250", qty="5", price="50", peak=1300.0)
    halt.run_day(D1)
    assert halt.digests()[0].split("\n")[3] == "Bugün ne yaptım: Gerçek emir açık olsaydı elimdeki fonun tamamını satardım."
    selling = Sim(tmp_path / "sell", mode="live", cash="90", qty="8", price="50")     # far above the target
    selling.run_day(D1)
    line = selling.digests()[0].split("\n")[3]
    assert line.startswith("Bugün ne yaptım: Gerçek emir açık olsaydı ") and line.endswith(" dolarlık Nasdaq fonu satardım.")


def test_the_new_digest_sentences_are_exact():
    base = dict(date=D1, prefix="dry_run", equity=490.0, day_usd=0.0, day_pct=0.0, product_weight=0.0, action="none",
                action_usd=0.0, reason_keys=["TREND_UP", "OVERLAY_HELD"], month_pct=0.0, total_pct=0.0, status_text=None)
    lines = vn.build_digest(dict(base)).split("\n")
    assert lines[4] == "Neden: Nasdaq yükseliş eğiliminde. " + HELD
    for action, usd, text in (("would_buy", 147.0, "Gerçek emir açık olsaydı 147,00 dolarlık Nasdaq fonu alırdım."),
                              ("would_sell", 52.5, "Gerçek emir açık olsaydı 52,50 dolarlık Nasdaq fonu satardım."),
                              ("would_sell_all", 310.0, "Gerçek emir açık olsaydı elimdeki fonun tamamını satardım.")):
        assert vn.build_digest(dict(base, action=action, action_usd=usd)).split("\n")[3] == "Bugün ne yaptım: " + text
    assert vn.build_digest(dict(base, as_of_now=True, equity=450.0, day_usd=-40.0, day_pct=-0.08)).split("\n")[1] == "Hesap şu an 450,00 dolar."
    assert vn.build_digest(dict(base, as_of_now=False)).split("\n")[1] == "Hesap: 490,00 dolar. Bugün değişmedi."


# ============================================================================ D4
def test_a_summary_sent_late_says_the_value_is_of_now(tmp_path):
    sim = Sim(tmp_path)
    w1, w2 = window(sim, D1), window(sim, D2)

    def day_one(t, r):
        if t >= w1.reconcile_from:
            sim.fb.down = True
    runner = sim.run_day(D1, hook=day_one)
    assert sim.digests() == []

    def day_two(t, r):
        if t == w2.reconcile_from + 10 * SECONDS and sim.fb.down:
            sim.fb.down = False
    sim.run_day(D2, runner, hook=day_two)
    first, second = sim.digests()[0].split("\n"), sim.digests()[1].split("\n")
    assert first[0].endswith("(16.11.2026)") and first[1] == "Hesap şu an %s dolar." % vn.fmt_money(sim.fb.equity)
    assert second[0].endswith("(17.11.2026)") and second[1].startswith("Hesap: %s dolar." % vn.fmt_money(sim.fb.equity))
    back = Sim(tmp_path / "exact")                                                    # reported during the next session:
    back.run_day(D1, stop=window(back, D1).decide_from + 2 * MINUTES)                 # then the broker's own close is used
    back.run_day(D2, start=back.session(D2).open + 30 * MINUTES)
    assert back.digests()[0].split("\n")[1].startswith("Hesap: ")


# ============================================================================ D5
def test_decide_now_applies_the_same_baseline_check_as_the_engine(tmp_path):
    sim = Sim(tmp_path, cash="600", peak=600.0)
    sim.ibs[D1] = 0.0
    sim.rails().record_close(FRIDAY, 490.0)                                           # our own close of the day before ...
    sim.fb.last_equity = sim.fb.equity + Decimal("100")                               # ... and the broker says otherwise
    sim.clock.now = window(sim, D1).decide_from
    code, doc = vo.cmd_decide_now(sim.settings, sim.fb, sim.clock.now)
    assert doc["breaker"] is True and doc["decision"]["trade"] is False and doc["plan"] is None
    assert "BREAKER_REDUCE_ONLY" in doc["decision"]["reason_keys"]
    reading = vr.read_equity(sim.fb, sim.clock.now)
    assert sim.rails().check_breaker(reading, D1, FRIDAY) is True
    assert sim.rails().breaker_info()["cause"] == "BASELINE_DOUBT"


def test_breaker_cause_is_one_rule_for_everyone():
    def reading(equity, last):
        return vr.EquityReading(dt.datetime(2026, 11, 16, 15, 0, tzinfo=UTC), equity, last, equity, 0.0, True, None)
    assert vr.breaker_cause(reading(500.0, 500.0), vr.NO_ROW, 0.15) is None
    assert vr.breaker_cause(reading(425.0, 500.0), vr.NO_ROW, 0.15) == "BREACH"
    assert vr.breaker_cause(reading(425.01, 500.0), vr.NO_ROW, 0.15) is None
    assert vr.breaker_cause(reading(500.0, 500.0), 489.9, 0.15) == "BASELINE_DOUBT"   # more than 2% apart
    assert vr.breaker_cause(reading(500.0, 500.0), 491.0, 0.15) is None
    for bad in (None, 0.0, -1.0, float("nan")):
        assert vr.breaker_cause(reading(500.0, bad), vr.NO_ROW, 0.15) == "BASELINE_DOUBT"
    assert vr.breaker_cause(reading(500.0, 500.0), None, 0.15) == "BASELINE_DOUBT"    # a stored row without a number


def test_health_does_not_call_a_broken_heartbeat_healthy():
    now = dt.datetime(2026, 11, 19, 20, 0, tzinfo=UTC)
    whole = {"time": now.isoformat(), "states": [], "readiness": {"ok": True, "problems": []}, "ntfy_configured": True}
    assert vh.assess(whole, now) == (0, ["Healthy."])
    level, lines = vh.assess({"time": now.isoformat()}, now)
    assert level >= 1 and "Heartbeat is incomplete." in lines
    for broken in ({**whole, "states": "FLOOR_HALT"}, {**whole, "readiness": None}, {k: v for k, v in whole.items() if k != "states"}):
        assert vh.assess(broken, now)[0] >= 1
    level, lines = vh.assess({**whole, "time": (now + dt.timedelta(days=1)).isoformat()}, now)
    assert level == 2 and lines[0] == "Heartbeat time is in the future (86400 seconds)."
    assert vh.assess({**whole, "time": (now + dt.timedelta(seconds=45)).isoformat()}, now)[0] == 0   # ordinary clock noise
