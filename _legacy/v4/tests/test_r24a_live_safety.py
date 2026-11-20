"""R24a PROOF: canli emniyet kilitleri , kalici taban, bear kilidi, auto-lock.

Uc vaat:
  1. Restart tabani ASLA asagi cekemez.
  2. Tek basina LIVE_ENTRIES_ENABLED=true ters-ETF ACMAZ.
  3. Belirsizlikte bot yeni riskin HICBIRINI acmaz ve bunu restart'tan
     sonra da HATIRLAR.
"""
from __future__ import annotations

import json

import pytest

from core.risk_guard import _RISK_KINDS, can_open_new_risk
from core.safety_state import (
    TETIK_STOP_DOGRULANMADI,
    auto_lock_durumu,
    auto_lock_oku,
    auto_lock_temizle,
    auto_lock_yaz,
    guvenlik_dizini_yazilabilir,
    kilitle,
    peak_oku,
    peak_yaz,
    taban_hesapla,
    zirve_guncelle,
)


class SahteBot:
    """Kapi icin gereken en kucuk yuzey."""

    def __init__(self, state_dir, *, is_paper=False):
        self._state_dir = str(state_dir)
        self.is_paper = is_paper
        self.kill_switch = None
        self._auto_lock_memory = None
        self.bumps = []
        self.kritik = []

    def _funnel_bump(self, stage, reason=None, symbol=None):
        self.bumps.append((stage, reason))

    class _N:
        def __init__(self, kayit): self.kayit = kayit
        def notify_critical(self, baslik, mesaj): self.kayit.append((baslik, mesaj))

    @property
    def notifier(self):
        return self._N(self.kritik)


def canli_cfg(**kw):
    cfg = {"live_entries_enabled": True, "live_bear_entries_enabled": False}
    cfg.update(kw)
    return cfg


# ================================================== (a)(b) KALICI TABAN
def test_a_restart_tabani_DUSUREMEZ(tmp_path):
    """Zirve $491 kayitli, equity $450 -> taban 417.40, 382.50 DEGIL."""
    peak_yaz(str(tmp_path), 491.65)
    zirve, hata = zirve_guncelle(str(tmp_path), 450.0, is_live=True)
    assert hata is None
    assert zirve == pytest.approx(491.65)
    taban = taban_hesapla(zirve, 0.85)
    assert taban == pytest.approx(417.90, abs=0.01)
    assert taban > 450.0 * 0.85, "restart tabani asagi cekti"


def test_b_zirve_yukari_guncellenir(tmp_path):
    peak_yaz(str(tmp_path), 491.65)
    zirve, _ = zirve_guncelle(str(tmp_path), 520.0, is_live=True)
    assert zirve == pytest.approx(520.0)
    assert taban_hesapla(zirve, 0.85) == pytest.approx(442.0)
    # Diske de yazildi mi (yeni surec ayni tabani gormeli)
    kayitli, hata = peak_oku(str(tmp_path))
    assert hata is None and kayitli == pytest.approx(520.0)


def test_b2_kayit_yoksa_ilk_deger_mevcut_equity(tmp_path):
    zirve, hata = zirve_guncelle(str(tmp_path), 300.0, is_live=True)
    assert hata is None and zirve == pytest.approx(300.0)
    assert peak_oku(str(tmp_path))[0] == pytest.approx(300.0)


@pytest.mark.parametrize("bozuk", [
    "{bozuk json", "[]", '{"peak_equity": "abc"}', '{"peak_equity": 0}',
    '{"peak_equity": -5}', '{"peak_equity": null}', "",
])
def test_c_bozuk_zirve_canlida_hata_paperda_kurtarilir(tmp_path, bozuk):
    yol = tmp_path / "peak_equity.json"
    yol.write_text(bozuk, encoding="utf-8")

    # CANLI: hata doner, taban yeniden KURULMAZ
    zirve, hata = zirve_guncelle(str(tmp_path), 450.0, is_live=True)
    assert hata is not None and zirve is None

    # PAPER: mevcut equity ile yeniden kurulur
    zirve_p, hata_p = zirve_guncelle(str(tmp_path), 450.0, is_live=False)
    assert hata_p is None and zirve_p == pytest.approx(450.0)


