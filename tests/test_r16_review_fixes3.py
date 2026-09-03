"""R16 kod incelemesi , UCUNCU tur duzeltmelerinin kaniti.

Codex'in ikinci fix-round incelemesinden. En agir bulgu bir CANLI TEHLIKEYDI ve
benim kendi duzeltmemden geliyordu: kota mesaji taramasi her govdeye
uygulaniyordu, ve kazanc takvimi BINLERCE satirlik CSV donuyor.

Her test, duzeltme geri alinirsa DUSER.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from core.av_quota import AVOutcome, AVQuotaStore, ReserveReason, classify_response
from core.fundamentals_cache import FundamentalsCache

T0 = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)


def _store(tmp_path, budget=12, now=T0, reserve=0, ad="q.json"):
    return AVQuotaStore(
        path=str(tmp_path / ad), budget=budget, profile="paper",
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
# CANLI TEHLIKE: CSV govdesinde sirket adi kotayi tukenmis gosteriyordu
# ======================================================================

def test_takvim_CSVsi_kotayi_tukenmis_GOSTERMIYOR():
    """EARNINGS_CALENDAR binlerce satirlik CSV doner.

    'Premium Brands Holdings' gibi bir SIRKET ADI, kota mesaji taramasina
    yakalanip isareti ANAHTAR GENELINDE yaziyordu; UC tuketici birden gun boyu
    susuyordu. Olculdu, gercekti.
    """
    csv = (
        "symbol,name,reportDate,estimate\n"
        "AAPL,Apple Inc,2026-10-01,1.5\n"
        "PRM,Premium Brands Holdings,2026-10-02,1.2\n"
        "RL,Ralph Lauren premium segment,2026-10-03,2.0\n"
    )
    assert classify_response(200, csv, {}) is AVOutcome.OK, (
        "CSV sirket adi kotayi tukenmis gosterdi"
    )


def test_gercek_kota_JSONu_hala_yakalaniyor():
    """Kontrol testi: gercek kota mesaji KISA BIR JSON'dur ve yakalanmali."""
    for govde, yuk in (
        ('{"Note": "Thank you for using Alpha Vantage! premium plans"}', {"Note": "x"}),
        ('{"Information": "higher API call volume"}', {"Information": "x"}),
        ('{"Note": "25 requests per day"}', {"Note": "x"}),
    ):
        assert classify_response(200, govde, yuk) is AVOutcome.QUOTA_EXHAUSTED, govde


def test_sozluk_yukte_de_yakalaniyor():
    """Govde JSON gorunmese bile ayristirilmis sozluk varsa taranmali."""
    assert classify_response(
        200, "rate limit reached", {"Note": "rate limit"}
    ) is AVOutcome.QUOTA_EXHAUSTED


# ======================================================================
# KATI TAMSAYI + TARIH BICIMI
# ======================================================================

@pytest.mark.parametrize("bozuk_used", [
    {"fundamental": 1.9, "news": 0, "earnings": 0},
    {"fundamental": True, "news": 0, "earnings": 0},
    {"fundamental": "3", "news": 0, "earnings": 0},
])
def test_int_zorlamasi_kabul_edilmiyor(tmp_path, bozuk_used):
    """int() 1.9'u 1'e kirpiyor, True'yu 1 yapiyordu; bozuk sayac gecerli
    gorunup yanlis butce veriyordu."""
    p = tmp_path / "q.json"
    toplam = 1 if isinstance(bozuk_used["fundamental"], bool) else 0
    p.write_text(json.dumps({
        "schema_version": 2, "utc_day": "2026-09-03", "profile": "paper",
        "budget": 12, "used": bozuk_used, "total_used": toplam,
    }), encoding="utf-8")
    q = AVQuotaStore(path=str(p), budget=12, profile="paper", now_fn=lambda: T0,
                     earnings_reserve=0)
    assert q.reserve("fundamental")[1] is ReserveReason.CORRUPT_COUNTER


@pytest.mark.parametrize("alan,deger", [
    ("exhausted_day", "uydurma"),
    ("exhausted_day", "2099-01-01"),
    ("earnings_refreshed_day", "yarin"),
    ("earnings_refreshed_day", "2099-01-01"),
])
def test_tarih_alanlari_bicim_dogrulaniyor(tmp_path, alan, deger):
    """Uydurma bir dize kalici tukenme yaratabilirdi."""
    p = tmp_path / "q.json"
    kayit = {
        "schema_version": 2, "utc_day": "2026-09-03", "profile": "paper",
        "budget": 12, "used": {"fundamental": 0, "news": 0, "earnings": 0},
        "total_used": 0,
    }
    kayit[alan] = deger
    p.write_text(json.dumps(kayit), encoding="utf-8")
    q = AVQuotaStore(path=str(p), budget=12, profile="paper", now_fn=lambda: T0,
                     earnings_reserve=0)
    assert q.reserve("fundamental")[1] is ReserveReason.CORRUPT_COUNTER


