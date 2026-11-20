"""R15/R16 kod incelemesi duzeltmelerinin kaniti.

Codex, R15 ve R16'yi capraz inceleme OLMADAN yazdiktan sonra gercek diff'i
inceledi ve 14 bulgu cikardi. Bu dosya her duzeltmeyi kanitlar. Bulgu basliklari
RF-SAME-PAGE-LOG-4.md "Kod Incelemesi" bolumunde verbatim duruyor.

Onemli olan sadece duzeltmenin var olmasi degil, ESKI DAVRANISIN GERI
GELMEYECEGI: her test, duzeltme geri alinirsa DUSECEK sekilde yazildi.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

import config
from core.agent_coordinator import AgentCoordinator, AgentVote
from core.av_quota import AVQuotaStore, LockUnavailable, file_lock
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


@pytest.fixture
def social(monkeypatch):
    def _set(v):
        monkeypatch.setitem(config.AGENT_CONFIG, "social_agent_enabled", v)
    return _set


# ======================================================================
# BULGU: kilit alinamazsa KILITSIZ DEVAM ediliyordu
# ======================================================================

def test_kilit_alinamazsa_rezervasyon_REDDEDILIYOR(tmp_path, monkeypatch):
    """Kilitsiz devam etmek modulun TEK garantisini iptal ederdi.

    Onceki surum uyarip devam ediyordu; iki eszamanli surec butceyi ikiye
    katlayabilirdi.
    """
    import core.av_quota as avq
    q = _store(tmp_path)

    def kilit_yok(_path):
        raise LockUnavailable("test: kilit alinamadi")

    monkeypatch.setattr(avq, "file_lock", kilit_yok)
    assert q.try_reserve("fundamental") is False
    assert q.remaining() == 0, "kilit yokken 'butce var' denmemeli"
    assert q.is_exhausted() is True, "kilit yokken fail-closed olmali"


def test_file_lock_govde_istisnasini_YUTMUYOR(tmp_path):
    """Genis except govdedeki istisnayi yakalayip IKINCI kez yield etmeye
    calisiyordu; bu Python'da RuntimeError uretir.

    Govde istisnasi CAGIRANA ulasmali, generator hatasina donusmemeli.
    """
    lock = str(tmp_path / "x.lock")
    with pytest.raises(ValueError, match="govde patladi"):
        with file_lock(lock):
            raise ValueError("govde patladi")
    # Kilit birakildi mi: ikinci kez alinabilmeli
    with file_lock(lock):
        pass


# ======================================================================
# BULGU: yazma basarisiz olsa bile try_reserve True donuyordu
# ======================================================================

def test_yazma_basarisizsa_rezervasyon_verilmiyor(tmp_path, monkeypatch):
    """True donup yazmamak, kaydedilmemis bir ag cagrisi demektir; restart
    ayni slotu ikinci kez harcardi."""
    q = _store(tmp_path)
    monkeypatch.setattr(AVQuotaStore, "_yaz", lambda self, kayit: False)
    assert q.try_reserve("fundamental") is False


# ======================================================================
# BULGU: ayristirilabilir ama SEMANTIK bozuk kayit "taze butce" sayiliyordu
# ======================================================================

@pytest.mark.parametrize("bozuk", [
    {"utc_day": "2026-09-03", "profile": "paper", "used": {}, "total_used": 0},
    {"utc_day": "yok", "profile": "paper",
     "used": {"fundamental": 0, "news": 0, "earnings": 0}, "total_used": 0},
    {"utc_day": "2026-09-03", "profile": "paper",
     "used": {"fundamental": -5, "news": 0, "earnings": 0}, "total_used": -5},
    {"utc_day": "2026-09-03", "profile": "paper",
     "used": {"fundamental": 3, "news": 0, "earnings": 0}, "total_used": 99},
    {"utc_day": "2026-09-03", "profile": "paper",
     "used": {"fundamental": "cok", "news": 0, "earnings": 0}, "total_used": 0},
    {"utc_day": "2099-01-01", "profile": "paper",
     "used": {"fundamental": 0, "news": 0, "earnings": 0}, "total_used": 0},
])
def test_semantik_bozuk_kayit_taze_butce_vermiyor(tmp_path, bozuk):
    """Eksik/negatif/tutarsiz/gelecek tarihli kayitlar fail-closed olmali."""
    p = tmp_path / "q.json"
    p.write_text(json.dumps(bozuk), encoding="utf-8")
    q = AVQuotaStore(path=str(p), budget=12, profile="paper", now_fn=lambda: T0,
                     earnings_reserve=0)
    assert q.try_reserve("fundamental") is False, f"taze butce verildi: {bozuk}"


def test_gecerli_onceki_gun_kaydi_sifirlaniyor(tmp_path):
    """Sadece GECERLI bir onceki gun kaydi sayaci sifirlayabilir."""
    p = tmp_path / "q.json"
    p.write_text(json.dumps({
        "schema_version": 2, "utc_day": "2026-09-02", "profile": "paper",
        "budget": 12, "used": {"fundamental": 12, "news": 0, "earnings": 0},
        "total_used": 12, "exhausted_day": "2026-09-02",
    }), encoding="utf-8")
    q = AVQuotaStore(path=str(p), budget=12, profile="paper", now_fn=lambda: T0,
                     earnings_reserve=0)
    assert q.try_reserve("fundamental") is True, "gecerli gun donusu sifirlamadi"


# ======================================================================
# BULGU: tukenme isareti SEMBOL bazliydi, anahtar geneli degildi
# ======================================================================

def test_tukenme_isareti_ANAHTAR_GENELINDE(tmp_path):
    """Bir tuketici kotanin bittigini ogrendiginde digerleri de durmali.

    Onceki surumde her sembol icin ayri ogreniliyordu: kalan butce bosa
    harcaniyor ve her biri icin 15 saniye uyunuyordu.
    """
    q = _store(tmp_path, budget=12)
    assert q.try_reserve("fundamental") is True
    q.mark_exhausted()
    assert q.is_exhausted() is True
    assert q.try_reserve("fundamental") is False, "tukenmisken rezervasyon verildi"
    assert q.try_reserve("news") is False, "diger tuketici hala cagri yapabiliyor"
    assert q.try_reserve("earnings") is False
    assert q.remaining() == 0


def test_tukenme_isareti_ertesi_gun_dusuyor(tmp_path):
    p = tmp_path / "q.json"
    bugun = AVQuotaStore(path=str(p), budget=12, profile="paper",
                         now_fn=lambda: T0, earnings_reserve=0)
    bugun.mark_exhausted()
    assert bugun.is_exhausted() is True
    yarin = AVQuotaStore(path=str(p), budget=12, profile="paper",
                         now_fn=lambda: T0 + timedelta(days=1), earnings_reserve=0)
    assert yarin.is_exhausted() is False
    assert yarin.try_reserve("fundamental") is True


def test_analizor_tukenmisken_ag_cagrisi_yapmiyor(tmp_path, monkeypatch):
    """Anahtar geneli isaret analizorun cagri yoluna GERCEKTEN baglanmis mi."""
    import core.fundamental_analyzer as fa
    from core.funnel import DailyFunnel
    monkeypatch.setenv("ALPHA_VANTAGE_KEY", "test")
    q = _store(tmp_path, budget=12)
    q.mark_exhausted()
    a = fa.FundamentalAnalyzer(
        quota=q, disk_cache=_cache(tmp_path),
        funnel=DailyFunnel(path=str(tmp_path / "f.json")),
    )
    sayac = {"get": 0, "sleep": 0}
    monkeypatch.setattr(fa.requests, "get",
                        lambda *_a, **_k: sayac.__setitem__("get", sayac["get"] + 1))
    monkeypatch.setattr(fa.time, "sleep",
                        lambda *_a: sayac.__setitem__("sleep", sayac["sleep"] + 1))

    for sembol in ("AAPL", "MSFT", "GOOGL", "AMZN", "NVDA"):
        assert a.get_company_overview(sembol) is None
    assert sayac["get"] == 0, "kota tukenmisken 5 sembol icin ag cagrisi yapildi"
    assert sayac["sleep"] == 0, "kota tukenmisken uyunuldu"


# ======================================================================
# BULGU: bellek cache'i bayatligi SIFIRLIYORDU
# ======================================================================

def test_bayat_veri_bellek_cachede_tazeye_donmuyor(tmp_path, monkeypatch):
    """23.9 saatlik bir kayit bellek cache'inde 12 saat daha taze gorunuyordu.

    Ikinci cagri da BAYAT demeli ve yasi tasimali.
    """
    import core.fundamental_analyzer as fa
    from core.funnel import DailyFunnel
    monkeypatch.setenv("ALPHA_VANTAGE_KEY", "test")

    yol = str(tmp_path / "c.json")
    FundamentalsCache(path=yol, ttl_hours=24, max_stale_hours=168,
                      now_fn=lambda: T0).put("AAPL", {"symbol": "AAPL", "pe_ratio": 30})
    sonra = T0 + timedelta(hours=48)
    a = fa.FundamentalAnalyzer(
        quota=_store(tmp_path, now=sonra),
        disk_cache=FundamentalsCache(path=yol, ttl_hours=24, max_stale_hours=168,
                                     now_fn=lambda: sonra),
        funnel=DailyFunnel(path=str(tmp_path / "f.json")),
    )
    monkeypatch.setattr(fa.requests, "get", lambda *_a, **_k: pytest.fail("ag"))

    for _ in range(3):
        r = a.get_company_overview("AAPL")
        assert r["is_stale"] is True, "bayatlik damgasi kayboldu"
        assert 47 < r["data_age_hours"] < 49, "yas sifirlandi"


def test_gelecek_tarihli_damga_reddediliyor(tmp_path):
    """Saat kaymasi 'cok taze' gibi okunup bayatlik kapisini atlamamali."""
    c = _cache(tmp_path)
    c.entries["AAPL"] = {
        "payload": {"s": "AAPL"},
        "fetched_at": (T0 + timedelta(hours=5)).isoformat(),
    }
    yuk, yas, bolge = c.get("AAPL")
    assert bolge == "YOK", "gelecek tarihli damga taze sayildi"
    assert yuk is None


# ======================================================================
# BULGU: bayatlik telemetrisi KARARA ULASMIYORDU
# ======================================================================

def test_bayatlik_analyze_fundamentals_ciktisinda(tmp_path, monkeypatch):
    """analyze_fundamentals data_age_hours/is_stale alanlarini DUSURUYORDU."""
    import core.fundamental_analyzer as fa
    from core.funnel import DailyFunnel
    monkeypatch.setenv("ALPHA_VANTAGE_KEY", "test")

    yol = str(tmp_path / "c.json")
    FundamentalsCache(path=yol, ttl_hours=24, max_stale_hours=168,
                      now_fn=lambda: T0).put(
        "AAPL", {"symbol": "AAPL", "pe_ratio": 12, "eps": 6, "profit_margin": 0.2}
    )
    sonra = T0 + timedelta(hours=48)
    a = fa.FundamentalAnalyzer(
        quota=_store(tmp_path, now=sonra),
        disk_cache=FundamentalsCache(path=yol, ttl_hours=24, max_stale_hours=168,
                                     now_fn=lambda: sonra),
        funnel=DailyFunnel(path=str(tmp_path / "f.json")),
    )
    monkeypatch.setattr(fa.requests, "get", lambda *_a, **_k: pytest.fail("ag"))

    sonuc = a.analyze_fundamentals("AAPL")
    assert sonuc["is_stale"] is True, "karar bayat veriyle verildigini bilmiyor"
    assert 47 < sonuc["data_age_hours"] < 49


def test_veri_yokken_kaynak_durustce_isaretleniyor(tmp_path, monkeypatch):
    import core.fundamental_analyzer as fa
    from core.funnel import DailyFunnel
    monkeypatch.setenv("ALPHA_VANTAGE_KEY", "test")
    q = _store(tmp_path)
    q.mark_exhausted()
    a = fa.FundamentalAnalyzer(quota=q, disk_cache=_cache(tmp_path),
                               funnel=DailyFunnel(path=str(tmp_path / "f.json")))
    sonuc = a.analyze_fundamentals("AAPL")
    assert sonuc["data_source"] == "SOURCE_UNAVAILABLE"
    assert sonuc["fundamental_score"] == 0


# ======================================================================
# BULGU: yenileme imleci URETIMDE OLU koddu
# ======================================================================

def test_prefetch_due_imleci_gercekten_kullaniyor(tmp_path, monkeypatch):
    """prefetch_due, imlecin uretimdeki TEK cagri noktasi."""
    import core.fundamental_analyzer as fa
    from core.funnel import DailyFunnel
    monkeypatch.setenv("ALPHA_VANTAGE_KEY", "test")

    c = _cache(tmp_path)
    # AAPL taze, digerleri hic cekilmemis
    c.put("AAPL", {"symbol": "AAPL"})
    a = fa.FundamentalAnalyzer(quota=_store(tmp_path, budget=2), disk_cache=c,
                               funnel=DailyFunnel(path=str(tmp_path / "f.json")))

    cekilen = []

    class _Y:
        status_code = 200
        def __init__(self, sym): self.text = json.dumps({"Symbol": sym}); self._s = sym
        def json(self): return {"Symbol": self._s}

    def _get(url, params=None, **_k):
        sym = (params or {}).get("symbol", "?")
        cekilen.append(sym)
        return _Y(sym)

    monkeypatch.setattr(fa.requests, "get", _get)
    monkeypatch.setattr(fa.time, "sleep", lambda *_a: None)

    rapor = a.prefetch_due(["AAPL", "MSFT", "GOOGL", "AMZN"])
    assert "AAPL" not in cekilen, "taze sembol icin butce harcandi"
    assert len(cekilen) == 2, f"butce disi cagri: {cekilen}"
    assert rapor["kapsama"]["benzersiz_sembol"] == 4


def test_prefetch_due_kota_tukenmisken_hic_cagri_yapmiyor(tmp_path, monkeypatch):
    import core.fundamental_analyzer as fa
    from core.funnel import DailyFunnel
    monkeypatch.setenv("ALPHA_VANTAGE_KEY", "test")
    q = _store(tmp_path)
    q.mark_exhausted()
    a = fa.FundamentalAnalyzer(quota=q, disk_cache=_cache(tmp_path),
                               funnel=DailyFunnel(path=str(tmp_path / "f.json")))
    monkeypatch.setattr(fa.requests, "get", lambda *_a, **_k: pytest.fail("ag"))
    rapor = a.prefetch_due(["AAPL", "MSFT"])
    assert rapor["denenen"] == 0


def test_prefetch_due_stock_bot_TARAMA_DONGUSUNE_bagli():
    """Uretim baglantisi GERCEKTEN var mi.

    Onceki surumde imlec yalniz testlerden cagriliyordu; ozellik uretimde HIC
    kosmuyordu. Bu test kaynak kodda gercek cagri noktasini arar , metodun var
    olmasi yetmez, CAGRILIYOR olmasi lazim.
    """
    import inspect
    import io as _io
    import os
    kok = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    kaynak = _io.open(os.path.join(kok, "stock_bot.py"), encoding="utf-8").read()
    assert "self.fundamental_analyzer.prefetch_due(" in kaynak, (
        "prefetch_due stock_bot tarama dongusunde CAGRILMIYOR , imlec yine olu"
    )
    # Dogru attribute adi mi (yanlis ad try/except icinde sessizce yutulurdu)
    from stock_bot import StockBot
    assert "fundamental_analyzer" in inspect.getsource(StockBot.__init__)


# ======================================================================
# BULGU: 'ajan_raporu' KAPALI ajani hala 'veri_yok' diye yaziyordu
# ======================================================================

def test_ajan_raporu_KAPALI_yaziyor(tmp_path, social):
    """Politika ile kapali, kaynak sustu'dan AYRI gorunmeli , raporda da."""
    from core.agent_stats import AgentStats
    from tools.ajan_raporu import report_lines
    import datetime as _dt

    social(False)
    gun = _dt.date(2026, 9, 3)
    st = AgentStats(path=str(tmp_path / "s.json"), today_fn=lambda: gun)
    st.record_decision(
        {"signal": "HOLD", "confidence": 0, "weighted_score": 0,
         "votes": [{"agent": "TechAgent", "signal": "BUY", "confidence": 40}]},
        data_ok={"TechAgent": True}, dynamic_weights={"TechAgent": 0.25},
        min_confidence_score=30,
    )
    metin = "\n".join(report_lines(st.days, gun.isoformat()))
    social_satiri = [l for l in metin.splitlines() if "SocialAgent" in l]
    assert social_satiri, "SocialAgent raporda yok"
    assert "KAPALI" in social_satiri[0], (
        f"kapali ajan hala 'veri_yok' diye raporlaniyor: {social_satiri[0]}"
    )


