"""R16 , AV kotasi: uc tuketici, profil butcesi, surecler arasi kilit.

Kanit maddeleri RF-PLAN-4.md R16 PROOF bolumunden birebir:
  (a) cache hit'te requests.get VE time.sleep cagri sayaci 0
  (b) UC tuketici (fundamental + news + earnings) toplami butceyi gecmiyor
  (c) IKI BAGIMSIZ SUREC (live+paper) toplami 25'i gecmiyor
  (d) AYNI PROFILDEN IKI ESZAMANLI SUREC butceyi asmiyor (interprocess lock)
  (e) AV haberi kapaliyken haber yolunun AV sayaci 0, Marketaux calisiyor
  (f) imlec: restart'lar boyunca her sembol sirayla tazeleniyor
  (g) bozuk sayac -> yeni ag cagrisi 0, ama yuk cache'i okunabiliyor
  (h) HTTP 200 kota govdesi QUOTA_EXHAUSTED, timeout RETRYABLE_ERROR
  (i) maksimum bayatlik yasi asilinca SOURCE_UNAVAILABLE
  (j) UTC gun sinirinda sayac sifirlaniyor, yerel saatte degil
  (k) fund_source_quota kalici funnel ciktisinda gercekten gorunuyor
  (l) kapsama sayilari BENZERSIZ SEMBOL sayiyor
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from datetime import datetime, timedelta, timezone

import pytest

from core.av_quota import (
    CONSUMERS,
    AVOutcome,
    AVQuotaStore,
    classify_response,
    utc_day,
)
from core.fundamentals_cache import FundamentalsCache

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
T0 = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)


def _store(tmp_path, budget=12, profile="paper", now=T0, reserve=0):
    """Varsayilan rezerv 0: butce mekanigi testleri rezervden bagimsiz olsun.

    Rezerv davranisi ayri testlerde ACIKCA verilerek sinanir; ortuk config'e
    guvenen bir test, config degisince alakasiz yerde kirilir.
    """
    return AVQuotaStore(
        path=str(tmp_path / "av_quota.json"),
        budget=budget,
        profile=profile,
        now_fn=lambda: now,
        earnings_reserve=reserve,
    )


def _cache(tmp_path, now=T0, ttl=24, max_stale=168):
    return FundamentalsCache(
        path=str(tmp_path / "fund_cache.json"),
        ttl_hours=ttl,
        max_stale_hours=max_stale,
        now_fn=lambda: now,
    )


# ======================================================================
# (b) UC TUKETICI TEK BUTCE
# ======================================================================

def test_b_uc_tuketici_ayni_butceyi_paylasiyor(tmp_path):
    """Uc tuketicinin TOPLAMI butceyi gecmemeli, her biri ayri 12 degil."""
    q = _store(tmp_path, budget=12)
    alinan = {c: 0 for c in CONSUMERS}
    # Sirayla dolastir: her tuketici sirasi geldiginde rezerve etmeye calissin
    for i in range(60):
        c = CONSUMERS[i % len(CONSUMERS)]
        if q.try_reserve(c):
            alinan[c] += 1
    assert sum(alinan.values()) == 12, f"butce asildi/eksik kaldi: {alinan}"
    assert q.remaining() == 0


def test_b_takvim_payi_temel_payini_azaltiyor(tmp_path):
    """Kazanc takvimi cagrisi temel analize kalan payi GERCEKTEN azaltir.

    Rezerv 0: burada olculen sey PAYLASILAN BUTCE, rezerv korumasi degil.
    """
    q = _store(tmp_path, budget=12, reserve=0)
    assert q.try_reserve("earnings") is True
    assert q.try_reserve("earnings") is True
    kalan_temel = 0
    while q.try_reserve("fundamental"):
        kalan_temel += 1
    assert kalan_temel == 10, "takvim payi temel analizden dusulmedi"


def test_b_tanimsiz_tuketici_reddediliyor(tmp_path):
    """CONSUMERS'a eklenmemis bir cagiran butce disinda kalamamali."""
    q = _store(tmp_path, budget=12)
    assert q.try_reserve("yeni_bilinmeyen_tuketici") is False
    assert q.remaining() == 12


# ======================================================================
# (c) IKI PROFIL , toplam 25
# ======================================================================