@pytest.mark.parametrize("count", [0, -1, 1.5, True, "2"])
def test_gecersiz_count_reddediliyor(tmp_path, count):
    """Sifir/negatif count OK donup hic kullanim kaydetmez ya da sayaci
    NEGATIFE cekerdi."""
    q = _store(tmp_path, budget=12)
    verildi, sebep = q.reserve("fundamental", count)
    assert verildi is False
    assert sebep is ReserveReason.INVALID_REQUEST
    assert q.remaining() == 12, "gecersiz istek butceyi bozdu"


# ======================================================================
# TAVAN BAYPASI: acik limit config tavanini atliyordu
# ======================================================================

def test_acik_limit_config_tavanini_ATLAMIYOR(tmp_path, monkeypatch):
    """Gecikme garantisi yalniz cagiran limit vermediginde tutuyordu."""
    import core.fundamental_analyzer as fa
    a = _analyzer(tmp_path, monkeypatch, budget=12)
    cagrilan = []
    monkeypatch.setattr(
        fa.requests, "get",
        lambda url, params=None, **_k: (
            cagrilan.append((params or {}).get("symbol"))
            or _Yanit((params or {}).get("symbol"))
        ),
    )
    monkeypatch.setattr(fa.time, "sleep", lambda *_a: None)

    evren = [f"SYM{i:02d}" for i in range(12)]
    a.prefetch_due(evren, limit=999)          # tavani asmaya calis
    tavan = a._prefetch_max_per_round()
    assert len(cagrilan) <= tavan, (
        f"acik limit tavani atladi: {len(cagrilan)} cagri, tavan {tavan}"
    )


# ======================================================================
# DAYANIKLILIK: disk yazimi basarisizken "basarili" sayilmamali
# ======================================================================

def test_put_dayaniklilik_durumu_donduruyor(tmp_path, monkeypatch):
    c = _cache(tmp_path)
    assert c.put("AAPL", {"s": "AAPL"}) is True
    monkeypatch.setattr(FundamentalsCache, "save", lambda self: False)
    assert c.put("MSFT", {"s": "MSFT"}) is False, "yazma hatasi True dondu"


def test_prefetch_kalici_olmayan_tazelemeyi_basarili_saymiyor(tmp_path, monkeypatch):
    """Ag basarili olsa bile DISK yazimi basarisizsa kapsama artmamistir."""
    import core.fundamental_analyzer as fa
    a = _analyzer(tmp_path, monkeypatch, budget=12)
    monkeypatch.setattr(
        fa.requests, "get",
        lambda url, params=None, **_k: _Yanit((params or {}).get("symbol")),
    )
    monkeypatch.setattr(fa.time, "sleep", lambda *_a: None)
    monkeypatch.setattr(FundamentalsCache, "save", lambda self: False)

    rapor = a.prefetch_due(["AAPL", "MSFT"])
    assert rapor["basarili"] == 0, "kalici olmayan yazim basarili sayildi"
    assert rapor.get("kalici_olmayan", 0) >= 1


# ======================================================================
# DENEME IMLECI: bozuk semboller her turu tekellestirmemeli
# ======================================================================

def test_basarisiz_sembol_her_turu_tekellestirmiyor(tmp_path, monkeypatch):
    """Tavan ekledigim icin bu risk artti: 2 bozuk sembol butun butceyi yiyip
    kuyrugu sonsuza dek ac birakabilirdi."""
    import core.fundamental_analyzer as fa
    yol = str(tmp_path / "c.json")
    evren = ["BOZUK1", "BOZUK2", "IYI1", "IYI2", "IYI3"]

    gorulen = []

    def _get(url, params=None, **_k):
        sym = (params or {}).get("symbol")
        gorulen.append(sym)
        if sym.startswith("BOZUK"):
            class _Hata:
                status_code = 500
                text = ""
                def json(self): return {}
            return _Hata()
        return _Yanit(sym)

    monkeypatch.setattr(fa.time, "sleep", lambda *_a: None)
    q = _store(tmp_path, budget=12)
    from core.funnel import DailyFunnel
    for _tur in range(4):
        c = FundamentalsCache(path=yol, ttl_hours=24, max_stale_hours=168,
                              now_fn=lambda: T0)
        a = fa.FundamentalAnalyzer(quota=q, disk_cache=c,
                                   funnel=DailyFunnel(path=str(tmp_path / "f.json")))
        monkeypatch.setattr(fa.requests, "get", _get)
        a.prefetch_due(evren)

    iyi_gorulen = {s for s in gorulen if s.startswith("IYI")}
    assert iyi_gorulen, (
        f"bozuk semboller butun turlari tekellestirdi, hicbir IYI denenmedi: {gorulen}"
    )


