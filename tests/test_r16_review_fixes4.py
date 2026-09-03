"""Codex'in yarim kalan ucuncu turundan cikan iki iz.

Codex haftalik kotasina takilmadan once iki sey soyledi:
  1. "on-cekim tasinmasinin KANITI cagriyi gercekten CLOSED dalina sabitliyor mu"
  2. "kazanc rezervi 'basariyla kaydedildi'nin ne demek olduguna bagli, sadece
      KAYNAK SIRASINA bakmak yetmez"

Ikisi de hakliydi: o iki testim kaynak metnine bakiyordu, DAVRANISA degil.
Bu dosya ikisini de davranis testine cevirir.
"""
from __future__ import annotations

import io
import json
import os
from datetime import datetime, timezone

import pytest

from core.av_quota import AVQuotaStore

KOK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
T0 = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)


def _store(tmp_path, budget=12, reserve=0):
    return AVQuotaStore(
        path=str(tmp_path / "q.json"), budget=budget, profile="paper",
        now_fn=lambda: T0, earnings_reserve=reserve,
    )


# ======================================================================
# IZ 1: on-cekim GERCEKTEN islem yolundan cikti mi
# ======================================================================

def test_prefetch_islem_tarama_yolunda_CAGRILMIYOR():
    """Tasima kanitinin kalbi: cagri artik sembol tarama yolunda OLMAMALI.

    Kaynak metninde "prefetch_due var mi" aramak yetmez , nerede oldugu onemli.
    Sembol dongusunun basladigi satirdan onceki blokta OLMAMALI.
    """
    kaynak = io.open(os.path.join(KOK, "stock_bot.py"), encoding="utf-8").read()
    satirlar = kaynak.splitlines()

    # Sembol tarama dongusunun yeri
    tarama = [i for i, l in enumerate(satirlar)
              if l.strip().startswith("for symbol in symbols:")]
    assert tarama, "sembol tarama dongusu bulunamadi"
    tarama_i = tarama[0]

    # Ondan onceki 40 satirda prefetch cagrisi OLMAMALI
    pencere = "\n".join(satirlar[max(0, tarama_i - 40):tarama_i])
    assert "prefetch_due" not in pencere, (
        "on-cekim hala islem tarama yolunda , gecikme garantisi yok"
    )


def test_prefetch_PIYASA_KAPALI_dalinda_cagriliyor():
    """Cagri CLOSED dalinda olmali; orada yonetilecek dolum yok."""
    kaynak = io.open(os.path.join(KOK, "stock_bot.py"), encoding="utf-8").read()
    satirlar = kaynak.splitlines()

    kapali = [i for i, l in enumerate(satirlar)
              if 'market_status["status"] == "CLOSED"' in l]
    assert kapali, "CLOSED dali bulunamadi"
    bas = kapali[0]
    # CLOSED dalinin govdesi: `continue`e kadar
    son = bas
    for i in range(bas + 1, min(len(satirlar), bas + 40)):
        if satirlar[i].strip() == "continue":
            son = i
            break
    assert son > bas, "CLOSED dalinin sonu bulunamadi"
    govde = "\n".join(satirlar[bas:son + 1])
    assert "_prefetch_fundamentals_if_idle" in govde, (
        "on-cekim CLOSED dalinda cagrilmiyor"
    )


def test_prefetch_yardimcisi_aralik_kapisini_uyguluyor(monkeypatch):
    """CLOSED dali ~60 saniyede bir kosuyor; her turda aga gitmemeli."""
    import stock_bot as sb

    class _Sahte:
        pass

    bot = _Sahte.__new__(_Sahte)
    cagri = {"n": 0}

    class _FA:
        def prefetch_due(self, evren, limit=None):
            cagri["n"] += 1
            return {"denenen": 0, "basarili": 0, "kapsama": {}}

    bot.fundamental_analyzer = _FA()
    yardimci = sb.StockBot._prefetch_fundamentals_if_idle

    yardimci(bot)                     # ilk cagri kosmali
    yardimci(bot)                     # aralik dolmadi, kosmamali
    yardimci(bot)
    assert cagri["n"] == 1, f"aralik kapisi yok, {cagri['n']} kez kostu"


