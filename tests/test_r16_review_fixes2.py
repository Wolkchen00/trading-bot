"""R15/R16 kod incelemesi , IKINCI tur duzeltmelerin kaniti.

Codex ilk 14 bulguyu dogruladiktan sonra 14 yeni bulgu daha cikardi; ikisi
DUZELTMELERIN KENDISINDEYDI:
  - prefetch_due bayat sembolu seciyor ama get_company_overview onu ANINDA geri
    veriyordu: "canlandirilan" imlec hala hicbir seyi tazelemiyordu.
  - On-cekim ANA ISLEM DONGUSUNDE butun butceyi harcayabiliyordu: 12 cagri x
    15 sn uyku = ~6 dakika bloklama, acik pozisyonlarin korumasi gecikirdi.

Her test, duzeltme geri alinirsa DUSER.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from core.av_quota import AVQuotaStore, ReserveReason
from core.fundamentals_cache import FundamentalsCache

T0 = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)


def _store(tmp_path, budget=12, now=T0, reserve=0):
    return AVQuotaStore(
        path=str(tmp_path / "q.json"), budget=budget, profile="paper",
        now_fn=lambda: now, earnings_reserve=reserve,
    )


def _cache(tmp_path, now=T0):
    return FundamentalsCache(
        path=str(tmp_path / "c.json"), ttl_hours=24, max_stale_hours=168,
        now_fn=lambda: now,
    )


class _Yanit:
    def __init__(self, sym):
        self.status_code = 200
        self._s = sym
        self.text = json.dumps({"Symbol": sym})

    def json(self):
        return {"Symbol": self._s, "PERatio": "20", "EPS": "5"}


def _analyzer(tmp_path, monkeypatch, budget=12, now=T0, cache=None):
    monkeypatch.setenv("ALPHA_VANTAGE_KEY", "test")
    import core.fundamental_analyzer as fa
    from core.funnel import DailyFunnel
    return fa.FundamentalAnalyzer(
        quota=_store(tmp_path, budget=budget, now=now),
        disk_cache=cache if cache is not None else _cache(tmp_path, now=now),
        funnel=DailyFunnel(path=str(tmp_path / "f.json")),
    )


# ======================================================================
# BULGU: prefetch bayat veriyi "basarili" sayip HICBIR SEYI tazelemiyordu
# ======================================================================

def test_prefetch_BAYAT_sembolu_GERCEKTEN_tazeliyor(tmp_path, monkeypatch):
    """En agir ikinci-tur bulgusu: imlec bayat sembolu seciyordu ama
    get_company_overview bayat yuku aninda geri donduruyor, "basarili"
    sayiliyor ve zaman damgasi HIC ilerlemiyordu.
    """
    import core.fundamental_analyzer as fa
    yol = str(tmp_path / "c.json")
    FundamentalsCache(path=yol, ttl_hours=24, max_stale_hours=168,
                      now_fn=lambda: T0).put("AAPL", {"symbol": "AAPL", "pe_ratio": 9})

    sonra = T0 + timedelta(hours=48)          # AAPL artik BAYAT
    c = FundamentalsCache(path=yol, ttl_hours=24, max_stale_hours=168,
                          now_fn=lambda: sonra)
    a = _analyzer(tmp_path, monkeypatch, now=sonra, cache=c)

    cagrilan = []
    monkeypatch.setattr(
        fa.requests, "get",
        lambda url, params=None, **_k: (
            cagrilan.append((params or {}).get("symbol")) or _Yanit((params or {}).get("symbol"))
        ),
    )
    monkeypatch.setattr(fa.time, "sleep", lambda *_a: None)

    rapor = a.prefetch_due(["AAPL"])
    assert cagrilan == ["AAPL"], f"bayat sembol icin AG CAGRISI YAPILMADI: {cagrilan}"
    assert rapor["basarili"] == 1
    # Zaman damgasi ILERLEMIS olmali , artik TAZE
    _, yas, bolge = c.get("AAPL")
    assert bolge == "TAZE", f"tazeleme sonrasi hala {bolge}"


def test_force_refresh_bellek_cachei_de_atliyor(tmp_path, monkeypatch):
    import core.fundamental_analyzer as fa
    a = _analyzer(tmp_path, monkeypatch)
    cagri = {"n": 0}
    monkeypatch.setattr(
        fa.requests, "get",
        lambda url, params=None, **_k: (
            cagri.__setitem__("n", cagri["n"] + 1) or _Yanit((params or {}).get("symbol"))
        ),
    )
    monkeypatch.setattr(fa.time, "sleep", lambda *_a: None)

    a.get_company_overview("AAPL")                       # 1. cagri, cache'ler
    a.get_company_overview("AAPL")                       # cache hit, cagri yok
    assert cagri["n"] == 1
    a.get_company_overview("AAPL", force_refresh=True)   # zorla
    assert cagri["n"] == 2, "force_refresh bellek cache'ini atlamadi"


# ======================================================================
# BULGU: on-cekim ANA DONGUYU ~6 dakika bloklayabiliyordu
# ======================================================================

def test_prefetch_tur_basina_TAVANLI(tmp_path, monkeypatch):
    """Butun butceyi tek turda harcamak gercek parada kabul edilemez.

    Ana dongu acik pozisyonlarin stop/koruma yonetimini yapiyor; 12 cagri x
    15 sn uyku onu ~6 dakika geciktirirdi.
    """
    import core.fundamental_analyzer as fa
    a = _analyzer(tmp_path, monkeypatch, budget=12)
    cagrilan = []
    monkeypatch.setattr(
        fa.requests, "get",
        lambda url, params=None, **_k: (
            cagrilan.append((params or {}).get("symbol")) or _Yanit((params or {}).get("symbol"))
        ),
    )
    monkeypatch.setattr(fa.time, "sleep", lambda *_a: None)

    evren = [f"SYM{i:02d}" for i in range(12)]
    a.prefetch_due(evren)
    tavan = a._prefetch_max_per_round()
    assert tavan <= 3, f"tur basina tavan cok yuksek: {tavan}"
    assert len(cagrilan) == tavan, (
        f"tur basina {len(cagrilan)} cagri yapildi, tavan {tavan}"
    )


def test_prefetch_gun_boyunca_yine_tum_evreni_kapsiyor(tmp_path, monkeypatch):
    """Tavan gunluk kapsamayi bozmamali , sadece tek tura yaymamali."""
    import core.fundamental_analyzer as fa
    yol = str(tmp_path / "c.json")
    evren = [f"SYM{i:02d}" for i in range(6)]
    monkeypatch.setattr(fa.time, "sleep", lambda *_a: None)

    gorulen = set()
    q = _store(tmp_path, budget=12)
    for _tur in range(3):
        c = FundamentalsCache(path=yol, ttl_hours=24, max_stale_hours=168,
                              now_fn=lambda: T0)
        from core.funnel import DailyFunnel
        a = fa.FundamentalAnalyzer(quota=q, disk_cache=c,
                                   funnel=DailyFunnel(path=str(tmp_path / "f.json")))
        monkeypatch.setattr(
            fa.requests, "get",
            lambda url, params=None, **_k: (
                gorulen.add((params or {}).get("symbol"))
                or _Yanit((params or {}).get("symbol"))
            ),
        )
        a.prefetch_due(evren)
    assert len(gorulen) == 6, f"3 turda evren kapanmadi: {sorted(gorulen)}"


# ======================================================================
# BULGU: bozuk sayac KALICI kilitleniyordu (is_exhausted onarmiyordu)
# ======================================================================

def test_bozuk_sayac_is_exhausted_yolundan_da_iyilesiyor(tmp_path):
    """Uretimde is_exhausted() try_reserve'den ONCE cagriliyor.

    Onarim yalniz try_reserve'de olsaydi hic tetiklenmez ve fail-closed KALICI
    kilitlenmeye donusurdu.
    """
    p = tmp_path / "q.json"
    p.write_text("bozuk degil bu json", encoding="utf-8")

    bugun = AVQuotaStore(path=str(p), budget=12, profile="paper",
                         now_fn=lambda: T0, earnings_reserve=0)
    assert bugun.is_exhausted() is True          # onarim kaydini YAZMALI

    yarin = AVQuotaStore(path=str(p), budget=12, profile="paper",
                         now_fn=lambda: T0 + timedelta(days=1), earnings_reserve=0)
    assert yarin.is_exhausted() is False, "ertesi gun iyilesmedi"
    assert yarin.try_reserve("fundamental") is True


# ======================================================================
# BULGU: try_reserve dort sebebi tek False'a cokertiyordu
# ======================================================================

def test_rezervasyon_sebepleri_ayrisiyor(tmp_path, monkeypatch):
    """Her depo AYRI dosya , paylasilan dosya sebepleri birbirine karistirir."""
    def depo(ad, butce=5, rezerv=0):
        return AVQuotaStore(path=str(tmp_path / ad), budget=butce,
                            profile="paper", now_fn=lambda: T0,
                            earnings_reserve=rezerv)

    q = depo("q1.json", butce=1)
    assert q.reserve("fundamental") == (True, ReserveReason.OK)
    assert q.reserve("fundamental")[1] is ReserveReason.BUDGET_EXHAUSTED

    q2 = depo("q2.json")
    q2.mark_exhausted()
    assert q2.reserve("fundamental")[1] is ReserveReason.PROVIDER_EXHAUSTED

    q3 = depo("q3.json", butce=12, rezerv=12)
    assert q3.reserve("fundamental")[1] is ReserveReason.EARNINGS_RESERVED

    q4 = depo("q4.json")
    monkeypatch.setattr(AVQuotaStore, "_yaz", lambda self, k: False)
    assert q4.reserve("fundamental")[1] is ReserveReason.WRITE_FAILED


def test_gecici_red_sembolu_gun_boyu_susturmuyor(tmp_path, monkeypatch):
    """Kilit/yazma hatasi KOTA TUKENMESI DEGILDIR , negatif cache yazilmamali."""
    import core.fundamental_analyzer as fa
    c = _cache(tmp_path)
    a = _analyzer(tmp_path, monkeypatch, cache=c)
    monkeypatch.setattr(AVQuotaStore, "_yaz", lambda self, k: False)
    monkeypatch.setattr(fa.requests, "get", lambda *_a, **_k: pytest.fail("ag"))

    assert a.get_company_overview("AAPL") is None
    assert c.is_negative_cached("AAPL") is False, (
        "gecici yazma hatasi sembolu gun boyu susturdu"
    )


def test_gercek_kota_tukenmesi_negatif_cacheleniyor(tmp_path, monkeypatch):
    """Kontrol testi: GERCEK tukenmede negatif cache YAZILMALI."""
    import core.fundamental_analyzer as fa
    c = _cache(tmp_path)
    a = _analyzer(tmp_path, monkeypatch, budget=1, cache=c)
    a.quota.try_reserve("earnings")     # butceyi tuket
    monkeypatch.setattr(fa.requests, "get", lambda *_a, **_k: pytest.fail("ag"))
    assert a.get_company_overview("AAPL") is None
    assert c.is_negative_cached("AAPL") is True


# ======================================================================
# BULGU: tukenmeyi yalniz FundamentalAnalyzer isaretliyordu
# ======================================================================

def test_haber_yolu_da_tukenmeyi_isaretliyor(tmp_path, monkeypatch):
    import core.news_analyzer as na
    q = _store(tmp_path, budget=5)
    monkeypatch.setattr(na, "shared_store", lambda: q)

    class _Kota:
        status_code = 200
        text = json.dumps({"Note": "Thank you for using Alpha Vantage!"})
        def json(self): return {"Note": "x"}

    n = na.StockNewsAnalyzer()
    n.alpha_vantage_key = "test"
    monkeypatch.setattr(na.requests, "get", lambda *_a, **_k: _Kota())
    monkeypatch.setattr(na.time, "sleep", lambda *_a: None)

    n._fetch_alpha_vantage_news("AAPL")
    assert q.is_exhausted() is True, "haber yolu tukenmeyi isaretlemedi"


def test_takvim_yolu_da_tukenmeyi_isaretliyor(tmp_path, monkeypatch):
    import core.earnings_calendar as ec
    q = _store(tmp_path, budget=5)
    monkeypatch.setattr(ec, "shared_store", lambda: q)

    class _Kota:
        status_code = 200
        text = json.dumps({"Information": "higher API call volume"})

    cal = ec.EarningsCalendar()
    cal.alpha_vantage_key = "test"
    cal._fetched_at = None
    cal._last_attempt = datetime(2020, 1, 1)
    monkeypatch.setattr(ec.requests, "get", lambda *_a, **_k: _Kota())

    cal._refresh_if_needed()
    assert q.is_exhausted() is True, "takvim yolu tukenmeyi isaretlemedi"


# ======================================================================
# BULGU: sema dogrulamasi eski surumu ve butce degisimini kabul ediyordu
# ======================================================================

def test_eski_sema_surumu_reddediliyor(tmp_path):
    p = tmp_path / "q.json"
    p.write_text(json.dumps({
        "schema_version": 1, "utc_day": "2026-09-03", "profile": "paper",
        "budget": 12, "used": {"fundamental": 0, "news": 0, "earnings": 0},
        "total_used": 0,
    }), encoding="utf-8")
    q = AVQuotaStore(path=str(p), budget=12, profile="paper", now_fn=lambda: T0,
                     earnings_reserve=0)
    assert q.try_reserve("fundamental") is False, "eski sema taze butce verdi"


def test_butce_degisince_eski_sayac_reddediliyor(tmp_path):
    """Config'de butce degistiyse eski sayac anlamsizdir."""
    p = tmp_path / "q.json"
    p.write_text(json.dumps({
        "schema_version": 2, "utc_day": "2026-09-03", "profile": "paper",
        "budget": 25, "used": {"fundamental": 0, "news": 0, "earnings": 0},
        "total_used": 0, "exhausted_day": None,
    }), encoding="utf-8")
    q = AVQuotaStore(path=str(p), budget=12, profile="paper", now_fn=lambda: T0,
                     earnings_reserve=0)
    assert q.try_reserve("fundamental") is False


def test_sema_surumu_yoksa_reddediliyor(tmp_path):
    p = tmp_path / "q.json"
    p.write_text(json.dumps({
        "utc_day": "2026-09-03", "profile": "paper", "budget": 12,
        "used": {"fundamental": 0, "news": 0, "earnings": 0}, "total_used": 0,
    }), encoding="utf-8")
    q = AVQuotaStore(path=str(p), budget=12, profile="paper", now_fn=lambda: T0,
                     earnings_reserve=0)
    assert q.try_reserve("fundamental") is False
