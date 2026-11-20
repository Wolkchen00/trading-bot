from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from v5.notify import (
    Outbox,
    Notifier,
    TEMPLATES,
    build_alert,
    build_digest,
    cause_for,
    fmt_money,
    fmt_pct,
    fmt_weight,
)


NOW = datetime(2026, 11, 16, 21, tzinfo=timezone.utc)


EXPECTED_TEMPLATES = {
    "DRAWDOWN_ALERT": "Hesap en yüksek değerinden yüzde 37,1 aşağıda, şu an 512,30 dolar. Bu risk seviyesinde geçmişte görülen en derin düşüşe geldik. Kurala göre çalışmaya devam ediyorum. Durdurmak istersen Claude'a 'botu durdur' yaz.",
    "BREAKER": "Hesap bugün yüzde 15,0 düştü. Bugün yeni alım yapmıyorum. Yarın normal çalışmaya dönerim. Senden bir şey beklemiyorum.",
    "FLOOR_DONE": "Hesap en yüksek değerinden yüzde 60,0 düştü, şu an 196,00 dolar. Elimdeki Nasdaq fonunun tamamını sattım ve durdum. Devam etmemi istersen Claude'a 'botu devam ettir' yaz.",
    "FLOOR_PENDING": "Hesap en yüksek değerinden yüzde 60,0 düştü, şu an 196,00 dolar. Satışa başladım ama henüz bitmedi. Şu an elimde yüzde 9 Nasdaq fonu var. Denemeye devam ediyorum.",
    "RAIL_LIQUIDATING": "Güvenlik satışı hala bitmedi. Şu an elimde yüzde 9 Nasdaq fonu var. Denemeye devam ediyorum. Claude'a 'trading botuna bak' yazarsan inceler.",
    "HOLD_NOT_READY": "Bugün karar veremedim, çünkü fiyat bilgisine ulaşamadım. Elimdekilere dokunmadım. Yarın yeniden denerim.",
    "MISSED": "Bugünkü karar saatini kaçırdım. Elimdekilere dokunmadım. Yarın normal devam ederim.",
    "ORDER_FAILED": "Alpaca emrimi kabul etmedi. Şu an elimde yüzde 9 Nasdaq fonu var. Claude'a 'trading botuna bak' yazarsan sebebini inceler.",
    "ORDER_PARTIAL": "Emrimin tamamı gerçekleşmedi. Şu an elimde yüzde 9 Nasdaq fonu var. Yarın duruma yeniden bakarım.",
    "UNRESOLVED": "Son emrimin akıbetini öğrenemedim. Netleşene kadar yeni işlem yapmıyorum. Claude'a 'trading botuna bak' yazarsan inceler.",
    "FOREIGN_ASSET": "Hesapta benim almadığım bir varlık ya da emir var (XYZ). Karışmamak için işlem yapmıyorum. Bunu sen yaptıysan Claude'a haber ver.",
    "DATA_DOUBT": "Hesap değeri tutarsız göründü. Yanlış alarm olmasın diye satış yapmadım, izlemeye devam ediyorum.",
    "PAUSED": "Bot duraklatılmış durumda. Elimdekiler duruyor, yeni işlem yapmıyorum.",
    "NOT_STARTED": "Bot başlayamadı. Claude'a 'trading botuna bak' yaz.",
    "WATCHDOG": "Trading botu çalışmıyor görünüyor. Claude'a 'trading botuna bak' yaz.",
    "READINESS": "Bugün borsa açılmadan önce bir sorun gördüm: fiyat bilgisine ulaşamadım. Düzelmezse bugün işlem yapmayacağım.",
    "ERROR": "Bot beklenmeyen bir sorunla karşılaştı. Claude'a 'trading botuna bak' yaz.",
    "TEST": "Deneme bildirimi: bu mesajı görüyorsan bildirimler çalışıyor.",
}