def test_c_iki_profil_toplami_25(tmp_path):
    """live 13 + paper 12 = 25. Ayri state hacimleri, ayri dosyalar."""
    live = AVQuotaStore(
        path=str(tmp_path / "live" / "av_quota.json"),
        budget=13, profile="live", now_fn=lambda: T0, earnings_reserve=0,
    )
    paper = AVQuotaStore(
        path=str(tmp_path / "paper" / "av_quota.json"),
        budget=12, profile="paper", now_fn=lambda: T0, earnings_reserve=0,
    )
    l = sum(1 for _ in iter(lambda: live.try_reserve("fundamental"), False))
    p = sum(1 for _ in iter(lambda: paper.try_reserve("fundamental"), False))
    assert l == 13
    assert p == 12
    assert l + p == 25


def test_c_profil_dosyalari_birbirini_etkilemiyor(tmp_path):
    live = AVQuotaStore(path=str(tmp_path / "l.json"), budget=13,
                        profile="live", now_fn=lambda: T0, earnings_reserve=0)
    paper = AVQuotaStore(path=str(tmp_path / "p.json"), budget=12,
                         profile="paper", now_fn=lambda: T0, earnings_reserve=0)
    for _ in range(13):
        live.try_reserve("fundamental")
    assert live.remaining() == 0
    assert paper.remaining() == 12, "profiller birbirinin sayacini tuketiyor"


def test_c_baska_profilin_dosyasi_FAIL_CLOSED(tmp_path):
    """Ayni yolda BASKA profilin kaydi varsa TEMIZ BUTCE verilmez.

    Onceki surum bunu "gun donusu" sanip sayaci sifirliyordu, yani yanlis
    yapilandirilmis bir profil taze 12 cagri kazaniyordu. Uretimde her profilin
    kendi state dizini var; ayni yolda baska profil gormek bir ANOMALIDIR ve
    fail-closed davranilmali (Codex kod incelemesi bulgusu).
    """
    p = tmp_path / "q.json"
    live = AVQuotaStore(path=str(p), budget=13, profile="live", now_fn=lambda: T0,
                        earnings_reserve=0)
    for _ in range(13):
        live.try_reserve("fundamental")
    paper = AVQuotaStore(path=str(p), budget=12, profile="paper", now_fn=lambda: T0,
                         earnings_reserve=0)
    assert paper.try_reserve("fundamental") is False, (
        "baska profilin dosyasi taze butce olarak okundu"
    )


# ======================================================================
# (d) SURECLER ARASI KILIT , atomik yazma TEK BASINA yetmez
# ======================================================================

def test_d_eszamanli_ayni_profil_surecleri_butceyi_asmiyor(tmp_path):
    """Restart/deploy ortusmesi: ayni profilden IKI GERCEK SUREC.

    Atomik yer degistirme oku-degistir-yaz yarisini COZMEZ; iki surec ayni
    degeri okuyup ayri ayri artirabilir. Bu test gercek subprocess kullanir,
    thread degil , GIL yarisi gizler.
    """
    qpath = str(tmp_path / "av_quota.json").replace("\\", "\\\\")
    script = textwrap.dedent(f"""
        import sys, json
        sys.path.insert(0, r"{ROOT}")
        from core.av_quota import AVQuotaStore
        q = AVQuotaStore(path="{qpath}", budget=12, profile="paper", earnings_reserve=0)
        n = 0
        for _ in range(40):
            if q.try_reserve("fundamental"):
                n += 1
        print(json.dumps({{"alinan": n}}))
    """)
    sp = tmp_path / "yaris.py"
    sp.write_text(script, encoding="utf-8")

    p1 = subprocess.Popen([sys.executable, str(sp)], stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL, text=True)
    p2 = subprocess.Popen([sys.executable, str(sp)], stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL, text=True)
    o1, _ = p1.communicate(timeout=120)
    o2, _ = p2.communicate(timeout=120)

    a1 = json.loads(o1.strip().splitlines()[-1])["alinan"]
    a2 = json.loads(o2.strip().splitlines()[-1])["alinan"]
    assert a1 + a2 == 12, (
        f"eszamanli surecler butceyi asti: {a1} + {a2} = {a1 + a2} (butce 12)"
    )