# ============================================ (d) BEARBRAIN AYRI KILIT
@pytest.mark.parametrize("r5,bear,beklenen", [
    (False, False, "LIVE_LOCK_R5"),
    (False, True, "LIVE_LOCK_R5"),     # R5 kapali -> asil sebep R5
    (True, False, "LIVE_BEAR_LOCK"),   # R5 acik ama bear kapali
    (True, True, None),                # ikisi de acik -> izin
])
def test_d_bear_dort_kombinasyon(tmp_path, r5, bear, beklenen):
    bot = SahteBot(tmp_path)
    cfg = canli_cfg(live_entries_enabled=r5, live_bear_entries_enabled=bear)
    izin, sebep = can_open_new_risk(bot, cfg, kind="bear_etf", symbol="SH")
    if beklenen is None:
        assert izin is True, f"bear acikken reddedildi: {sebep}"
    else:
        assert (izin, sebep) == (False, beklenen)


def test_d2_stock_long_yalniz_R5e_bagli(tmp_path):
    """Bear anahtari hisse girisini ETKILEMEZ."""
    bot = SahteBot(tmp_path)
    izin, _ = can_open_new_risk(
        bot, canli_cfg(live_bear_entries_enabled=False),
        kind="stock_long", symbol="AAPL",
    )
    assert izin is True


def test_d3_paperda_bear_serbest(tmp_path):
    bot = SahteBot(tmp_path, is_paper=True)
    izin, _ = can_open_new_risk(
        bot, canli_cfg(live_entries_enabled=False, live_bear_entries_enabled=False),
        kind="bear_etf", symbol="SH",
    )
    assert izin is True, "paper canli kilitlerden etkilendi"


# ================================================ (e)(h) OTOMATIK KILIT
def test_e_kilit_yeni_surecte_de_gecerli(tmp_path):
    auto_lock_yaz(str(tmp_path), TETIK_STOP_DOGRULANMADI, "AAPL", "stop yok")
    # YENI surec = yeni bot nesnesi, bellekte hicbir sey yok
    yeni_bot = SahteBot(tmp_path)
    izin, sebep = can_open_new_risk(
        yeni_bot, canli_cfg(), kind="stock_long", symbol="AAPL",
    )
    assert (izin, sebep) == (False, "LIVE_AUTO_LOCK")


@pytest.mark.parametrize("kind", sorted(_RISK_KINDS))
def test_h_kilit_BUTUN_risk_turlerini_kapsar(tmp_path, kind):
    """Parking DAHIL. Yeni bir risk turu eklenirse test kendiliginden kapsar."""
    auto_lock_yaz(str(tmp_path), TETIK_STOP_DOGRULANMADI, "AAPL")
    bot = SahteBot(tmp_path)
    izin, sebep = can_open_new_risk(bot, canli_cfg(), kind=kind, symbol="X")
    assert (izin, sebep) == (False, "LIVE_AUTO_LOCK"), f"{kind} kilitten kacti"


def test_h2_parking_kilit_YOKKEN_R5den_muaf_kalir(tmp_path):
    """Kilit kalkinca parking eski muafiyetini korumali (regresyon)."""
    bot = SahteBot(tmp_path)
    izin, _ = can_open_new_risk(
        bot, canli_cfg(live_entries_enabled=False),
        kind="index_parking", symbol="SPY",
    )
    assert izin is True


def test_h3_paperda_kilit_yazilmaz_ve_okunmaz(tmp_path):
    auto_lock_yaz(str(tmp_path), TETIK_STOP_DOGRULANMADI, "AAPL")
    bot = SahteBot(tmp_path, is_paper=True)
    izin, _ = can_open_new_risk(bot, canli_cfg(), kind="stock_long", symbol="A")
    assert izin is True, "paper canli otomatik kilitten etkilendi"


# ============================================== (i)(j) FAIL-CLOSED OKUMA
def test_i_bozuk_kilit_kaydi_KILITLI_sayilir(tmp_path):
    (tmp_path / "live_auto_lock.json").write_text("{bozuk", encoding="utf-8")
    bot = SahteBot(tmp_path)
    kilitli, sebep = auto_lock_durumu(bot, str(tmp_path))
    assert kilitli is True and "OKUNAMADI" in sebep
    izin, red = can_open_new_risk(bot, canli_cfg(), kind="stock_long", symbol="A")
    assert (izin, red) == (False, "LIVE_AUTO_LOCK")


