"""R16 entegrasyon , gercek FundamentalAnalyzer / news / earnings yollari.

test_r16_fund_quota.py kota ve cache BIRIMLERINI kanitlar. Bu dosya uc AV
tuketicisinin GERCEK cagri yollarindan geciyor:
  (a) cache hit'te requests.get VE time.sleep cagri sayaci 0
  (e) AV haberi kapaliyken AV sayaci 0, Marketaux/Google News calisiyor
  (k) fund_source_quota kalici funnel ciktisinda GERCEKTEN gorunuyor
"""
from __future__ import annotations

import datetime as _dt
import json
import logging
from datetime import datetime, timedelta, timezone

import pytest

from core.av_quota import AVQuotaStore
from core.fundamentals_cache import FundamentalsCache

T0 = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)


def _store(tmp_path, budget=12, now=T0):
    return AVQuotaStore(
        path=str(tmp_path / "av_quota.json"), budget=budget,
        profile="paper", now_fn=lambda: now,
    )


def _cache(tmp_path, now=T0):
    return FundamentalsCache(
        path=str(tmp_path / "fund_cache.json"), ttl_hours=24,
        max_stale_hours=168, now_fn=lambda: now,
    )


def _analyzer(tmp_path, monkeypatch, budget=12, now=T0):
    monkeypatch.setenv("ALPHA_VANTAGE_KEY", "test-anahtar")
    import core.fundamental_analyzer as fa
    from core.funnel import DailyFunnel
    q = _store(tmp_path, budget=budget, now=now)
    c = _cache(tmp_path, now=now)
    f = DailyFunnel(path=str(tmp_path / "funnel.json"))
    return fa.FundamentalAnalyzer(quota=q, disk_cache=c, funnel=f), q, c, f


class _Yanit:
    def __init__(self, kod, govde, veri):
        self.status_code = kod
        self.text = govde
        self._veri = veri

    def json(self):
        return self._veri


# ======================================================================
# (a) CACHE HIT , NE AG NE UYKU
# ======================================================================

def test_a_disk_cache_hitinde_ag_ve_uyku_YOK(tmp_path, monkeypatch):
    """Restart sonrasi taze cache: ne requests.get ne time.sleep calismali.

    Eski kod bellek cache'ini restart'ta kaybediyor, her sembol icin AV'yi
    yeniden cagirip time.sleep(15) oduyordu.
    """
    import core.fundamental_analyzer as fa
    a, q, c, _ = _analyzer(tmp_path, monkeypatch)
    c.put("AAPL", {"symbol": "AAPL", "pe_ratio": 30})

    sayac = {"get": 0, "sleep": 0}
    monkeypatch.setattr(fa.requests, "get",
                        lambda *_a, **_k: sayac.__setitem__("get", sayac["get"] + 1))
    monkeypatch.setattr(fa.time, "sleep",
                        lambda *_a: sayac.__setitem__("sleep", sayac["sleep"] + 1))

    sonuc = a.get_company_overview("AAPL")
    assert sonuc is not None and sonuc["pe_ratio"] == 30
    assert sayac["get"] == 0, "cache hit'te ag cagrisi yapildi"
    assert sayac["sleep"] == 0, "cache hit'te uyunuldu"
    assert q.remaining() == 12, "cache hit'te kota harcandi"


def test_a_bayat_cache_yasi_karara_ilistiriliyor(tmp_path, monkeypatch):
    """Bayat veri kullanilir ama SESSIZCE taze gibi davranilmaz."""
    import core.fundamental_analyzer as fa
    from core.funnel import DailyFunnel
    monkeypatch.setenv("ALPHA_VANTAGE_KEY", "test-anahtar")

    yol = str(tmp_path / "fund_cache.json")
    FundamentalsCache(path=yol, ttl_hours=24, max_stale_hours=168,
                      now_fn=lambda: T0).put("AAPL", {"symbol": "AAPL", "pe_ratio": 30})

    sonra = T0 + timedelta(hours=48)
    a = fa.FundamentalAnalyzer(
        quota=_store(tmp_path, now=sonra),
        disk_cache=FundamentalsCache(path=yol, ttl_hours=24, max_stale_hours=168,
                                     now_fn=lambda: sonra),
        funnel=DailyFunnel(path=str(tmp_path / "f.json")),
    )
    monkeypatch.setattr(fa.requests, "get",
                        lambda *_a, **_k: pytest.fail("bayat cache'te ag cagrildi"))
    monkeypatch.setattr(fa.time, "sleep",
                        lambda *_a: pytest.fail("bayat cache'te uyunuldu"))

    sonuc = a.get_company_overview("AAPL")
    assert sonuc["is_stale"] is True
    assert 47 < sonuc["data_age_hours"] < 49


