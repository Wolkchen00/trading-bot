"""R5g adversarial suite (written by the reviewer from rf6/fix-r5g.md, before reading the build).

Plan Revision 16: no sentence the owner reads names a calendar day relative to today. The engine works in
sessions; the session after today is three days away on a Friday and two before a holiday.
"""
import datetime as dt
import hashlib
import json
import re

import pytest

from tests_v5.fakebroker import partial
from tests_v5.test_r5_saldirgan import D1, D2, PRODUCT, UTC, Sim, assert_only_known_sentences, window
from v5 import executor as ex
from v5 import notify as vn

NEW = {
    "OVERLAY_ENTER": "Bugün sert bir düşüş oldu, sonraki işlem günü için küçük bir ek alım yaptım.",
    "OVERLAY_WOULD": "Bugün sert bir düşüş oldu; gerçek emir açık olsaydı sonraki işlem günü için küçük bir ek alım yapardım.",
    "OVERLAY_HELD": "Bugün sert bir düşüş oldu, bu yüzden elimdekinin bir kısmını sonraki işlem gününe kadar tutuyorum.",
    "OVERLAY_KEEP": "Düşüş sürdü, ek payı bir işlem günü daha tutuyorum.",
    "OVERLAY_EXIT": "Düşüş için tuttuğum ek payı sattım.",
    "BREAKER": "Hesap bugün yüzde {pct} düştü. Bugün yeni alım yapmıyorum. Sonraki işlem gününde normal çalışmaya dönerim. Senden bir şey beklemiyorum.",
    "HOLD_NOT_READY": "Bugün karar veremedim, çünkü {cause}. Elimdekilere dokunmadım. Sonraki işlem gününde yeniden denerim.",
    "MISSED": "Bugünkü karar saatini kaçırdım. Elimdekilere dokunmadım. Sonraki işlem gününde normal devam ederim.",
    "ORDER_UNFILLED": "Emrim gerçekleşmedi ve iptal oldu. Şu an elimde yüzde {product_pct} Nasdaq fonu var. Sonraki işlem gününde yeniden karar veririm.",
    "ORDER_PARTIAL": "Emrimin tamamı gerçekleşmedi. Şu an elimde yüzde {product_pct} Nasdaq fonu var. Sonraki işlem gününde duruma yeniden bakarım.",
    "NOT_SENT": "Bugün bir işlem yapmam gerekiyordu ama emri gönderemedim, çünkü {cause}. Elimdekilere dokunmadım. Sonraki işlem gününde yeniden karar veririm.",
}
REASON_KEYS = ("OVERLAY_ENTER", "OVERLAY_WOULD", "OVERLAY_HELD", "OVERLAY_KEEP", "OVERLAY_EXIT")
PLACEHOLDERS = {"BREAKER": ["pct"], "HOLD_NOT_READY": ["cause"], "ORDER_UNFILLED": ["product_pct"],
                "ORDER_PARTIAL": ["product_pct"], "NOT_SENT": ["cause"]}
# Every OTHER sentence of the five tables as it stood before this round (sha256 of their sorted JSON).
UNTOUCHED = "1f2df3f7576368b7989c9c56e2aa42b04cc0e082e3ecd6b5416e8d6a3c2fdb94"
# A word that begins with "yarın", "dün" or "ertesi", or one of the phrases: a calendar day next to today.
# Wider than the contract asks (the fresh reader of the round suggested the extra phrases for the future).
RELATIVE = re.compile(
    r"(?<!\w)(yarın|dün|ertesi|evvelsi|evvelki)\w*|öbür gün|önceki gün|geçen gün|bir gün daha", re.IGNORECASE)
FRI20, MON23, WED25, FRI27, MON30 = (dt.date(2026, 11, 20), dt.date(2026, 11, 23), dt.date(2026, 11, 25),
                                     dt.date(2026, 11, 27), dt.date(2026, 11, 30))