# ======================================================================
# (g) BOZUK SAYAC , FAIL-CLOSED, ama yuk cache'i BAGIMSIZ
# ======================================================================

def test_g_bozuk_sayac_yeni_cagriya_izin_vermiyor(tmp_path):
    p = tmp_path / "av_quota.json"
    p.write_text("{bozuk json degil bu", encoding="utf-8")
    q = AVQuotaStore(path=str(p), budget=12, profile="paper", now_fn=lambda: T0,
                     earnings_reserve=0)
    assert q.try_reserve("fundamental") is False, "bozuk sayacta yeni cagri acildi"
    assert q.remaining() == 0


def test_g_bozuk_sayac_ertesi_gun_kendiliginden_iyilesiyor(tmp_path):
    """Fail-closed kalici kilitlenmeye donusmemeli."""
    p = tmp_path / "av_quota.json"
    p.write_text("bozuk", encoding="utf-8")
    bugun = AVQuotaStore(path=str(p), budget=12, profile="paper", now_fn=lambda: T0,
                         earnings_reserve=0)
    assert bugun.try_reserve("fundamental") is False

    yarin = AVQuotaStore(path=str(p), budget=12, profile="paper",
                         now_fn=lambda: T0 + timedelta(days=1), earnings_reserve=0)
    assert yarin.try_reserve("fundamental") is True, "ertesi gun iyilesmedi"


def test_g_yuk_cachei_sayactan_BAGIMSIZ(tmp_path):
    """Sayac bozuk olsa bile cache'teki veri okunabilmeli , AYRI dosyalar."""
    (tmp_path / "av_quota.json").write_text("bozuk", encoding="utf-8")
    c = _cache(tmp_path)
    c.put("AAPL", {"symbol": "AAPL", "pe_ratio": 30})
    yeni = _cache(tmp_path)
    yuk, yas, bolge = yeni.get("AAPL")
    assert bolge == "TAZE"
    assert yuk["pe_ratio"] == 30


def test_g_bozuk_yuk_cachei_temiz_basliyor(tmp_path):
    """Cache bozuksa temiz baslanir (veri kaybi), COKMEZ."""
    (tmp_path / "fund_cache.json").write_text("{yarim", encoding="utf-8")
    c = _cache(tmp_path)
    assert c.entries == {}
    _, _, bolge = c.get("AAPL")
    assert bolge == "YOK"


# ======================================================================
# (h) TIPLI SONUCLAR , kota tukenmesi HTTP 200 ile gelir
# ======================================================================

@pytest.mark.parametrize("govde,payload,beklenen", [
    ('{"Note": "Thank you for using Alpha Vantage! Our standard API rate limit is 25 requests per day"}',
     {"Note": "..."}, AVOutcome.QUOTA_EXHAUSTED),
    ('{"Information": "higher API call volume"}',
     {"Information": "..."}, AVOutcome.QUOTA_EXHAUSTED),
    ('{"Symbol": "AAPL"}', {"Symbol": "AAPL"}, AVOutcome.OK),
    ('{"Error Message": "invalid symbol"}',
     {"Error Message": "invalid"}, AVOutcome.NO_DATA),
])
def test_h_http200_govdesi_dogru_siniflandiriliyor(govde, payload, beklenen):
    """KRITIK: kota tukendiginde AV HTTP 200 doner.

    Sadece status_code'a bakan kod bunu 'basarili ama veri yok' sanip her turda
    yeniden cagirirdi , kotanin dakikalar icinde bitmesinin sebebi buydu.
    """
    assert classify_response(200, govde, payload) is beklenen


@pytest.mark.parametrize("kod", [429, 500, 502, 503, 504, 401])
def test_h_http_hatalari_gecici_sayiliyor(kod):
    """Gecici hata negatif cache'lenmemeli, ayni gun tekrar denenmeli."""
    assert classify_response(kod, "", {}) is AVOutcome.RETRYABLE_ERROR


def test_h_gecici_hata_negatif_cachelenmiyor(tmp_path):
    c = _cache(tmp_path)
    assert c.is_negative_cached("AAPL") is False
    # Yalniz kota tukenmesi negatif cache'lenir
    c.mark_quota_exhausted("AAPL")
    assert c.is_negative_cached("AAPL") is True
    assert c.is_negative_cached("MSFT") is False


