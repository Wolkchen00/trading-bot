"""R5f, the reviewer's suite (written by Claude from `rf6/fix-r5f.md`, before reading the build).

Three small things the review of the R5d -> R5e upgrade found: two sentences that could mislead, and a health
tool that must answer even when it cannot read the message file."""
import datetime as dt
import json
from decimal import Decimal

import pytest

from tests_v5.test_r5_saldirgan import D1, D2, D3, PRODUCT, UTC, Sim, assert_only_known_sentences, window
from v5 import broker as vb
from v5 import health as vh
from v5 import notify as vn

MINUTES = dt.timedelta(minutes=1)
DOUBT = "Hesap değeri tutarsız göründü. Yanlış alarm olmasın diye güvenlik satışı yapmadım, izlemeye devam ediyorum."
FRIDAY, MONDAY = dt.date(2026, 11, 20), dt.date(2026, 11, 23)


def test_no_owner_sentence_begins_with_a_blank():
    """`assert_only_known_sentences` recognises a text by the fixed beginning of its sentence."""
    assert [kind for kind, text in vn.TEMPLATES.items() if text.startswith("{")] == []


# ============================================================================ T1
def test_a_doubtful_reading_and_the_rules_own_sale_on_the_same_day_do_not_contradict(tmp_path):
    sim = Sim(tmp_path)
    runner = sim.run_day(D1)
    sim.ibs[D2] = 0.0                                                                # the extra share is bought
    runner = sim.run_day(D2, runner)
    held = sim.fb.positions[PRODUCT]
    session = sim.session(D3)
    odd = session.open + dt.timedelta(hours=2)
    real_account = sim.fb.get_account

    def account():
        current = real_account()
        if sim.clock.now == odd:                                                     # one reading that does not add up
            return vb.Account(**{**current.__dict__, "equity": current.equity * 1.08})
        return current
    sim.fb.get_account = account
    before = len(sim.texts())
    sim.run_day(D3, runner)                                                          # no fresh dip: the share expires
    today = sim.texts()[before:]
    doubts = [text for text in sim.alerts() if text == DOUBT]
    assert len(doubts) == 1
    assert [o.side for o in sim.orders()][-1] == "sell" and sim.fb.positions[PRODUCT] < held
    digest = [text for text in today if "Gün sonu özeti (" in text]
    assert len(digest) == 1 and "Düşüş için tuttuğum ek payı sattım." in digest[0]
    assert not [text for text in today if "diye satış yapmadım" in text]             # nothing says "I did not sell"
    assert_only_known_sentences(sim)


def test_the_doubt_sentence_is_the_only_one_for_every_doubt():
    assert vn.build_alert("DATA_DOUBT") == DOUBT


# ============================================================================ T4
def odd_reading_at(sim, moment):
    real_account = sim.fb.get_account

    def account():
        current = real_account()
        if sim.clock.now == moment:                                                  # one reading that does not add up
            return vb.Account(**{**current.__dict__, "equity": current.equity * 1.08})
        return current
    sim.fb.get_account = account


def test_no_doubt_sentence_while_the_engine_is_halted(tmp_path):
    sim = Sim(tmp_path, cash="250", qty="5", price="50", peak=1300.0)                # 500 is 61.5 % below 1300
    runner = sim.run_day(D1)
    assert sim.rails().floor_state() == "HALT" and PRODUCT not in sim.fb.positions   # the safety sale was made
    done = [text for text in sim.alerts() if "Elimdeki Nasdaq fonunun tamamını sattım ve durdum." in text]
    assert len(done) == 1
    odd_reading_at(sim, sim.session(D2).open + dt.timedelta(hours=2))
    sim.run_day(D2, runner)
    assert not [text for text in sim.texts() if "Hesap değeri tutarsız göründü" in text]
    assert not [text for text in sim.texts() if "güvenlik satışı yapmadım" in text]  # it was made, on D1
    assert sim.digests()[-1].split("\n")[6].startswith("Durum: Hesap en yüksek değerinden")
    assert sim.rails().floor_state() == "HALT" and len(sim.submits()) == 1
    assert_only_known_sentences(sim)


def test_the_same_odd_reading_without_a_halt_is_told_once(tmp_path):
    sim = Sim(tmp_path)
    odd_reading_at(sim, sim.session(D1).open + dt.timedelta(hours=2))
    sim.run_day(D1)
    assert [text for text in sim.alerts() if text.startswith("Hesap değeri tutarsız göründü")] == [DOUBT]
    assert sim.rails().floor_state() == "OK"
    assert_only_known_sentences(sim)