def test_a_kota_doluyken_ag_cagrisi_YOK(tmp_path, monkeypatch):
    import core.fundamental_analyzer as fa
    a, q, c, _ = _analyzer(tmp_path, monkeypatch, budget=1)
    assert q.try_reserve("earnings") is True

    sayac = {"get": 0, "sleep": 0}
    monkeypatch.setattr(fa.requests, "get",
                        lambda *_a, **_k: sayac.__setitem__("get", sayac["get"] + 1))
    monkeypatch.setattr(fa.time, "sleep",
                        lambda *_a: sayac.__setitem__("sleep", sayac["sleep"] + 1))

    assert a.get_company_overview("AAPL") is None
    assert sayac["get"] == 0, "kota doluyken ag cagrisi yapildi"
    assert sayac["sleep"] == 0


def test_a_negatif_cache_ikinci_cagriyi_engelliyor(tmp_path, monkeypatch):
    import core.fundamental_analyzer as fa
    a, q, c, _ = _analyzer(tmp_path, monkeypatch, budget=1)
    q.try_reserve("earnings")
    a.get_company_overview("AAPL")
    assert c.is_negative_cached("AAPL") is True

    sayac = {"get": 0}
    monkeypatch.setattr(fa.requests, "get",
                        lambda *_a, **_k: sayac.__setitem__("get", sayac["get"] + 1))
    assert a.get_company_overview("AAPL") is None
    assert sayac["get"] == 0


def test_a_gercek_cagrida_uyku_VAR_ve_diske_yaziliyor(tmp_path, monkeypatch):
    """Uyku KALDIRILMADI, yeri degisti: yalniz gercek ag cagrisindan sonra."""
    import core.fundamental_analyzer as fa
    a, q, c, _ = _analyzer(tmp_path, monkeypatch)

    veri = {"Symbol": "AAPL", "Name": "Apple", "PERatio": "30", "EPS": "6"}
    sayac = {"sleep": 0}
    monkeypatch.setattr(fa.requests, "get",
                        lambda *_a, **_k: _Yanit(200, json.dumps(veri), veri))
    monkeypatch.setattr(fa.time, "sleep",
                        lambda *_a: sayac.__setitem__("sleep", sayac["sleep"] + 1))

    sonuc = a.get_company_overview("AAPL")
    assert sonuc is not None and sonuc["symbol"] == "AAPL"
    assert sayac["sleep"] == 1, "gercek cagridan sonra uyku olmali"
    assert q.remaining() == 11, "kota harcanmali"
    _, _, bolge = c.get("AAPL")
    assert bolge == "TAZE", "disk cache'e yazilmadi, restart'i atlatmaz"


def test_a_gecici_hata_negatif_cachelenmiyor(tmp_path, monkeypatch):
    """HTTP 500 gecicidir: gun boyu susturulmamali."""
    import core.fundamental_analyzer as fa
    a, q, c, _ = _analyzer(tmp_path, monkeypatch)
    monkeypatch.setattr(fa.requests, "get", lambda *_a, **_k: _Yanit(500, "", {}))
    monkeypatch.setattr(fa.time, "sleep", lambda *_a: None)

    assert a.get_company_overview("AAPL") is None
    assert c.is_negative_cached("AAPL") is False