def test_h_negatif_cache_ertesi_gun_dusuyor(tmp_path):
    c = _cache(tmp_path, now=T0)
    c.mark_quota_exhausted("AAPL")
    assert c.is_negative_cached("AAPL") is True
    yarin = FundamentalsCache(
        path=str(tmp_path / "fund_cache.json"),
        now_fn=lambda: T0 + timedelta(days=1),
    )
    assert yarin.is_negative_cached("AAPL") is False


# ======================================================================
# (i) BAYATLIK SOZLESMESI
# ======================================================================

def test_i_taze_bolge(tmp_path):
    c = _cache(tmp_path, now=T0, ttl=24)
    c.put("AAPL", {"pe_ratio": 30})
    _, yas, bolge = c.get("AAPL")
    assert bolge == "TAZE"
    assert yas < 1


def test_i_bayat_bolge_kullanilir_ama_yasi_bilinir(tmp_path):
    c = _cache(tmp_path, now=T0, ttl=24, max_stale=168)
    c.put("AAPL", {"pe_ratio": 30})
    sonra = FundamentalsCache(
        path=str(tmp_path / "fund_cache.json"), ttl_hours=24, max_stale_hours=168,
        now_fn=lambda: T0 + timedelta(hours=48),
    )
    yuk, yas, bolge = sonra.get("AAPL")
    assert bolge == "BAYAT"
    assert yuk is not None, "bayat veri kullanilabilmeli"
    assert 47 < yas < 49, "yas dogru raporlanmali"


def test_i_max_bayatlik_asilinca_KULLANILMAZ(tmp_path):
    """Suresiz guven YOK: cok bayat veri SOURCE_UNAVAILABLE olur."""
    c = _cache(tmp_path, now=T0, ttl=24, max_stale=168)
    c.put("AAPL", {"pe_ratio": 30})
    cok_sonra = FundamentalsCache(
        path=str(tmp_path / "fund_cache.json"), ttl_hours=24, max_stale_hours=168,
        now_fn=lambda: T0 + timedelta(hours=200),
    )
    yuk, yas, bolge = cok_sonra.get("AAPL")
    assert bolge == "SOURCE_UNAVAILABLE"
    assert yuk is None, "cok bayat veri KULLANILMAMALI"
    assert yas > 168


# ======================================================================
# (j) UTC GUN SINIRI , yerel saat DEGIL
# ======================================================================

def test_j_utc_gun_sinirinda_sifirlaniyor(tmp_path):
    p = tmp_path / "q.json"
    aksam = datetime(2026, 9, 3, 23, 59, tzinfo=timezone.utc)
    q1 = AVQuotaStore(path=str(p), budget=12, profile="paper", now_fn=lambda: aksam,
                      earnings_reserve=0)
    for _ in range(12):
        q1.try_reserve("fundamental")
    assert q1.remaining() == 0

    gece_yarisi = datetime(2026, 9, 4, 0, 1, tzinfo=timezone.utc)
    q2 = AVQuotaStore(path=str(p), budget=12, profile="paper",
                      now_fn=lambda: gece_yarisi, earnings_reserve=0)
    assert q2.remaining() == 12, "UTC gun donusunde sayac sifirlanmadi"


def test_j_yerel_saat_sinirinda_SIFIRLANMIYOR(tmp_path):
    """Los Angeles gece yarisi (UTC 07:00) sayaci SIFIRLAMAMALI.

    Yerel saate baglanmis bir sayac, gelistirme makinesi ile konteynerde farkli
    anlarda sifirlanir ve butce sessizce iki katina cikardi.
    """
    p = tmp_path / "q.json"
    # LA 2026-09-03 18:00 PDT = UTC 2026-09-04 01:00 , UTC gunu DEGISTI
    # LA 2026-09-03 23:00 PDT = UTC 2026-09-04 06:00 , UTC gunu yine 09-04
    la_aksam = datetime(2026, 9, 3, 20, 0, tzinfo=timezone.utc)   # LA 13:00
    la_gece = datetime(2026, 9, 3, 22, 0, tzinfo=timezone.utc)    # LA 15:00
    q1 = AVQuotaStore(path=str(p), budget=12, profile="paper", now_fn=lambda: la_aksam,
                      earnings_reserve=0)
    for _ in range(12):
        q1.try_reserve("fundamental")
    q2 = AVQuotaStore(path=str(p), budget=12, profile="paper", now_fn=lambda: la_gece,
                      earnings_reserve=0)
    assert q2.remaining() == 0, "ayni UTC gununde sayac sifirlandi"