def test_i2_kilit_yazilamazsa_bellek_ici_halt_ve_alarm(tmp_path, monkeypatch):
    bot = SahteBot(tmp_path)
    monkeypatch.setattr(
        "core.safety_state.auto_lock_yaz", lambda *a, **k: False,
    )
    kilitle(bot, TETIK_STOP_DOGRULANMADI, "AAPL", "disk dolu")
    assert bot._auto_lock_memory["kalici"] is False
    assert bot.kritik and bot.kritik[0][0] == "AUTO_LOCK_YAZILAMADI"
    kilitli, _ = auto_lock_durumu(bot, str(tmp_path))
    assert kilitli is True, "yazilamayan kilit bellek-ici halt uretmedi"


def test_i3_yazilamaz_guvenlik_dizini_canli_girisi_reddeder(tmp_path, monkeypatch):
    bot = SahteBot(tmp_path)
    monkeypatch.setattr(
        "core.safety_state.guvenlik_dizini_yazilabilir",
        lambda _d: (False, "izin yok"),
    )
    izin, sebep = can_open_new_risk(bot, canli_cfg(), kind="stock_long", symbol="A")
    assert (izin, sebep) == (False, "LIVE_AUTO_LOCK")


def test_i4_yazilabilirlik_kontrolu_gercekten_yaziyor(tmp_path):
    ok, hata = guvenlik_dizini_yazilabilir(str(tmp_path))
    assert ok is True and hata is None
    ok2, hata2 = guvenlik_dizini_yazilabilir(str(tmp_path / "a" / "b" / "c"))
    assert ok2 is True, "alt dizin olusturulamadi"


def test_j_bozuk_kill_kaydi_canlida_AKTIF_kill(tmp_path, monkeypatch):
    """Bozuk kill_switch.json 'yok' sayilamaz , fail-closed."""
    import config as config_module
    from core.kill_switch import KillSwitch

    yol = tmp_path / "kill_switch.json"
    yol.write_text("{bozuk json", encoding="utf-8")

    monkeypatch.setattr(config_module, "TRADING_MODE", "live", raising=False)
    ks = KillSwitch(kill_file=str(yol))
    assert ks.is_killed is True
    assert "OKUNAMADI" in ks.kill_reason

    monkeypatch.setattr(config_module, "TRADING_MODE", "paper", raising=False)
    ks_paper = KillSwitch(kill_file=str(yol))
    assert ks_paper.is_killed is False, "paper ogrenme akisi bozuk dosyayla durdu"


# ==================================================== TEMIZLEME KOMUTU
def test_l_temizleme_kilidi_kaldirir_ve_kayit_gider(tmp_path):
    auto_lock_yaz(str(tmp_path), TETIK_STOP_DOGRULANMADI, "AAPL")
    assert auto_lock_oku(str(tmp_path))[0] is not None
    assert auto_lock_temizle(str(tmp_path)) is True
    kayit, hata = auto_lock_oku(str(tmp_path))
    assert kayit is None and hata is None


def test_l2_kilit_yokken_temizleme_hata_vermez(tmp_path):
    assert auto_lock_temizle(str(tmp_path)) is True


# ==================================================== BOT ENTEGRASYONU
def test_bot_tabani_kalici_zirveden_kurar(tmp_path, monkeypatch):
    """_emniyet_tabani_kur gercek yoldan tabani zirveden hesaplar."""
    import stock_bot as sb

    peak_yaz(str(tmp_path), 491.65)
    bot = sb.StockBot.__new__(sb.StockBot)
    bot._state_dir = str(tmp_path)
    bot.is_paper = False
    bot._auto_lock_memory = None
    taban = bot._emniyet_tabani_kur(450.0, {"equity_floor_pct": 0.85})
    assert taban == pytest.approx(417.90, abs=0.01)