def test_a_http200_kota_govdesi_negatif_cacheleniyor(tmp_path, monkeypatch):
    """Kota tukenmesi HTTP 200 ile gelir; gun sonuna kadar cache'lenmeli."""
    import core.fundamental_analyzer as fa
    a, q, c, _ = _analyzer(tmp_path, monkeypatch)
    govde = json.dumps(
        {"Note": "Thank you for using Alpha Vantage! 25 requests per day"}
    )
    monkeypatch.setattr(fa.requests, "get",
                        lambda *_a, **_k: _Yanit(200, govde, {"Note": "x"}))
    monkeypatch.setattr(fa.time, "sleep", lambda *_a: None)

    assert a.get_company_overview("AAPL") is None
    assert c.is_negative_cached("AAPL") is True


# ======================================================================
# (k) FUNNEL ETIKETI GERCEKTEN KAYDEDILIYOR
# ======================================================================

def test_k_fund_source_quota_yutulmuyor(tmp_path, monkeypatch):
    """`fund_source_quota` DailyFunnel.STAGES'te YOK.

    Oldugu gibi bump edilseydi core/funnel.py:211-213 onu `logger.debug` ile
    SESSIZCE yutardi. Bu yuzden `gate_block` sebebi olarak kaydediliyor ve bu
    test KALICI ciktiyi okuyor.
    """
    from core.funnel import DailyFunnel
    assert "fund_source_quota" not in DailyFunnel.STAGES, (
        "STAGES'e eklendiyse bu test guncellenmeli"
    )

    import core.fundamental_analyzer as fa
    a, q, c, f = _analyzer(tmp_path, monkeypatch, budget=1)
    q.try_reserve("earnings")
    monkeypatch.setattr(fa.requests, "get", lambda *_a, **_k: pytest.fail("ag"))

    a.get_company_overview("AAPL")

    anlik = f.snapshot(_dt.date.today().isoformat())
    sebepler = anlik.get("gate_block_reasons", {})
    assert sebepler.get("fund_source_quota", 0) >= 1, (
        f"kota etiketi kalici funnel ciktisinda yok: {sebepler}"
    )


def test_k_kota_uyarisi_bir_kez(tmp_path, monkeypatch, caplog):
    """Kota tukenmesi SESSIZ kalmaz ama her sembolde log'u da bogmaz."""
    import core.fundamental_analyzer as fa
    a, q, c, f = _analyzer(tmp_path, monkeypatch, budget=1)
    q.try_reserve("earnings")
    monkeypatch.setattr(fa.requests, "get", lambda *_a, **_k: pytest.fail("ag"))

    with caplog.at_level(logging.WARNING):
        for s in ("AAPL", "MSFT", "GOOGL"):
            a.get_company_overview(s)
    uyarilar = [r for r in caplog.records if "KOTASI TUKENDI" in r.getMessage()]
    assert len(uyarilar) <= 1, f"uyari {len(uyarilar)} kez verildi"


# ======================================================================
# (e) AV HABERI KAPALI , diger kaynaklar calismaya devam
# ======================================================================

def test_e_av_haberi_varsayilan_KAPALI():
    from config import AV_QUOTA_CONFIG
    assert AV_QUOTA_CONFIG["av_news_enabled"] is False, "AV haberi acik kalmis"


def test_e_kapaliyken_av_haber_yolu_cagrilmiyor(monkeypatch):
    """Ihsan karari: 25 cagrinin tamami temel analize."""
    import config
    import core.news_analyzer as na
    monkeypatch.setitem(config.AV_QUOTA_CONFIG, "av_news_enabled", False)

    sayac = {"av": 0, "marketaux": 0}
    monkeypatch.setattr(
        na.StockNewsAnalyzer, "_fetch_alpha_vantage_news",
        lambda self, s: sayac.__setitem__("av", sayac["av"] + 1) or [],
    )
    monkeypatch.setattr(na.StockNewsAnalyzer, "_fetch_google_news",
                        lambda self, s: [])
    monkeypatch.setattr(
        na.StockNewsAnalyzer, "_fetch_marketaux_news",
        lambda self, s: sayac.__setitem__("marketaux", sayac["marketaux"] + 1) or [],
    )
    n = na.StockNewsAnalyzer()
    n.alpha_vantage_key = "test-anahtar"
    n.marketaux_token = "test-token"
    # try/except YOK: bir AttributeError yutulsaydi hicbir sey kosmaz ve
    # asagidaki "av == 0" iddiasi BOSUNA gecerdi. (Ilk yazimda tam bu oldu:
    # metot adi yanlisti, istisna yutuldu, test yesil yandi.)
    n.analyze_stock_news("AAPL")

    assert sayac["av"] == 0, "AV haber yolu kapaliyken cagrildi"
    assert sayac["marketaux"] >= 1, "Marketaux yedegi de olmus, haber tamamen olu"