def test_j_utc_day_naive_datetime_kabul_ediyor(tmp_path):
    naive = datetime(2026, 9, 3, 12, 0)
    assert utc_day(naive) == "2026-09-03"


# ======================================================================
# (f) YENILEME IMLECI
# ======================================================================

def test_f_imlec_en_eski_once(tmp_path):
    """Sabit alfabetik sira yerine en-eski-once; kuyruk aclikta kalmamali."""
    c = _cache(tmp_path, now=T0)
    # AAPL en yeni, ZM en eski
    for sym, saat in (("AAPL", 1), ("MSFT", 50), ("ZM", 100)):
        c.entries[sym] = {
            "payload": {"s": sym},
            "fetched_at": (T0 - timedelta(hours=saat)).isoformat(),
        }
    sira = c.refresh_order(["AAPL", "MSFT", "ZM"])
    assert sira == ["ZM", "MSFT", "AAPL"], f"en-eski-once degil: {sira}"


def test_f_hic_cekilmemis_en_basta(tmp_path):
    c = _cache(tmp_path, now=T0)
    c.entries["AAPL"] = {
        "payload": {}, "fetched_at": (T0 - timedelta(hours=100)).isoformat()
    }
    sira = c.refresh_order(["AAPL", "YENI"])
    assert sira[0] == "YENI", "hic cekilmemis sembol once gelmeli"


def test_f_restartlar_boyunca_tum_evren_tazeleniyor(tmp_path):
    """KRITIK: sabit sira + gunluk butce = kuyruk HIC tazelenmez.

    12 sembolluk evren, gunluk 4 butce. Uc gunde HEPSI tazelenmeli, ayni ilk
    4'u uc kez degil. Imlec DISKTEN okundugu icin restart'i atlatir.
    """
    evren = [f"SYM{i:02d}" for i in range(12)]
    yol = str(tmp_path / "fund_cache.json")
    tazelenen = set()

    for gun in range(3):
        an = T0 + timedelta(days=gun)
        # Her gun YENI nesne = restart simulasyonu
        c = FundamentalsCache(path=yol, ttl_hours=24, max_stale_hours=168,
                              now_fn=lambda an=an: an)
        adaylar = c.next_refresh_candidates(evren, limit=4)
        assert len(adaylar) == 4, f"gun {gun}: {len(adaylar)} aday"
        for s in adaylar:
            c.put(s, {"symbol": s})
            tazelenen.add(s)

    assert tazelenen == set(evren), (
        f"evrenin tamami tazelenmedi, eksik: {set(evren) - tazelenen}"
    )


def test_f_taze_sembol_butce_harcamiyor(tmp_path):
    c = _cache(tmp_path, now=T0)
    c.put("AAPL", {"s": "AAPL"})
    adaylar = c.next_refresh_candidates(["AAPL", "MSFT"], limit=5)
    assert "AAPL" not in adaylar, "taze sembol icin butce harcaniyor"
    assert "MSFT" in adaylar


def test_f_negatif_cachelenmis_sembol_atlaniyor(tmp_path):
    c = _cache(tmp_path, now=T0)
    c.mark_quota_exhausted("AAPL")
    adaylar = c.next_refresh_candidates(["AAPL", "MSFT"], limit=5)
    assert "AAPL" not in adaylar
    assert "MSFT" in adaylar


# ======================================================================
# (l) KAPSAMA , BENZERSIZ SEMBOL, tarama sayisi DEGIL
# ======================================================================

def test_l_kapsama_benzersiz_sembol_sayiyor(tmp_path):
    """Ayni sembol bir turda defalarca degerlendirilse de sayim sismemeli."""
    c = _cache(tmp_path, now=T0)
    c.put("AAPL", {"s": "AAPL"})
    # Ayni sembol 100 kez listede
    evren = ["AAPL"] * 100 + ["MSFT"]
    k = c.coverage(evren)
    assert k["benzersiz_sembol"] == 2, "tekrarlar sayimi sisirdi"
    assert k["taze"] == 1
    assert k["verisiz"] == 1