def tables():
    return {"REASON_SENTENCES": vn.REASON_SENTENCES, "TEMPLATES": vn.TEMPLATES, "CAUSE_SENTENCES": vn.CAUSE_SENTENCES,
            "FLOOR_WAIT_CAUSES": vn.FLOOR_WAIT_CAUSES, "OUTCOME_SENTENCES": vn.OUTCOME_SENTENCES}


def bear(tmp_path, first, **kw):
    """A market far below its averages (the core stays in cash), the engine new on the morning of `first`."""
    sim = Sim(tmp_path, **kw)
    sim.set_return(dt.date(2026, 11, 13), -0.20)
    sim.clock.now = dt.datetime.combine(first, dt.time(13, 0), UTC)
    return sim


def no_relative_day(texts):
    for text in texts:
        found = RELATIVE.search(text)
        assert found is None, (found.group(0), text)


# ============================================================================ the tables
def test_the_eleven_sentences_are_exactly_the_new_ones():
    for key, text in NEW.items():
        table = vn.REASON_SENTENCES if key in REASON_KEYS else vn.TEMPLATES
        assert table[key] == text, key


def test_no_sentence_names_a_day_relative_to_today():
    for name, table in tables().items():
        for key, text in table.items():
            found = RELATIVE.search(text)
            assert found is None, (name, key, found.group(0))


def test_the_pattern_itself_catches_the_old_words():
    for old in ("Yarın normal devam ederim.", "yarın için küçük", "bir kısmını yarına kadar", "Dünkü düşüş için",
                "dün", "DÜN akşam", "ek payı bir gün daha tutuyorum", "ertesi gün bakarım", "ertesi sabah",
                "öbür gün", "önceki gün", "geçen gün", "evvelki gün"):
        assert RELATIVE.search(old) is not None, old
    for fine in ("Bugün sert bir düşüş oldu", "sonraki işlem günü için", "bir işlem günü daha", "düşündüm", "ödündür",
                 "önceki emrimin akıbeti hala belli değil", "Gün sonu özetindeki", "gün içi fiyat aralığına",
                 "sonraki işlem gününde"):
        assert RELATIVE.search(fine) is None, fine


def test_every_other_sentence_is_untouched():
    rest = {name: {k: v for k, v in sorted(table.items()) if k not in NEW} for name, table in tables().items()}
    blob = json.dumps(rest, ensure_ascii=False, sort_keys=True).encode("utf-8")
    assert hashlib.sha256(blob).hexdigest() == UNTOUCHED
    assert {name: len(table) for name, table in tables().items()} == {
        "REASON_SENTENCES": 14, "TEMPLATES": 26, "CAUSE_SENTENCES": 30, "FLOOR_WAIT_CAUSES": 10, "OUTCOME_SENTENCES": 7}
    assert vn.PAPER_LABEL == "DENEME HESABI. " and vn.DRY_RUN_LABEL == "DENEME, gerçek emir gönderilmedi. "


def test_placeholders_are_the_same_and_none_opens_a_sentence():
    for key in NEW:
        table = vn.REASON_SENTENCES if key in REASON_KEYS else vn.TEMPLATES
        assert re.findall(r"\{(\w+)\}", table[key]) == PLACEHOLDERS.get(key, []), key
    for key, text in vn.TEMPLATES.items():
        assert not text.startswith("{"), key
    assert vn.build_alert("BREAKER", pct=0.152).endswith(
        "Bugün yeni alım yapmıyorum. Sonraki işlem gününde normal çalışmaya dönerim. Senden bir şey beklemiyorum.")
    assert vn.build_alert("HOLD_NOT_READY", cause="süre yetmedi") == (
        "Bugün karar veremedim, çünkü süre yetmedi. Elimdekilere dokunmadım. Sonraki işlem gününde yeniden denerim.")
    assert vn.build_alert("ORDER_UNFILLED", product_pct=0.30) == (
        "Emrim gerçekleşmedi ve iptal oldu. Şu an elimde yüzde 30 Nasdaq fonu var. Sonraki işlem gününde yeniden karar veririm.")
    assert vn.build_alert("NOT_SENT", cause="süre yetmedi") == (
        "Bugün bir işlem yapmam gerekiyordu ama emri gönderemedim, çünkü süre yetmedi. Elimdekilere dokunmadım. "
        "Sonraki işlem gününde yeniden karar veririm.")
    with pytest.raises(KeyError):
        vn.build_alert("HOLD_NOT_READY")                    # never a half-filled sentence