def fields(kind):
    return {
        "DRAWDOWN_ALERT": {"pct": 0.371, "equity": 512.3},
        "BREAKER": {"pct": 0.15},
        "FLOOR_DONE": {"pct": 0.60, "equity": 196},
        "FLOOR_PENDING": {"pct": 0.60, "equity": 196, "product_pct": 0.094},
        "RAIL_LIQUIDATING": {"product_pct": 0.094},
        "HOLD_NOT_READY": {"cause": "fiyat bilgisine ulaşamadım"},
        "ORDER_FAILED": {"product_pct": 0.094},
        "ORDER_PARTIAL": {"product_pct": 0.094},
        "FOREIGN_ASSET": {"symbol": "XYZ"},
        "READINESS": {"cause": "fiyat bilgisine ulaşamadım"},
    }.get(kind, {})


@pytest.mark.parametrize("kind", sorted(EXPECTED_TEMPLATES))
def test_every_template_literal_snapshot(kind):
    assert build_alert(kind, **fields(kind)) == EXPECTED_TEMPLATES[kind]


def test_number_formats_and_causes():
    assert [fmt_money(x) for x in (1234.5, 512.3, 0.004, -1.005)] == ["1.234,50", "512,30", "0,00", "1,01"]
    assert [fmt_pct(x) for x in (0.0123, -0.0123, 0.37, 0.0005)] == ["1,2", "1,2", "37,0", "0,1"]
    assert fmt_weight(0.094) == "9" and fmt_weight(0.095) == "10"
    assert cause_for("BROKER_DOWN") == "Alpaca'ya bağlanamadım"
    assert cause_for("other") == "beklenmeyen bir sorun çıktı"
    with pytest.raises(KeyError):
        build_alert("UNKNOWN")
    with pytest.raises(KeyError):
        build_alert("BREAKER")
    assert build_alert("TEST", pct=None, cause=None) == EXPECTED_TEMPLATES["TEST"]


def context(**changes):
    result = {
        "date": date(2026, 11, 16), "prefix": "", "equity": 512.3,
        "day_usd": 12.3, "day_pct": 0.0246, "product_weight": 0.63,
        "action": "buy", "action_usd": 20, "reason_keys": ["TREND_UP", "VOL_CALM"],
        "month_pct": 0.12, "total_pct": -0.03, "status_text": None,
    }
    result.update(changes)
    return result


def test_digest_literal_and_variants():
    expected = "\n".join([
        "Gün sonu özeti (16.11.2026)",
        "Hesap: 512,30 dolar. Bugün yüzde 2,5 arttı (12,30 dolar).",
        "Elimde: yüzde 63 Nasdaq fonu, yüzde 37 nakit.",
        "Bugün ne yaptım: 20,00 dolarlık Nasdaq fonu aldım.",
        "Neden: Nasdaq yükseliş eğiliminde. Piyasa sakin.",
        "Bu ay: yüzde 12,0 artıda. Başlangıçtan beri: yüzde 3,0 ekside.",
        "Durum: Her şey normal.",
    ])
    assert build_digest(context()) == expected
    down = build_digest(context(prefix="paper", day_usd=-2, day_pct=-0.004, action="sell", action_usd=2, month_pct=-0.1, total_pct=0.2))
    assert down.startswith("DENEME HESABI. Gün sonu özeti")
    assert "Bugün yüzde 0,4 azaldı (2,00 dolar)." in down
    assert "2,00 dolarlık Nasdaq fonu sattım." in down
    assert "Bu ay: yüzde 10,0 ekside. Başlangıçtan beri: yüzde 20,0 artıda." in down
    flat = build_digest(context(prefix="dry_run", day_usd=0.004, day_pct=0, action="none", reason_keys=None, month_pct=None, total_pct=0, status_text="Bot duraklatılmış durumda."))
    assert "Bugün değişmedi." in flat and "İşlem yapmadım." in flat
    assert "Neden: Bugün karar veremedim." in flat
    assert "Bu ay: bilgi yok. Başlangıçtan beri: değişim yok." in flat
    assert flat.endswith("Durum: Bot duraklatılmış durumda.")
    assert "Elimdeki fonun tamamını sattım." in build_digest(context(action="sell_all"))
    with pytest.raises(ValueError):
        build_digest(context(prefix="bad"))
    with pytest.raises(ValueError):
        build_digest(context(action="bad"))
    missing = context(); del missing["equity"]
    with pytest.raises(KeyError):
        build_digest(missing)