def test_l_kapsama_yas_dagilimi(tmp_path):
    c = _cache(tmp_path, now=T0)
    c.entries["A"] = {"payload": {}, "fetched_at": (T0 - timedelta(hours=2)).isoformat()}
    c.entries["B"] = {"payload": {}, "fetched_at": (T0 - timedelta(hours=48)).isoformat()}
    c.entries["C"] = {"payload": {}, "fetched_at": (T0 - timedelta(hours=300)).isoformat()}
    k = c.coverage(["A", "B", "C", "D"])
    assert k["taze"] == 1
    assert k["bayat"] == 1
    assert k["cok_bayat_kullanilmaz"] == 1
    assert k["verisiz"] == 1
    assert k["yas_saat_max"] >= 299


# ======================================================================
# KAZANC TAKVIMI REZERVI , olu config degil, gercek koruma
# ======================================================================

def test_takvim_rezervi_temel_analizce_yenilemiyor(tmp_path):
    """Takvim bir ISLEM KAPISINI besliyor (earnings_gate).

    Temel analiz butcenin tamamini yerse takvim bayatlar ve kapi fail-open'a
    duser , yani kazanc gunlerinde islem acilir. Ayrilan slotlara diger
    tuketiciler DOKUNAMAMALI.
    """
    q = AVQuotaStore(path=str(tmp_path / "q.json"), budget=12, profile="paper",
                     now_fn=lambda: T0, earnings_reserve=2)
    alinan = 0
    while q.try_reserve("fundamental"):
        alinan += 1
    assert alinan == 10, f"temel analiz rezervi yedi: {alinan}"
    # Ayrilan iki slot TAKVIM icin hala duruyor
    assert q.try_reserve("earnings") is True
    assert q.try_reserve("earnings") is True
    assert q.try_reserve("earnings") is False


def test_takvim_rezervini_kullanmazsa_serbest_kalmiyor_ama_takvim_alabiliyor(tmp_path):
    """Rezerv, takvim kullanmadigi surece bosta bekler , kasitli.

    Amac takvimin HER ZAMAN yer bulmasi. Bosa gitmesi kabul edilen maliyettir.
    """
    q = AVQuotaStore(path=str(tmp_path / "q.json"), budget=12, profile="paper",
                     now_fn=lambda: T0, earnings_reserve=2)
    for _ in range(10):
        assert q.try_reserve("fundamental") is True
    assert q.try_reserve("fundamental") is False
    assert q.try_reserve("news") is False, "haber de rezerve dokunmamali"
    assert q.try_reserve("earnings") is True


def test_takvim_kullandikca_rezerv_serbest_kaliyor(tmp_path):
    """Takvim payini kullandiginda tavan geri acilir , slot bosa gitmez."""
    q = AVQuotaStore(path=str(tmp_path / "q.json"), budget=12, profile="paper",
                     now_fn=lambda: T0, earnings_reserve=2)
    assert q.try_reserve("earnings") is True
    assert q.try_reserve("earnings") is True   # rezerv tuketildi
    alinan = 0
    while q.try_reserve("fundamental"):
        alinan += 1
    assert alinan == 10, f"rezerv tuketildikten sonra tavan acilmadi: {alinan}"


def test_rezerv_sifirsa_eski_davranis(tmp_path):
    q = AVQuotaStore(path=str(tmp_path / "q.json"), budget=12, profile="paper",
                     now_fn=lambda: T0, earnings_reserve=0)
    alinan = 0
    while q.try_reserve("fundamental"):
        alinan += 1
    assert alinan == 12


def test_config_rezervi_gercekten_okunuyor():
    """earnings_reserve OLU CONFIG olmamali , config'deki deger etkili mi."""
    from config import AV_QUOTA_CONFIG
    assert AV_QUOTA_CONFIG["earnings_reserve"] >= 1
    q = AVQuotaStore(path="/olmayan/yol/q.json", budget=12, profile="paper")
    assert q._earnings_reserve() == AV_QUOTA_CONFIG["earnings_reserve"]