# ============================================================================ the days the old words were wrong
def test_a_dip_before_the_holiday_and_its_sale_on_the_half_day(tmp_path):
    sim = bear(tmp_path, WED25)
    sim.ibs[WED25] = 0.0
    runner = sim.run_day(WED25)
    wednesday = sim.digests()[0].split("\n")
    assert wednesday[0] == "DENEME HESABI. Gün sonu özeti (25.11.2026)"
    assert wednesday[4] == "Neden: Nasdaq düşüş eğiliminde, bu yüzden ana para nakitte bekliyor. " + NEW["OVERLAY_ENTER"]
    for hour in (9, 15, 18, 21):                            # Thursday the 26th: no session, nothing is said
        sim.clock.now = dt.datetime(2026, 11, 26, hour, 5, tzinfo=UTC)
        runner.tick()
    assert len(sim.posts) == 1
    sim.fb.balance_asof = WED25
    sim.run_day(FRI27, runner)
    friday = sim.digests()[1].split("\n")
    assert friday[0] == "DENEME HESABI. Gün sonu özeti (27.11.2026)"
    assert friday[3] == "Bugün ne yaptım: Elimdeki fonun tamamını sattım."
    assert friday[4] == "Neden: Nasdaq düşüş eğiliminde, bu yüzden ana para nakitte bekliyor. " + NEW["OVERLAY_EXIT"]
    assert [o.side for o in sim.orders()] == ["buy", "sell"] and sim.alerts() == []
    no_relative_day(sim.texts())
    assert_only_known_sentences(sim)


def test_a_dip_on_a_friday_and_its_sale_on_the_monday(tmp_path):
    sim = bear(tmp_path, FRI20)
    sim.ibs[FRI20] = 0.0
    runner = sim.run_day(FRI20)
    assert NEW["OVERLAY_ENTER"] in sim.digests()[0].split("\n")[4]
    sim.fb.balance_asof = FRI20
    sim.run_day(MON23, runner)
    assert NEW["OVERLAY_EXIT"] in sim.digests()[1].split("\n")[4]
    assert [o.side for o in sim.orders()] == ["buy", "sell"]
    no_relative_day(sim.texts())


def test_a_second_dip_keeps_the_share_over_the_weekend(tmp_path):
    sim = bear(tmp_path, WED25)
    sim.ibs[WED25] = 0.0
    sim.ibs[FRI27] = 0.0
    runner = sim.run_day(WED25)
    sim.fb.balance_asof = WED25
    runner = sim.run_day(FRI27, runner)
    friday = sim.digests()[1].split("\n")
    assert friday[3] == "Bugün ne yaptım: İşlem yapmadım."
    assert friday[4] == "Neden: Nasdaq düşüş eğiliminde, bu yüzden ana para nakitte bekliyor. " + NEW["OVERLAY_KEEP"]
    assert [o.side for o in sim.orders()] == ["buy"]
    sim.fb.balance_asof = FRI27
    sim.run_day(MON30, runner)
    assert NEW["OVERLAY_EXIT"] in sim.digests()[2].split("\n")[4]
    assert [o.side for o in sim.orders()] == ["buy", "sell"]
    no_relative_day(sim.texts())
    assert_only_known_sentences(sim)


def test_orders_off_on_a_dip_day_says_what_it_would_have_done(tmp_path):
    sim = bear(tmp_path, WED25, mode="live")
    assert sim.dry is True
    sim.ibs[WED25] = 0.0
    sim.run_day(WED25)
    lines = sim.digests()[0].split("\n")
    assert lines[0] == "DENEME, gerçek emir gönderilmedi. Gün sonu özeti (25.11.2026)"
    assert lines[4] == "Neden: Nasdaq düşüş eğiliminde, bu yüzden ana para nakitte bekliyor. " + NEW["OVERLAY_WOULD"]
    assert sim.submits() == []
    no_relative_day(sim.texts())