def test_outbox_durability_backoff_expiry_dedupe_and_corrupt(tmp_path):
    box = Outbox(tmp_path)
    identifier = box.add("TEST", "hello", NOW, dedupe_key="one")
    size = box.path.stat().st_size
    assert box.add("TEST", "other", NOW, dedupe_key="one") == identifier
    assert box.path.stat().st_size == size
    assert Outbox(tmp_path).pending(NOW)[0]["id"] == identifier
    box.mark_attempt(identifier, NOW)
    assert box.pending(NOW + timedelta(seconds=59)) == []
    assert box.pending(NOW + timedelta(minutes=1))[0]["id"] == identifier
    box.mark_attempt(identifier, NOW + timedelta(minutes=1))
    assert box.pending(NOW + timedelta(minutes=2, seconds=59)) == []
    assert box.pending(NOW + timedelta(minutes=3))[0]["id"] == identifier
    assert box.pending(NOW + timedelta(hours=48)) == []
    before = box.path.stat().st_size
    with box.path.open("a", encoding="utf-8") as handle:
        handle.write("not json\n")
    assert box.corrupt_lines == 1 and box.path.stat().st_size > before


class Clock:
    def __init__(self, value=NOW): self.value = value
    def __call__(self): return self.value


def notifier(tmp_path, post, topic="secret-topic", clock=None):
    settings = SimpleNamespace(ntfy_topic=topic, ntfy_base_url="https://ntfy.invalid")
    return Notifier(settings, Outbox(tmp_path), post, clock or Clock())


def test_failed_posts_later_deliver_and_topic_never_raises(tmp_path):
    clock = Clock()
    calls = []
    outcomes = [500, RuntimeError("secret-topic"), 204]
    def post(url, data, headers, timeout):
        calls.append((url, data, headers, timeout))
        result = outcomes.pop(0)
        if isinstance(result, Exception): raise result
        return result
    note = notifier(tmp_path, post, clock=clock)
    identifier = note.alert("TEST")
    assert Outbox(tmp_path).pending(clock.value) == []
    clock.value += timedelta(minutes=1)
    assert note.flush() == 0
    clock.value += timedelta(minutes=2)
    assert note.flush() == 1
    assert Outbox(tmp_path).pending(clock.value) == []
    url, body, headers, timeout = calls[-1]
    assert url.endswith("/secret-topic") and body.decode("utf-8") == EXPECTED_TEMPLATES["TEST"]
    assert timeout == 10 and all(value.isascii() for value in headers.values())


def test_cooldown_no_topic_priorities_and_session_dedupe(tmp_path):
    clock = Clock()
    calls = []
    note = notifier(tmp_path, lambda *args: calls.append(args) or 204, topic="", clock=clock)
    assert not note.configured
    first = note.alert("NOT_STARTED", state_key="boot")
    assert note.alert("NOT_STARTED", state_key="boot") is None and calls == []
    message = Outbox(tmp_path).last("NOT_STARTED")
    assert message["id"] == first and message["priority"] == 5
    session = date(2026, 11, 16)
    assert note.reminder("PAUSED", session, state_key="pause") == note.reminder("PAUSED", session, state_key="pause")


def test_44_session_reminders_are_exactly_once(tmp_path):
    clock = Clock()
    note = notifier(tmp_path, lambda *args: 204, clock=clock)
    start = date(2026, 9, 1)
    for offset in range(44):
        session = start + timedelta(days=offset)
        note.reminder("NOT_STARTED", session, state_key="boot")
        note.reminder("NOT_STARTED", session, state_key="boot")
        clock.value += timedelta(days=1)
    assert len([m for m in Outbox(tmp_path)._messages() if m["kind"] == "NOT_STARTED"]) == 44