def test_prefetch_yardimcisi_arizada_yukselmiyor(monkeypatch):
    """Best-effort: on-cekim arizasi ana donguyu durduramaz."""
    import stock_bot as sb

    class _Sahte:
        pass

    bot = _Sahte.__new__(_Sahte)

    class _FA:
        def prefetch_due(self, evren, limit=None):
            raise RuntimeError("on-cekim patladi")

    bot.fundamental_analyzer = _FA()
    with pytest.raises(RuntimeError):
        sb.StockBot._prefetch_fundamentals_if_idle(bot)
    # NOT: yardimci kendi icinde yutmuyor , cagri YERI try/except ile sariyor.
    # Cagri yerinin sardigini dogrula:
    kaynak = io.open(os.path.join(KOK, "stock_bot.py"), encoding="utf-8").read()
    i = kaynak.index("_prefetch_fundamentals_if_idle()")
    onceki = kaynak[max(0, i - 200):i]
    assert "try:" in onceki, "cagri yeri try/except ile sarilmamis"


# ======================================================================
# IZ 2: kazanc rezervi GERCEK kaliciliga bagli mi (kaynak sirasina degil)
# ======================================================================

def test_disk_yazimi_BASARISIZKEN_rezerv_BIRAKILMIYOR(tmp_path, monkeypatch):
    """Codex'in izi: siralama duzeltmesi KOZMETIKTI.

    _save_disk_cache istisnayi yutup None donuyordu, yani cagiran basarisizligi
    goremiyordu ve rezerv yine birakiliyordu. Restart ise bayat/eksik takvim
    buluyor, earnings_gate fail-open'a dusuyordu.
    """
    import core.earnings_calendar as ec
    q = _store(tmp_path, budget=12, reserve=1)
    monkeypatch.setattr(ec, "shared_store", lambda: q)

    csv = "symbol,name,reportDate\nAAPL,Apple Inc,2026-10-01\n"

    class _Csv:
        status_code = 200
        text = csv

    cal = ec.EarningsCalendar()
    cal.alpha_vantage_key = "test"
    cal._fetched_at = None
    cal._last_attempt = datetime(2020, 1, 1)
    monkeypatch.setattr(ec.requests, "get", lambda *_a, **_k: _Csv())
    # DISK YAZIMI BASARISIZ
    monkeypatch.setattr(ec.EarningsCalendar, "_save_disk_cache", lambda self: False)

    cal._refresh_if_needed()

    anlik = q.snapshot()
    assert anlik.get("earnings_refreshed_day") is None, (
        "disk yazimi basarisizken rezerv birakildi"
    )


def test_disk_yazimi_BASARILIYKEN_rezerv_birakiliyor(tmp_path, monkeypatch):
    """Kontrol testi: gercek basarida rezerv SERBEST kalmali."""
    import core.earnings_calendar as ec
    q = _store(tmp_path, budget=12, reserve=1)
    monkeypatch.setattr(ec, "shared_store", lambda: q)

    class _Csv:
        status_code = 200
        text = "symbol,name,reportDate\nAAPL,Apple Inc,2026-10-01\n"

    cal = ec.EarningsCalendar()
    cal.alpha_vantage_key = "test"
    cal._fetched_at = None
    cal._last_attempt = datetime(2020, 1, 1)
    cal._cache_file = str(tmp_path / "earnings.json")
    monkeypatch.setattr(ec.requests, "get", lambda *_a, **_k: _Csv())

    cal._refresh_if_needed()

    anlik = q.snapshot()
    assert anlik.get("earnings_refreshed_day") is not None, (
        "gercek basarida rezerv birakilmadi"
    )


def test_save_disk_cache_basariyi_DONDURUYOR(tmp_path):
    """Istisnayi yutup None donmek, cagiranin basariyi gormesini engelliyordu."""
    import core.earnings_calendar as ec
    cal = ec.EarningsCalendar()
    cal._cache_file = str(tmp_path / "ok.json")
    cal._fetched_at = datetime.now()
    cal._calendar = {"AAPL": "2026-10-01"}
    assert cal._save_disk_cache() is True

    # Yazilamayan yol -> False
    cal._cache_file = str(tmp_path / "olmayan_dizin" / "x" / "y.json")
    assert cal._save_disk_cache() is False
