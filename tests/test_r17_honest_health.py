"""R17 , durust aletler: uc boyut + taahhut edilmis supurge isareti.

Kanit maddeleri RF-PLAN-4.md R17 PROOF bolumunden birebir:
  (a) kilitli VE karar telemetrisi bayat -> ozet KILITLI DEGIL, iki sorun da gorunur
  (b) uc boyutun her biri icin durum testleri + SEMA/TEST UYUMU
  (c) gelecek tarihli / bozuk heartbeat -> DEGRADED, asla SAGLIKLI
  (d) yalniz park dolumu + bayat karar telemetrisi -> SAGLIKLI degil
  (e) ikinci profil okunamiyor -> UNKNOWN
  (f) sayfa ortasi broker hatasi -> isaret ILERLEMIYOR
  (g) defter yazma hatasi -> isaret ILERLEMIYOR
  (h) 73 saatlik kesinti -> isaretten basliyor, 0 dolum kaciriyor
  (i) isaret yok (ilk acilis) -> tanimli sinirdan basliyor
  (j) kesinti broker retansiyonundan eski -> DEGRADED, sessiz basari yok
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from core.health_status import (
    BOYUTLAR,
    CIKIS_KODLARI,
    SIDDET,
    TUM_DURUMLAR,
    BoyutDurumu,
    Durum,
    ProfilSagligi,
    SistemSagligi,
    dolum_boyutu,
    giris_yetkisi_durumu,
    karar_hatti_durumu,
    runtime_durumu,
)
from core.sweep_watermark import SweepWatermark

T0 = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)


def _saglikli(sebep="ok"):
    return BoyutDurumu(Durum.SAGLIKLI, sebep)


# ======================================================================
# (a) EN KRITIK TEST , kilit olu bir hatti MASKELEMEMELI
# ======================================================================

def test_a_kilitli_VE_sessiz_ozette_IKISI_DE_gorunur():
    """Tek skalerin maskeleme hatasi.

    KILITLI siddeti (2) SESSIZ'den (1) buyuk. "En kotu"yu gosteren bir ozet,
    kilitli + olu hatti "sadece kilitli" diye raporlardi , R17'nin onlemek icin
    var oldugu seyin ta kendisi.
    """
    p = ProfilSagligi(
        profil="live",
        runtime=_saglikli("heartbeat 1 dk once"),
        decision_pipeline=BoyutDurumu(Durum.SESSIZ, "son karar 30 saat once"),
        entry_authorization=BoyutDurumu(Durum.KILITLI, "R5 kilidi kapali"),
    )
    ozet = p.ozet_metni()
    assert "decision_pipeline=SESSIZ" in ozet, (
        f"olu karar hatti kilidin arkasinda MASKELENDI: {ozet}"
    )
    assert "entry_authorization=KILITLI" in ozet
    assert len(p.sorunlu_boyutlar()) == 2


def test_a_kilitli_VE_bozuk_hat_ikisi_de_gorunur():
    p = ProfilSagligi(
        profil="live",
        runtime=_saglikli(),
        decision_pipeline=BoyutDurumu(Durum.DEGRADED, "invaryant ihlali"),
        entry_authorization=BoyutDurumu(Durum.KILITLI, "R5"),
    )
    ozet = p.ozet_metni()
    assert "decision_pipeline=DEGRADED" in ozet and "KILITLI" in ozet
    assert p.en_kotu() is Durum.DEGRADED


def test_a_hepsi_saglikli_ise_ozet_temiz():
    p = ProfilSagligi("paper", _saglikli(), _saglikli(), _saglikli())
    assert p.sorunlu_boyutlar() == {}
    assert "SAGLIKLI" in p.ozet_metni()
    assert p.en_kotu() is Durum.SAGLIKLI


# ======================================================================
# (b) SEMA / TEST UYUMU , proof'ta gecen HER durum tanimli olmali
# ======================================================================

def test_b_her_durum_siddet_tablosunda():
    for d in TUM_DURUMLAR:
        assert d in SIDDET, f"{d} icin siddet tanimli degil"
        assert d in CIKIS_KODLARI, f"{d} icin cikis kodu tanimli degil"


def test_b_siddet_sirasi_tutarli():
    """Kasitli durumlar (SESSIZ/KILITLI) arizalardan HAFIF olmali."""
    assert SIDDET[Durum.SAGLIKLI] < SIDDET[Durum.SESSIZ]
    assert SIDDET[Durum.SESSIZ] < SIDDET[Durum.KILITLI]
    assert SIDDET[Durum.KILITLI] < SIDDET[Durum.UNKNOWN]
    assert SIDDET[Durum.UNKNOWN] < SIDDET[Durum.DEGRADED]
    assert SIDDET[Durum.DEGRADED] < SIDDET[Durum.KAPALI]


def test_b_kasitli_durumlar_ariza_cikisi_vermiyor():
    """Kilit ve secici mod ARIZA DEGILDIR , 0 donmeli."""
    assert CIKIS_KODLARI[Durum.KILITLI] == 0
    assert CIKIS_KODLARI[Durum.SESSIZ] == 0
    assert CIKIS_KODLARI[Durum.UNKNOWN] != 0
    assert CIKIS_KODLARI[Durum.DEGRADED] != 0


def test_b_boyut_listesi_profil_ile_uyumlu():
    p = ProfilSagligi("paper", _saglikli(), _saglikli(), _saglikli())
    assert set(p.boyutlar()) == set(BOYUTLAR)


# ======================================================================
# (c) GELECEK TARIHLI / BOZUK HEARTBEAT -> asla SAGLIKLI
# ======================================================================

def test_c_gelecek_tarihli_heartbeat_DEGRADED():
    d = runtime_durumu(T0 + timedelta(minutes=45), T0)
    assert d.durum is Durum.DEGRADED
    assert d.durum is not Durum.SAGLIKLI


def test_c_bozuk_heartbeat_DEGRADED():
    d = runtime_durumu(None, T0, okuma_hatasi="bozuk json")
    assert d.durum is Durum.DEGRADED


def test_c_heartbeat_yok_UNKNOWN_saglikli_degil():
    d = runtime_durumu(None, T0)
    assert d.durum is Durum.UNKNOWN


def test_c_bayat_heartbeat_KAPALI():
    assert runtime_durumu(T0 - timedelta(minutes=90), T0).durum is Durum.KAPALI


def test_c_taze_heartbeat_SAGLIKLI():
    assert runtime_durumu(T0 - timedelta(minutes=2), T0).durum is Durum.SAGLIKLI


# ======================================================================
# (d) DOLUM SAGLIK KANITI DEGILDIR
# ======================================================================

def test_d_park_dolumu_saglik_kaniti_degil():
    """Canli hesapta birebir yasanan durum: tek hareket SPY parki."""
    dolumlar = [
        {"provenance": "index_parking", "ts": T0 - timedelta(minutes=5)},
        {"provenance": "index_parking", "ts": T0 - timedelta(minutes=9)},
    ]
    d = dolum_boyutu(dolumlar, T0)
    assert d["strateji"] == 0, "park islemi strateji sayildi"
    assert d["strateji_disi"] == 2
    assert d["son_strateji_dolumu"] is None
    assert "SAGLIK KANITI DEGILDIR" in d["not"]


def test_d_taze_park_dolumu_bayat_hatti_kurtarmiyor():
    """Taze dolum + bayat karar hatti -> SAGLIKLI OLAMAZ."""
    p = ProfilSagligi(
        profil="live",
        runtime=_saglikli(),
        decision_pipeline=karar_hatti_durumu(T0 - timedelta(hours=40), T0),
        entry_authorization=BoyutDurumu(Durum.KILITLI, "R5"),
        dolumlar=dolum_boyutu(
            [{"provenance": "index_parking", "ts": T0 - timedelta(minutes=1)}], T0
        ),
    )
    assert p.decision_pipeline.durum is Durum.SESSIZ
    assert p.en_kotu() is not Durum.SAGLIKLI
    assert "decision_pipeline=SESSIZ" in p.ozet_metni()


def test_d_strateji_dolumu_ayrisiyor():
    d = dolum_boyutu([
        {"provenance": "strategy", "ts": T0 - timedelta(hours=1)},
        {"provenance": "index_parking", "ts": T0},
    ], T0)
    assert d["strateji"] == 1 and d["strateji_disi"] == 1
    assert d["son_strateji_dolumu"] is not None


# ======================================================================
# (e) OKUNAMAYAN PROFIL -> UNKNOWN, sessizce atlanmaz
# ======================================================================

def test_e_okunamayan_profil_UNKNOWN():
    s = SistemSagligi(
        {"paper": ProfilSagligi("paper", _saglikli(), _saglikli(), _saglikli())},
        {"live": "bu konteynerden okunamaz"},
    )
    assert s.en_kotu() is Durum.UNKNOWN, "eksik profil sessizce atlandi"
    assert s.cikis_kodu() == CIKIS_KODLARI[Durum.UNKNOWN]


def test_e_hicbir_profil_yoksa_UNKNOWN():
    assert SistemSagligi({}, {}).en_kotu() is Durum.UNKNOWN


def test_e_tum_profiller_saglikli_ise_saglikli():
    s = SistemSagligi(
        {"paper": ProfilSagligi("paper", _saglikli(), _saglikli(), _saglikli())},
        {},
    )
    assert s.en_kotu() is Durum.SAGLIKLI and s.cikis_kodu() == 0


# ======================================================================
# GIRIS YETKISI , kilit ARIZA DEGIL ama gizlenmez de
# ======================================================================

def test_kilit_ariza_olarak_raporlanmiyor():
    d = giris_yetkisi_durumu(False, is_paper=False)
    assert d.durum is Durum.KILITLI
    assert CIKIS_KODLARI[d.durum] == 0, "kasitli kilit ariza cikisi verdi"
    assert "LIVE_ENTRIES_ENABLED" in d.sebep, "nasil acilacagi yazmiyor"


def test_kilit_okunamazsa_UNKNOWN():
    assert giris_yetkisi_durumu(None, is_paper=False).durum is Durum.UNKNOWN


def test_paper_kilitten_etkilenmiyor():
    assert giris_yetkisi_durumu(False, is_paper=True).durum is Durum.SAGLIKLI


# ======================================================================
# KARAR HATTI , invaryant ihlali SESSIZ degil DEGRADED
# ======================================================================

def test_invaryant_ihlali_DEGRADED():
    """R15 incelemesinden: invaryant ihlali siradan HOLD gibi gorunuyordu."""
    d = karar_hatti_durumu(T0, T0, invaryant_ihlali=True)
    assert d.durum is Durum.DEGRADED


def test_kill_switch_DEGRADED():
    assert karar_hatti_durumu(T0, T0, kill_switch_aktif=True).durum is Durum.DEGRADED


def test_ardisik_hata_DEGRADED():
    assert karar_hatti_durumu(T0, T0, ardisik_hata=3).durum is Durum.DEGRADED


def test_karar_telemetrisi_yok_UNKNOWN():
    assert karar_hatti_durumu(None, T0).durum is Durum.UNKNOWN


def test_taze_karar_SAGLIKLI():
    assert karar_hatti_durumu(T0 - timedelta(hours=1), T0).durum is Durum.SAGLIKLI


# ======================================================================
# SUPURGE ISARETI , (f) (g) (h) (i) (j)
# ======================================================================

def _wm(tmp_path, now=T0, retention=90):
    return SweepWatermark(
        path=str(tmp_path / "wm.json"), retention_days=retention,
        now_fn=lambda: now,
    )


def test_f_sayfa_ortasi_hatada_isaret_ILERLEMIYOR(tmp_path):
    wm = _wm(tmp_path)
    assert wm.commit(T0, pages_complete=False, writes_ok=True) is False
    assert wm.read() is None, "eksik sayfada isaret ilerledi"


def test_g_defter_yazma_hatasinda_isaret_ILERLEMIYOR(tmp_path):
    wm = _wm(tmp_path)
    assert wm.commit(T0, pages_complete=True, writes_ok=False) is False
    assert wm.read() is None, "yazma hatasinda isaret ilerledi"


def test_tam_basarida_isaret_ilerliyor(tmp_path):
    wm = _wm(tmp_path)
    assert wm.commit(T0, pages_complete=True, writes_ok=True) is True
    assert wm.read() == T0


def test_isaret_geriye_gitmiyor(tmp_path):
    wm = _wm(tmp_path)
    wm.commit(T0, pages_complete=True, writes_ok=True)
    wm.commit(T0 - timedelta(hours=5), pages_complete=True, writes_ok=True)
    assert wm.read() == T0, "isaret geriye gitti"


def test_h_73_saatlik_kesinti_ISARETTEN_basliyor(tmp_path):
    """SABIT PENCERE burada COKER: 72 saatlik pencere 73 saatlik kesintide
    KALICI delik birakir. Isaret oyle davranmaz."""
    kapanma = T0
    wm_yaz = _wm(tmp_path, now=kapanma)
    wm_yaz.commit(kapanma, pages_complete=True, writes_ok=True)

    donus = kapanma + timedelta(hours=73)
    wm = _wm(tmp_path, now=donus)
    cutoff, durum, eksiksiz = wm.plan_window(donus, min_window_hours=24)

    assert durum == "ISARET"
    assert eksiksiz is True
    assert cutoff <= kapanma, (
        f"73 saatlik kesintide delik birakildi: cutoff={cutoff} kapanma={kapanma}"
    )


def test_h_sabit_72_saat_pencere_ayni_kesintide_delik_birakirdi():
    """Kontrol: eski yaklasimin NEDEN yetersiz oldugunu dondurur."""
    kapanma = T0
    donus = kapanma + timedelta(hours=73)
    sabit_cutoff = donus - timedelta(hours=72)
    assert sabit_cutoff > kapanma, (
        "sabit pencere bu senaryoda delik birakmiyor , senaryo yanlis"
    )


def test_i_ilk_acilis_tanimli_sinirdan_basliyor(tmp_path):
    wm = _wm(tmp_path)
    taban = T0 - timedelta(days=10)
    cutoff, durum, eksiksiz = wm.plan_window(T0, bootstrap_from=taban)
    assert durum == "ILK_ACILIS"
    assert cutoff == taban
    assert eksiksiz is True


def test_i_ilk_acilis_taban_yoksa_min_pencere(tmp_path):
    wm = _wm(tmp_path)
    cutoff, durum, _ = wm.plan_window(T0, min_window_hours=24)
    assert durum == "ILK_ACILIS"
    assert cutoff == T0 - timedelta(hours=24)


def test_j_retansiyondan_eski_kesinti_DEGRADED(tmp_path):
    """Eksiksiz kurtarma IMKANSIZ , sessizce basarili sayilamaz."""
    cok_eski = T0 - timedelta(days=200)
    wm_yaz = _wm(tmp_path, now=cok_eski)
    wm_yaz.commit(cok_eski, pages_complete=True, writes_ok=True)

    wm = _wm(tmp_path, now=T0, retention=90)
    cutoff, durum, eksiksiz = wm.plan_window(T0)
    assert durum == "RETANSIYON_ASILDI"
    assert eksiksiz is False, "kurtarilamayan kesinti basarili sayildi"
    assert cutoff >= T0 - timedelta(days=91)


def test_gelecek_tarihli_isaret_reddediliyor(tmp_path):
    """Kabul edilseydi arasindaki butun dolumlar sonsuza dek atlanirdi."""
    p = tmp_path / "wm.json"
    p.write_text(json.dumps({
        "schema_version": 1,
        "committed_until": (T0 + timedelta(days=5)).isoformat(),
        "committed_at": T0.isoformat(),
    }), encoding="utf-8")
    wm = _wm(tmp_path)
    assert wm.read() is None


def test_bozuk_isaret_dosyasi_ilk_acilisa_dusuyor(tmp_path):
    (tmp_path / "wm.json").write_text("bozuk", encoding="utf-8")
    wm = _wm(tmp_path)
    assert wm.read() is None
    _, durum, _ = wm.plan_window(T0)
    assert durum == "ILK_ACILIS"


# ======================================================================
# ENTEGRASYON , invaryant isareti SAGLIK RAPORUNA ULASIYOR mu
# ======================================================================

def test_invaryant_isareti_saglik_raporuna_ULASIYOR(tmp_path):
    """Kalici isaret olmadan saglik katmani hatti NORMAL sanirdi.

    Bu, "kanit gecerken ozellik bozuk" sinifini kapatan entegrasyon testi:
    yalniz ERROR loglamak yetmez, DISK ISARETI saglik raporunu degistirmeli.
    """
    from tools.saglik import profil_sagligi

    (tmp_path / "invariant_violations.json").write_text(
        json.dumps({
            "son_ihlal_ts": (T0 - timedelta(hours=2)).isoformat(),
            "son_sembol": "AAPL",
            "son_mesaj": "AJAN INVARYANTI IHLALI: RiskAgent oyu bulunamadi",
            "toplam": 1,
        }),
        encoding="utf-8",
    )
    (tmp_path / "heartbeat.json").write_text(
        json.dumps({"ts": (T0 - timedelta(minutes=1)).isoformat()}), encoding="utf-8"
    )

    p = profil_sagligi("paper", str(tmp_path), T0)
    assert p.decision_pipeline.durum is Durum.DEGRADED, (
        f"invaryant ihlali saglik raporuna ulasmadi: {p.decision_pipeline}"
    )
    assert "decision_pipeline=DEGRADED" in p.ozet_metni()


def test_eski_invaryant_ihlali_hatti_sonsuza_dek_bozuk_saymiyor(tmp_path):
    """24 saatten eski bir ihlal, duzeltilmis olabilir , kalici DEGRADED yasak."""
    from tools.saglik import profil_sagligi

    (tmp_path / "invariant_violations.json").write_text(
        json.dumps({"son_ihlal_ts": (T0 - timedelta(days=5)).isoformat()}),
        encoding="utf-8",
    )
    (tmp_path / "heartbeat.json").write_text(
        json.dumps({"ts": (T0 - timedelta(minutes=1)).isoformat()}), encoding="utf-8"
    )
    p = profil_sagligi("paper", str(tmp_path), T0)
    assert p.decision_pipeline.durum is not Durum.DEGRADED


def test_bayatlik_analysis_sozlugune_tasiniyor():
    """Codex ertelememi reddetti: metadata tasimak esikleri degistirmez ama
    tasimamak saglik katmaninin bayat kanitla calisan hatti SAGLIKLI ilan
    etmesine yol acar. Kaynak kodda gercek atama olmali."""
    import io as _io
    import os as _os
    kok = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
    kaynak = _io.open(_os.path.join(kok, "stock_bot.py"), encoding="utf-8").read()
    for alan in ("fundamental_data_age_hours", "fundamental_is_stale",
                 "fundamental_data_source"):
        assert f'analysis["{alan}"]' in kaynak, f"{alan} analysis'e tasinmiyor"