# ============================================================================ T2
def test_a_corrected_close_names_its_summary_by_date_after_a_weekend(tmp_path):
    sim = Sim(tmp_path)
    sim.clock.now = dt.datetime(2026, 11, 20, 13, 0, tzinfo=UTC)
    w = window(sim, FRIDAY)

    def one_print(t, runner):
        if t == w.reconcile_from + dt.timedelta(seconds=10):
            sim.fb.set_price(PRODUCT, "310")                                         # the closing reading is absurd
        elif t == w.reconcile_from + dt.timedelta(seconds=70):
            sim.fb.set_price(PRODUCT, "50")
    runner = sim.run_day(FRIDAY, hook=one_print)
    true_close = float(sim.fb.equity)
    sim.fb.balance_asof = FRIDAY                                                     # on Monday the broker names Friday
    sim.run_day(MONDAY, runner)
    told = [text for text in sim.alerts() if "özetindeki hesap değeri yanlış okunmuş" in text]
    assert told == [
        "Gün sonu özetindeki hesap değeri yanlış okunmuş (20.11.2026). Doğru kapanış değeri %s dolar."
        % vn.fmt_money(true_close)
    ]
    assert not [text for text in sim.texts() if "Dünkü özetteki" in text]            # Friday is not "yesterday"
    assert_only_known_sentences(sim)


def test_the_corrected_close_sentence_needs_its_date():
    text = vn.build_alert("CLOSE_CORRECTED", date=dt.date(2026, 11, 25), equity=1234.5)
    assert text == "Gün sonu özetindeki hesap değeri yanlış okunmuş (25.11.2026). Doğru kapanış değeri 1.234,50 dolar."
    with pytest.raises(KeyError):
        vn.build_alert("CLOSE_CORRECTED", equity=1234.5)                             # never a half-filled sentence


# ============================================================================ T3
def health_json(sim, monkeypatch, capsys):
    monkeypatch.setattr(vh, "load_settings", lambda: sim.settings)
    code = vh.main(["--json"])
    return code, json.loads(capsys.readouterr().out)


def fresh_beat(sim):
    """A healthy heartbeat written "now" (the health tool reads the wall clock)."""
    vh.write_heartbeat(sim.settings.state_dir, {
        "time": dt.datetime.now(UTC).isoformat(), "mode": "paper", "package": "high", "phase": "closed",
        "session": None, "last_decision": None, "states": [], "readiness": {"ok": True, "problems": []},
        "ntfy_configured": True, "orders_enabled": True, "outbox_oldest_minutes": None,
    })


def test_the_health_tool_answers_even_when_the_message_file_cannot_be_read(tmp_path, monkeypatch, capsys):
    sim = Sim(tmp_path)
    fresh_beat(sim)
    code, healthy = health_json(sim, monkeypatch, capsys)
    assert code == 0 and healthy["lines"] == ["Healthy."]
    assert healthy["owner_unreachable"] is False and isinstance(healthy["heartbeat_age_seconds"], float)

    def broken(self, now):
        raise PermissionError("outbox.jsonl")
    monkeypatch.setattr(vn.Outbox, "owner_unreachable", broken)
    code, report = health_json(sim, monkeypatch, capsys)
    assert isinstance(report["heartbeat_age_seconds"], float) and report["heartbeat_age_seconds"] < 300
    assert report["owner_unreachable"] is True                                       # it cannot be shown otherwise
    assert code >= 1 and report["level"] == code
    assert "The owner message file could not be read." in report["lines"]
    assert "Healthy." not in report["lines"]
    assert report["heartbeat"]["mode"] == "paper"


def test_the_plain_health_output_survives_the_same_fault(tmp_path, monkeypatch, capsys):
    sim = Sim(tmp_path)
    fresh_beat(sim)

    def broken(self, now):
        raise OSError("outbox.jsonl")
    monkeypatch.setattr(vn.Outbox, "owner_unreachable", broken)
    monkeypatch.setattr(vh, "load_settings", lambda: sim.settings)
    code = vh.main([])
    out = capsys.readouterr().out.splitlines()
    assert code >= 1 and out[0] in ("degraded", "blocked")
    assert "The owner message file could not be read." in out