# ============================================================================ the messages that promised "tomorrow"
def test_a_missed_window_on_a_friday(tmp_path):
    sim = bear(tmp_path, FRI20)
    w = window(sim, FRI20)
    sim.run_day(FRI20, start=w.decide_until + dt.timedelta(seconds=20))    # the process starts too late
    assert sim.record(FRI20).status == ex.MISSED and sim.submits() == []
    assert sim.alerts() == [NEW["MISSED"]]
    assert sim.digests()[0].split("\n")[6] == "Durum: " + NEW["MISSED"]
    no_relative_day(sim.texts())


def test_a_day_without_the_broker_before_the_holiday(tmp_path):
    sim = bear(tmp_path, WED25)
    runner = sim.runner()
    sim.clock.now = sim.session(WED25).open - dt.timedelta(minutes=60)
    runner.tick()                                           # the calendar is cached while all is well
    sim.fb.down = True
    sim.run_day(WED25, runner)
    expected = NEW["HOLD_NOT_READY"].format(cause="Alpaca'ya bağlanamadım")
    assert expected in sim.alerts() and sim.submits() == []
    no_relative_day(sim.texts())
    assert_only_known_sentences(sim)


def test_the_brake_on_a_friday(tmp_path):
    sim = Sim(tmp_path, cash="49", qty="8.82", price="50", peak=490.0)     # 90 percent in the fund
    sim.clock.now = dt.datetime.combine(FRI20, dt.time(13, 0), UTC)

    def crash(t, r):
        if t == sim.session(FRI20).open + dt.timedelta(minutes=30):
            sim.fb.set_price(PRODUCT, "40")                 # -20 percent on the fund: -18 percent on the account
    sim.run_day(FRI20, hook=crash)
    brake = [t for t in sim.alerts() if t.startswith("Hesap bugün yüzde ")]
    assert brake == ["Hesap bugün yüzde 18,0 düştü. Bugün yeni alım yapmıyorum. Sonraki işlem gününde normal "
                     "çalışmaya dönerim. Senden bir şey beklemiyorum."]
    no_relative_day(sim.texts())
    assert_only_known_sentences(sim)


def test_a_half_filled_order(tmp_path):
    sim = Sim(tmp_path)
    sim.fb.script(partial("0.4"))
    sim.run_day(D1)
    assert sim.record(D1).status == ex.PARTIAL
    held = vn.fmt_weight(sim.weight())
    expected = "Emrimin tamamı gerçekleşmedi. Şu an elimde yüzde %s Nasdaq fonu var. Sonraki işlem gününde duruma yeniden bakarım." % held
    assert sim.digests()[0].split("\n")[6] == "Durum: " + expected
    no_relative_day(sim.texts())


def test_three_weeks_of_mixed_days_never_name_yesterday_or_tomorrow(tmp_path):
    sim = Sim(tmp_path)
    days = [d for d in sim.days if D1 <= d <= dt.date(2026, 12, 4)]
    assert dt.date(2026, 11, 26) not in days and FRI27 in days and len(days) == 14
    for i, day in enumerate(days):
        if i % 3 == 1:
            sim.ibs[day] = 0.0                              # a dip every third session, also on a Friday
    sim.set_return(dt.date(2026, 11, 24), -0.20)            # and the trend breaks in the second week
    runner, previous = None, None
    for day in days:
        if previous is not None:
            sim.fb.balance_asof = previous
        runner = sim.run_day(day, runner)
        previous = day
    assert len(sim.digests()) == len(days)
    joined = "\n".join(sim.texts())
    for sentence in (NEW["OVERLAY_ENTER"], NEW["OVERLAY_EXIT"]):
        assert sentence in joined, sentence                 # the run really met the sentences it is about
    no_relative_day(sim.texts())
    assert_only_known_sentences(sim)
