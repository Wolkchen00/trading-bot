"""R19 , canli-config paper epoch'u: CALISTIRMA KANITINI URETEN taraf.

Kanit maddeleri RF-PLAN-4.md R19 PROOF bolumunden birebir:
  (a) canli-config modunda esik/boyut/kapi degerleri CANLI profille birebir
  (b) measured_profile() sabit donmuyor; yururlukteki profili raporluyor
  (c) profil kimligi R18'in kanonik hash'iyle AYNI algoritmadan uretiliyor
  (d) canli-config epoch'u ile PAPER_AGGRESSIVE epoch'u AYRI, gozlemler karismiyor
  (e) bu modda CANLI broker istemcisine hicbir emir cagrisi yapilmiyor
  (f) mevcut agresif paper davranisi regresyona ugramiyor
"""
from __future__ import annotations

import importlib
import io
import os

import pytest

from core.run_profile import (
    KAPI_ICIN_GECERLI,
    LIVE,
    PAPER_AGGRESSIVE,
    PAPER_LIVE_CONFIG,
    TUM_PROFILLER,
    agresif_override_uygulanir_mi,
    aktif_profil,
    kapi_icin_gecerli,
    profil_hash,
    profil_ozeti,
)

KOK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def profil(monkeypatch):
    def _set(deger):
        if deger is None:
            monkeypatch.delenv("PAPER_PROFILE", raising=False)
        else:
            monkeypatch.setenv("PAPER_PROFILE", deger)
    return _set


# ======================================================================
# (a) PROFIL SECIMI ve AGRESIF OVERRIDE
# ======================================================================

def test_a_varsayilan_agresif_paper(profil):
    """Mevcut davranis BIREBIR korunmali , varsayilan degismedi."""
    profil(None)
    assert aktif_profil() == PAPER_AGGRESSIVE
    assert agresif_override_uygulanir_mi() is True


def test_a_live_config_agresif_override_UYGULAMIYOR(profil):
    """Amac canli karar profilini paper broker'inda kosturmak."""
    profil("live_config")
    assert aktif_profil() == PAPER_LIVE_CONFIG
    assert agresif_override_uygulanir_mi() is False, (
        "canli-config modunda agresif override hala uygulaniyor"
    )


@pytest.mark.parametrize("deger", ["live_config", "live-config", "LIVE_CONFIG"])
def test_a_live_config_yazim_varyantlari(profil, deger):
    profil(deger)
    assert aktif_profil() == PAPER_LIVE_CONFIG


@pytest.mark.parametrize("deger", ["aggressive", "", "sacma", "agresif"])
def test_a_tanimsiz_deger_agresife_dusuyor(profil, deger):
    """Fail-safe: tanimsiz bir deger MEVCUT davranisi degistirmemeli."""
    profil(deger)
    assert aktif_profil() == PAPER_AGGRESSIVE


def test_a_stock_bot_merge_kosula_bagli():
    """Uretim baglantisi: merge GERCEKTEN kosula bagli mi.

    Metodun var olmasi yetmez; kaynak kodda merge'in kosullu oldugu
    dogrulanmali (yorum satirina alinmis bir kosul de gecerdi diye
    agresif_override_uygulanir_mi cagrisi ARANIYOR).
    """
    kaynak = io.open(os.path.join(KOK, "stock_bot.py"), encoding="utf-8").read()
    assert "agresif_override_uygulanir_mi()" in kaynak, (
        "merge kosula baglanmamis , live_config modu agresif override alir"
    )
    i_kosul = kaynak.index("agresif_override_uygulanir_mi()")
    i_merge = kaynak.index("for key, value in PAPER_AGGRESSIVE_CONFIG.items():")
    assert i_kosul < i_merge, "kosul merge'den SONRA geliyor"


# ======================================================================
# (b) measured_profile() SABIT DONMUYOR
# ======================================================================

def test_b_measured_profile_sabit_degil(profil):
    """Onceki surum HER ZAMAN 'PAPER_AGGRESSIVE' diyordu; raporun kendisi bu
    profilin canli kanit OLMADIGINI soyluyor, yani kapi KALICI NOT_READY
    kalirdi ve kilit hic acilmazdi."""
    import tools.olcum_raporu as olcum

    profil(None)
    a = olcum.measured_profile()
    profil("live_config")
    b = olcum.measured_profile()

    assert a.name != b.name, (
        f"measured_profile hala sabit donuyor: {a.name} == {b.name}"
    )
    assert b.name == PAPER_LIVE_CONFIG.upper()


def test_b_kapi_uygunlugu_profile_bagli(profil):
    import tools.olcum_raporu as olcum

    profil(None)
    agresif = olcum.measured_profile()
    profil("live_config")
    canli_cfg = olcum.measured_profile()

    assert getattr(agresif, "gate_eligible", None) is False, (
        "agresif paper kapi kanidi sayiliyor , canli davranisi temsil etmez"
    )
    assert getattr(canli_cfg, "gate_eligible", None) is True


def test_b_agresif_paper_kapi_kaniti_SAYILMIYOR(profil):
    profil(None)
    assert kapi_icin_gecerli() is False
    assert PAPER_AGGRESSIVE not in KAPI_ICIN_GECERLI


def test_b_live_config_kapi_kaniti_sayiliyor(profil):
    profil("live_config")
    assert kapi_icin_gecerli() is True
    assert PAPER_LIVE_CONFIG in KAPI_ICIN_GECERLI