def test_mark_attempt_siralamayi_ilerletiyor(tmp_path):
    c = _cache(tmp_path)
    sira1 = c.refresh_order(["A", "B", "C"])
    c.mark_attempt(sira1[0])
    sira2 = c.refresh_order(["A", "B", "C"])
    assert sira2[0] != sira1[0], "denenen sembol hala en onde"


def test_ardisik_hatada_parti_kesiliyor(tmp_path, monkeypatch):
    """Kalan butceyi bozuk sembollere yedirme."""
    import core.fundamental_analyzer as fa
    a = _analyzer(tmp_path, monkeypatch, budget=12)

    class _Hata:
        status_code = 500
        text = ""
        def json(self): return {}

    cagri = {"n": 0}
    monkeypatch.setattr(
        fa.requests, "get",
        lambda *_a, **_k: (cagri.__setitem__("n", cagri["n"] + 1) or _Hata()),
    )
    monkeypatch.setattr(fa.time, "sleep", lambda *_a: None)
    rapor = a.prefetch_due([f"S{i}" for i in range(10)], limit=10)
    assert rapor.get("erken_kesildi") is True
    assert cagri["n"] <= 2, f"ardisik hatada durmadi: {cagri['n']} cagri"


# ======================================================================
# TAKVIM: sira, gunluk deneme tavani, tipli sebep
# ======================================================================

def test_takvim_rezervi_DISK_YAZIMINDAN_SONRA_birakiyor(tmp_path, monkeypatch):
    """Onceki sira, basarisiz disk yaziminda rezervi birakiyordu ama restart
    bayat/eksik takvim buluyor ve earnings_gate fail-open'a dusuyordu."""
    import inspect
    import core.earnings_calendar as ec
    src = inspect.getsource(ec.EarningsCalendar._refresh_if_needed)
    i_save = src.find("_save_disk_cache()")
    i_mark = src.find("mark_earnings_refreshed()")
    assert i_save != -1 and i_mark != -1
    assert i_save < i_mark, "isaret disk yaziminden ONCE veriliyor"


def test_takvim_gunluk_deneme_tavani(tmp_path, monkeypatch):
    """30 dakikada bir basarisiz olan takvim butun butceyi yiyebilirdi."""
    import core.earnings_calendar as ec
    q = _store(tmp_path, budget=12)
    monkeypatch.setattr(ec, "shared_store", lambda: q)

    class _Hata:
        status_code = 500
        text = ""

    cal = ec.EarningsCalendar()
    cal.alpha_vantage_key = "test"
    cal._fetched_at = None
    monkeypatch.setattr(ec.requests, "get", lambda *_a, **_k: _Hata())

    for _ in range(10):
        cal._last_attempt = datetime(2020, 1, 1)   # backoff'u atla
        cal._refresh_if_needed()

    assert cal._gunluk_deneme <= ec.EarningsCalendar.MAX_DAILY_ATTEMPTS
    kalan = q.remaining()
    assert kalan >= 12 - ec.EarningsCalendar.MAX_DAILY_ATTEMPTS, (
        f"takvim butceyi yedi, kalan {kalan}"
    )


def test_takvim_tipli_sebep_logluyor(tmp_path, monkeypatch, caplog):
    """bool sarmalayici kilit/yazma/tukenme durumlarini ayni mesaja cokertiyordu."""
    import logging
    import core.earnings_calendar as ec
    q = _store(tmp_path, budget=12)
    q.mark_exhausted()
    monkeypatch.setattr(ec, "shared_store", lambda: q)

    yakalanan = []
    monkeypatch.setattr(ec.logger, "warning",
                        lambda m, *a, **k: yakalanan.append(str(m)))
    cal = ec.EarningsCalendar()
    cal.alpha_vantage_key = "test"
    cal._fetched_at = None
    cal._last_attempt = datetime(2020, 1, 1)
    monkeypatch.setattr(ec.requests, "get", lambda *_a, **_k: pytest.fail("ag"))

    cal._refresh_if_needed()
    assert any("PROVIDER_EXHAUSTED" in m for m in yakalanan), yakalanan
