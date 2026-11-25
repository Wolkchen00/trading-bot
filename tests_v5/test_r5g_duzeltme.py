"""R5g: owner sentences never name a calendar day relative to today (RF-ISSUES-6 item 36)."""
import datetime as dt
import re

from tests_v5.test_r5_saldirgan import MISSED_TEXT, Sim, window
from v5 import executor as ex
from v5 import notify as vn

WED, FRI_EARLY, FRI = dt.date(2026, 11, 25), dt.date(2026, 11, 27), dt.date(2026, 11, 20)

NEW_TEXTS = {
    "REASON_SENTENCES": {
        "OVERLAY_ENTER": "Bugün sert bir düşüş oldu, sonraki işlem günü için küçük bir ek alım yaptım.",
        "OVERLAY_WOULD": "Bugün sert bir düşüş oldu; gerçek emir açık olsaydı sonraki işlem günü için küçük bir ek alım yapardım.",
        "OVERLAY_HELD": "Bugün sert bir düşüş oldu, bu yüzden elimdekinin bir kısmını sonraki işlem gününe kadar tutuyorum.",
        "OVERLAY_KEEP": "Düşüş sürdü, ek payı bir işlem günü daha tutuyorum.",
        "OVERLAY_EXIT": "Düşüş için tuttuğum ek payı sattım.",
    },
    "TEMPLATES": {
        "BREAKER": "Hesap bugün yüzde {pct} düştü. Bugün yeni alım yapmıyorum. Sonraki işlem gününde normal çalışmaya dönerim. Senden bir şey beklemiyorum.",
        "HOLD_NOT_READY": "Bugün karar veremedim, çünkü {cause}. Elimdekilere dokunmadım. Sonraki işlem gününde yeniden denerim.",
        "MISSED": "Bugünkü karar saatini kaçırdım. Elimdekilere dokunmadım. Sonraki işlem gününde normal devam ederim.",
        "ORDER_UNFILLED": "Emrim gerçekleşmedi ve iptal oldu. Şu an elimde yüzde {product_pct} Nasdaq fonu var. Sonraki işlem gününde yeniden karar veririm.",
        "ORDER_PARTIAL": "Emrimin tamamı gerçekleşmedi. Şu an elimde yüzde {product_pct} Nasdaq fonu var. Sonraki işlem gününde duruma yeniden bakarım.",
        "NOT_SENT": "Bugün bir işlem yapmam gerekiyordu ama emri gönderemedim, çünkü {cause}. Elimdekilere dokunmadım. Sonraki işlem gününde yeniden karar veririm.",
    },
}
BAD = re.compile(r"\b(yarın|dün)|ertesi gün|bir gün daha", re.IGNORECASE)
TABLES = ("REASON_SENTENCES", "TEMPLATES", "CAUSE_SENTENCES", "FLOOR_WAIT_CAUSES", "OUTCOME_SENTENCES")


def _texts(table):
    value = getattr(vn, table)
    return value.values() if isinstance(value, dict) else value


def test_the_eleven_sentences_and_no_relative_day_anywhere():
    for table, entries in NEW_TEXTS.items():
        for key, text in entries.items():
            assert getattr(vn, table)[key] == text, key
    for table in TABLES:
        for text in _texts(table):
            assert not BAD.search(text), (table, text)


def test_dip_before_a_holiday_says_next_session_and_no_relative_day(tmp_path):
    sim = Sim(tmp_path)
    sim.ibs[WED] = 0.0
    runner = sim.run_day(WED)
    assert "sonraki işlem günü için küçük bir ek alım yaptım." in sim.digests()[0].split("\n")[4]
    assert not re.search(r"\byarın", sim.digests()[0], re.IGNORECASE)
    sim.clock.now = dt.datetime(2026, 11, 27, 13, 0, tzinfo=dt.timezone.utc)
    sim.run_day(FRI_EARLY, runner)
    assert sim.record(FRI_EARLY).status == ex.DONE
    assert "Düşüş için tuttuğum ek payı sattım." in sim.digests()[1]
    assert not re.search(r"\bdün", sim.digests()[1], re.IGNORECASE)
    assert [o.side for o in sim.orders()] == ["buy", "sell"]


def test_second_dip_keeps_the_extra_share_for_one_more_session(tmp_path):
    sim = Sim(tmp_path)
    sim.ibs[WED] = 0.0
    sim.ibs[FRI_EARLY] = 0.0
    runner = sim.run_day(WED)
    sim.clock.now = dt.datetime(2026, 11, 27, 13, 0, tzinfo=dt.timezone.utc)
    sim.run_day(FRI_EARLY, runner)
    assert "Düşüş sürdü, ek payı bir işlem günü daha tutuyorum." in sim.digests()[1]
    assert [o.side for o in sim.orders()] == ["buy"]


def test_missed_window_on_a_friday_uses_the_new_text(tmp_path):
    sim = Sim(tmp_path)
    w = window(sim, FRI)
    sim.run_day(FRI, start=w.decide_until + dt.timedelta(seconds=20))
    assert sim.record(FRI).status == ex.MISSED
    new = NEW_TEXTS["TEMPLATES"]["MISSED"]
    assert new == MISSED_TEXT and sim.alerts() == [new]
    assert sim.digests()[0].split("\n")[6] == "Durum: " + new