# ======================================================================
# BULGU: coordinator "5 uzman ajan aktif" diye YALAN logluyordu
# ======================================================================

def test_coordinator_gercek_ajan_kumesini_logluyor(social, monkeypatch):
    """Proje kendi logger'ini kullaniyor; caplog'a yansimiyor , dogrudan yakala."""
    import core.agent_coordinator as ac
    social(False)
    yakalanan = []
    monkeypatch.setattr(ac.logger, "info", lambda m, *a, **k: yakalanan.append(str(m)))
    AgentCoordinator()
    mesajlar = " ".join(yakalanan)
    assert mesajlar, "coordinator hic log atmadi -> test bosuna geciyor"
    assert "5 uzman ajan aktif" not in mesajlar, "yalan log hala duruyor"
    assert "4 ajan aktif" in mesajlar, mesajlar
    assert "KAPALI" in mesajlar, mesajlar


# ======================================================================
# BULGU: kazanc rezervi bir slotu KALICI olarak bosa yatiriyordu
# ======================================================================

def test_rezerv_BASARIDAN_sonra_TAMAMEN_serbest(tmp_path):
    """reserve=1: takvim BASARIYLA tazeleyince kalan 11 slotun HEPSI temele acilir.

    Slot kalici olarak bosa yatmamali (ilk bulgu) AMA basarisiz bir deneme de
    onu serbest birakmamali (ikinci bulgu). Ikisi birden.
    """
    q = _store(tmp_path, budget=12, reserve=1)
    assert q.try_reserve("earnings") is True
    q.mark_earnings_refreshed()
    alinan = 0
    while q.try_reserve("fundamental"):
        alinan += 1
    assert alinan == 11, f"rezerv slotu bosa yatiyor: temele {alinan} kaldi"