# ======================================================================
# (c) KIMLIK TEK ALGORITMADAN , iki eksen eslesebilsin
# ======================================================================

def test_c_shadow_ledger_ayni_hash_fonksiyonunu_kullaniyor(profil):
    """Ayri hash'ler iki ekseni sonradan ESLESTIRILEMEZ yapardi."""
    import core.shadow_ledger as sl
    profil("live_config")
    assert sl.profil_hash() == profil_hash(), (
        "golge defter ve calisma profili FARKLI hash uretiyor"
    )


def test_c_profil_degisince_hash_degisiyor(profil):
    profil(None)
    h_agresif = profil_hash()
    profil("live_config")
    h_canli = profil_hash()
    assert h_agresif != h_canli, "profil adi hash'e girmiyor"


def test_c_hash_deterministik(profil):
    profil("live_config")
    assert profil_hash() == profil_hash()


# ======================================================================
# (d) EPOCH AYRIMI , gozlemler karismiyor
# ======================================================================

def test_d_epochlar_ayrisiyor(profil):
    from core.shadow_ledger import epoch_id
    profil(None)
    e_agresif = epoch_id("ayni_sha", profil_hash())
    profil("live_config")
    e_canli = epoch_id("ayni_sha", profil_hash())
    assert e_agresif != e_canli, (
        "ayni commit'te iki profil AYNI epoch'a dusuyor , gozlemler karisir"
    )


def test_d_golge_kayitlari_profil_bazinda_ayrisiyor(tmp_path, profil, monkeypatch):
    import core.shadow_ledger as sl
    from datetime import datetime, timezone

    T0 = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)
    yol = str(tmp_path / "s.jsonl")
    monkeypatch.setattr(sl, "commit_sha", lambda: "sabit_sha")

    profil(None)
    d1 = sl.ShadowLedger(path=yol, now_fn=lambda: T0)
    d1.record_lock_rejection(symbol="A", kind="stock_long",
                             block_reason="LIVE_LOCK_R5")

    profil("live_config")
    d2 = sl.ShadowLedger(path=yol, now_fn=lambda: T0)
    d2.record_lock_rejection(symbol="B", kind="stock_long",
                             block_reason="LIVE_LOCK_R5")

    assert len(d2.ozet()["epochlar"]) == 2, (
        "iki profilin gozlemleri ayni epoch'ta karisti"
    )


# ======================================================================
# (e) CANLI HESABA HICBIR EMIR GITMIYOR
# ======================================================================

def test_e_live_config_paper_broker_kullaniyor(profil):
    """Paper broker'i , canli hesap DEGIL. Isin butun anlami bu."""
    profil("live_config")
    assert aktif_profil() != LIVE
    assert aktif_profil() in (PAPER_AGGRESSIVE, PAPER_LIVE_CONFIG)


def test_e_live_modda_profil_secimi_env_ile_zorlanamaz(profil, monkeypatch):
    """PAPER_PROFILE canli modu ELE GECIREMEZ."""
    profil("live_config")
    import core.run_profile as rp
    monkeypatch.setattr(
        rp, "aktif_profil",
        rp.aktif_profil.__wrapped__ if hasattr(rp.aktif_profil, "__wrapped__")
        else rp.aktif_profil,
    )
    import config as cfg
    monkeypatch.setattr(cfg, "TRADING_MODE", "live", raising=False)
    assert rp.aktif_profil() == LIVE, (
        "PAPER_PROFILE canli modu ele gecirdi"
    )


def test_e_canli_modda_agresif_override_yok(profil, monkeypatch):
    import config as cfg
    import core.run_profile as rp
    profil(None)
    monkeypatch.setattr(cfg, "TRADING_MODE", "live", raising=False)
    assert rp.aktif_profil() == LIVE
    assert rp.agresif_override_uygulanir_mi() is False


# ======================================================================
# (f) MEVCUT AGRESIF PAPER REGRESYONA UGRAMIYOR
# ======================================================================

def test_f_agresif_profil_degerleri_degismedi():
    """R19 mevcut paper botuna DOKUNMAMALI."""
    from config import PAPER_AGGRESSIVE_CONFIG
    assert PAPER_AGGRESSIVE_CONFIG["min_confidence_score"] == 30
    assert PAPER_AGGRESSIVE_CONFIG["max_open_positions"] == 10
    assert PAPER_AGGRESSIVE_CONFIG["max_position_usd"] == 9000
    assert PAPER_AGGRESSIVE_CONFIG["scan_interval_seconds"] == 15


def test_f_canli_esikler_degismedi():
    """Canli taban degerleri R19'dan ETKILENMEMELI."""
    from config import STOCK_CONFIG
    assert STOCK_CONFIG["min_confidence_score"] == 50
    assert STOCK_CONFIG["live_entries_enabled"] is False, (
        "R5 kilidi acilmis , R19 kilide DOKUNMAMALI"
    )


def test_f_profil_ozeti_tum_profilleri_aciklıyor(profil):
    for p in TUM_PROFILLER:
        assert isinstance(p, str)
    profil("live_config")
    o = profil_ozeti()
    assert o["profil"] == PAPER_LIVE_CONFIG
    assert o["kapi_icin_gecerli"] is True
    assert "SIFIR DOLAR" in o["aciklama"]


def test_f_agresif_ozet_kanit_olmadigini_soyluyor(profil):
    profil(None)
    o = profil_ozeti()
    assert o["kapi_icin_gecerli"] is False
    assert "TEMSIL" in o["aciklama"].upper()