def test_e_acilirsa_ayni_butceden_geciyor(tmp_path, monkeypatch):
    """Kapatma tek basina yeterli SAYILMAZ , kapi yerinde durmali."""
    import core.news_analyzer as na
    q = _store(tmp_path, budget=1)
    monkeypatch.setattr(na, "shared_store", lambda: q)
    q.try_reserve("fundamental")

    n = na.StockNewsAnalyzer()
    n.alpha_vantage_key = "test-anahtar"
    monkeypatch.setattr(na.requests, "get",
                        lambda *_a, **_k: pytest.fail("kota doluyken ag cagrildi"))

    assert n._fetch_alpha_vantage_news("AAPL") == []


def test_e_earnings_takvimi_de_butceden_geciyor(tmp_path, monkeypatch):
    """UCUNCU tuketici de ayni kapidan , yoksa sinir sessizce asilir."""
    import core.earnings_calendar as ec
    q = _store(tmp_path, budget=1)
    monkeypatch.setattr(ec, "shared_store", lambda: q)
    q.try_reserve("fundamental")

    cal = ec.EarningsCalendar()
    cal.alpha_vantage_key = "test-anahtar"
    cal._fetched_at = None
    cal._last_attempt = datetime(2020, 1, 1)
    monkeypatch.setattr(ec.requests, "get",
                        lambda *_a, **_k: pytest.fail("kota doluyken ag cagrildi"))

    cal._refresh_if_needed()


def test_e_butce_varsa_earnings_cagriyi_yapiyor(tmp_path, monkeypatch):
    """Kapi kotayi tuketmiyorsa is de durmamali."""
    import core.earnings_calendar as ec
    q = _store(tmp_path, budget=5)
    monkeypatch.setattr(ec, "shared_store", lambda: q)

    cagrildi = {"n": 0}

    class _Csv:
        status_code = 200
        text = "symbol,name,reportDate\nAAPL,Apple,2026-10-01\n"

    def _get(*_a, **_k):
        cagrildi["n"] += 1
        return _Csv()

    cal = ec.EarningsCalendar()
    cal.alpha_vantage_key = "test-anahtar"
    cal._fetched_at = None
    cal._last_attempt = datetime(2020, 1, 1)
    monkeypatch.setattr(ec.requests, "get", _get)

    cal._refresh_if_needed()
    assert cagrildi["n"] == 1, "butce varken cagri yapilmadi"
    assert q.remaining() == 4, "earnings kotasi dusulmedi"


def test_e_haber_yolu_gercekten_kosuyor(monkeypatch):
    """Ustteki testin BOSUNA gecmedigini kanitlar.

    analyze_stock_news gercekten cagriliyorsa Google News yolu da cagrilir.
    Cagrilmiyorsa (metot adi yanlis, istisna yutuldu vb.) bu test duser.
    """
    import config
    import core.news_analyzer as na
    monkeypatch.setitem(config.AV_QUOTA_CONFIG, "av_news_enabled", False)

    gorulen = {"google": 0}
    monkeypatch.setattr(
        na.StockNewsAnalyzer, "_fetch_google_news",
        lambda self, s: gorulen.__setitem__("google", gorulen["google"] + 1) or [],
    )
    monkeypatch.setattr(na.StockNewsAnalyzer, "_fetch_marketaux_news",
                        lambda self, s: [])
    n = na.StockNewsAnalyzer()
    n.marketaux_token = ""
    n.analyze_stock_news("AAPL")
    assert gorulen["google"] == 1, "haber yolu hic kosmadi -> diger testler bosuna"