def test_rezerv_takvim_cagirmadan_once_korunuyor(tmp_path):
    q = _store(tmp_path, budget=12, reserve=1)
    alinan = 0
    while q.try_reserve("fundamental"):
        alinan += 1
    assert alinan == 11, "rezerv korunmadi"
    assert q.try_reserve("earnings") is True, "takvim icin slot kalmadi"


# ======================================================================
# BULGU: cache yazimi KILITSIZDI , eszamanli surec digerini siliyordu
# ======================================================================

def test_cache_eszamanli_yazimda_veri_silmiyor(tmp_path):
    """Iki ayri nesne farkli semboller yazsin; ikisi de HAYATTA kalmali."""
    yol = str(tmp_path / "c.json")
    a = FundamentalsCache(path=yol, now_fn=lambda: T0)
    b = FundamentalsCache(path=yol, now_fn=lambda: T0)
    a.put("AAPL", {"s": "AAPL"})
    b.put("MSFT", {"s": "MSFT"})     # a'nin yazdigini silmemeli

    son = FundamentalsCache(path=yol, now_fn=lambda: T0)
    assert son.get("AAPL")[2] == "TAZE", "ilk surecin verisi silindi"
    assert son.get("MSFT")[2] == "TAZE"


def test_cache_negatif_kayit_da_korunuyor(tmp_path):
    yol = str(tmp_path / "c.json")
    a = FundamentalsCache(path=yol, now_fn=lambda: T0)
    b = FundamentalsCache(path=yol, now_fn=lambda: T0)
    a.mark_quota_exhausted("AAPL")
    b.mark_quota_exhausted("MSFT")
    son = FundamentalsCache(path=yol, now_fn=lambda: T0)
    assert son.is_negative_cached("AAPL")
    assert son.is_negative_cached("MSFT")


# ======================================================================
# BULGU: altin cikti ureteci kendi kendini KUTSUYORDU
# ======================================================================

def test_altin_cikti_ureteci_provenance_istiyor(tmp_path, monkeypatch):
    """Ureteci varsayilan olarak kanonik fixture'i EZMEMELI.

    Ezerse bir regresyon + yeniden uretim kendini onaylar ve altin cikti
    anlamini kaybeder.
    """
    import tools.r15_golden_uret as gen
    import os
    kok = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    kanonik = os.path.join(kok, "tests", "fixtures", "r15_golden.json")
    assert os.path.exists(kanonik)
    # Uretecin ana akisi acik onay istemeli
    import inspect
    src = inspect.getsource(gen.main)
    assert "--onayla" in src or "--confirm" in src, (
        "uretec kanonik fixture'i onaysiz ezebiliyor"
    )