def test_bot_bozuk_zirve_canlida_kendini_kilitler(tmp_path):
    import stock_bot as sb

    (tmp_path / "peak_equity.json").write_text("{bozuk", encoding="utf-8")
    bot = sb.StockBot.__new__(sb.StockBot)
    bot._state_dir = str(tmp_path)
    bot.is_paper = False
    bot._auto_lock_memory = None
    bot.equity_floor = 400.0
    kritik = []

    class _N:
        def notify_critical(self, b, m): kritik.append((b, m))
    bot.notifier = _N()

    taban = bot._emniyet_tabani_kur(450.0, {"equity_floor_pct": 0.85})
    assert taban == pytest.approx(400.0), "bozuk kayitta taban yeniden kuruldu"
    assert bot._auto_lock_memory is not None, "bozuk zirve kilit yazmadi"
    assert kritik and kritik[0][0] == "PEAK_KAYDI_BOZUK"
    # Ve diskte de kilit var , yeni surec de gorur
    assert auto_lock_oku(str(tmp_path))[0] is not None


# ============================================ (k) SAGLIK ARACI ENTEGRASYONU
def test_k_saglik_otomatik_kilidi_ADIYLA_soyler(tmp_path, monkeypatch):
    """Funnel temiz olsa bile arac KILITLI (OTOMATIK) demeli ve cikis 3 vermeli."""
    from core.health_status import CIKIS_KODLARI, Durum
    import tools.saglik as saglik

    auto_lock_yaz(str(tmp_path), TETIK_STOP_DOGRULANMADI, "AAPL", "stop yok")
    from datetime import datetime, timezone
    p = saglik.profil_sagligi("live", str(tmp_path), datetime.now(timezone.utc))

    assert "KILITLI (OTOMATIK: STOP_UNVERIFIED)" in p.entry_authorization.sebep
    assert p.entry_authorization.durum is Durum.DEGRADED
    assert CIKIS_KODLARI[p.entry_authorization.durum] == 3
    assert "kilit_temizle" in p.entry_authorization.sebep


def test_k2_saglik_bozuk_kilit_kaydini_DEGRADED_gosterir(tmp_path):
    from core.health_status import Durum
    import tools.saglik as saglik
    from datetime import datetime, timezone

    (tmp_path / "live_auto_lock.json").write_text("{bozuk", encoding="utf-8")
    p = saglik.profil_sagligi("live", str(tmp_path), datetime.now(timezone.utc))
    assert p.entry_authorization.durum is Durum.DEGRADED
    assert "OKUNAMADI" in p.entry_authorization.sebep


def test_k3_saglik_paperda_otomatik_kilide_bakmaz(tmp_path):
    from core.health_status import Durum
    import tools.saglik as saglik
    from datetime import datetime, timezone

    auto_lock_yaz(str(tmp_path), TETIK_STOP_DOGRULANMADI, "AAPL")
    p = saglik.profil_sagligi("paper", str(tmp_path), datetime.now(timezone.utc))
    assert p.entry_authorization.durum is Durum.SAGLIKLI


# ================================ (g) GONDERIM SONRASI ISTISNA -> KILIT
def test_g_gonderim_sonrasi_istisna_kilit_yazar(tmp_path, monkeypatch):
    """Emir GONDERILDIKTEN sonraki istisna 'dolum olmus olabilir' demektir."""
    from core.executor import OrderExecutor

    bot = SahteBot(tmp_path)
    monkeypatch.setattr("config.TRADING_MODE", "live", raising=False)
    OrderExecutor._auto_lock_safe(bot, "POST_SUBMIT_EXCEPTION", "AAPL", "timeout")
    assert bot._auto_lock_memory is not None
    assert auto_lock_oku(str(tmp_path))[0]["sebep"] == "POST_SUBMIT_EXCEPTION"


def test_g2_paperda_gonderim_istisnasi_kilit_YAZMAZ(tmp_path):
    from core.executor import OrderExecutor

    bot = SahteBot(tmp_path, is_paper=True)
    OrderExecutor._auto_lock_safe(bot, "POST_SUBMIT_EXCEPTION", "AAPL", "timeout")
    assert bot._auto_lock_memory is None
    assert auto_lock_oku(str(tmp_path))[0] is None
