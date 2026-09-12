"""R20 SALDIRGAN INCELEME (Claude, Level 10) , Codex suite'inin denemedigi uc durumlar.

Buradaki her test R20'nin MUTLAK vaadini hedefler:
"Hicbir girdi zarar serisini 24 saatten uzun kalici yapamaz."
Config zehirlenmesi, bozuk disk sekilleri, devralinmis pozisyon ve sinir anlari.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from core.streak import decay_loss_streaks, update_loss_streak
from core.trade_gates import TradeGates

from tests.test_r20_streak_decay import (
    bare_bot, freeze_gate_clock, gate_config, read_profile, set_profile, write_state,
)

UTC = timezone.utc


# ---------------------------------------------------------------- config zehri
@pytest.mark.parametrize("zehir", [
    999, 10**9, float("inf"), -5, "abc", None, float("nan"), True, [], {},
])
def test_decay_config_zehri_24_saatlik_tavani_asamaz(monkeypatch, tmp_path, zehir):
    """Bozuk/asiri decay_hours bile seriyi 25. saatte ayakta tutamaz."""
    set_profile(monkeypatch, "live")
    now = datetime.now(UTC)
    path = tmp_path / "state_live" / "bot_positions.json"
    write_state(path, "live", 2, (now - timedelta(hours=25)).isoformat())
    bot = bare_bot(path)
    bot._load_position_metadata()
    freeze_gate_clock(monkeypatch, now)

    cfg = gate_config()
    cfg["loss_streak_decay_hours"] = zehir
    assert TradeGates(bot)._check_loss_streak("META", {"confidence": 47}, cfg) == (False, "")
    assert bot._consecutive_losses == 0


@pytest.mark.parametrize("zehir", [999, 10**9, float("inf"), "abc", None])
def test_sembol_decay_config_zehri_de_asamaz(monkeypatch, tmp_path, zehir):
    """Sembol filtresi de bozuk config ile kalici kilide donusemez."""
    set_profile(monkeypatch, "live")
    now = datetime.now(UTC)
    path = tmp_path / "state_live" / "bot_positions.json"
    write_state(
        path, "live", 0, None, symbol_losses=3,
        symbol_last_loss_at=(now - timedelta(hours=25)).isoformat(),
    )
    bot = bare_bot(path)
    bot._load_position_metadata()
    freeze_gate_clock(monkeypatch, now)

    cfg = gate_config()
    cfg["symbol_loss_decay_hours"] = zehir
    assert TradeGates(bot).check_all_gates("META", {"confidence": 99}, cfg) == (True, "")


def test_halt_hours_999_olsa_bile_25_saatte_serbest(monkeypatch, tmp_path):
    """KRITIK: halt_hours decay gibi kirpilmiyor. Seri sondugu icin HALT da dusmeli."""
    set_profile(monkeypatch, "live")
    now = datetime.now(UTC)
    path = tmp_path / "state_live" / "bot_positions.json"
    write_state(path, "live", 4, (now - timedelta(hours=25)).isoformat())
    bot = bare_bot(path)
    bot._load_position_metadata()
    freeze_gate_clock(monkeypatch, now)

    cfg = gate_config()
    cfg["loss_streak_halt_hours"] = 999
    assert TradeGates(bot)._check_loss_streak("META", {"confidence": 1}, cfg) == (False, "")
    assert bot._consecutive_losses == 0


def test_tam_24_saat_sinirinda_soner(monkeypatch, tmp_path):
    """Sinir ani: tam 24.0 saat >= esigine girmeli, 1 saniye eksigi girmemeli."""
    set_profile(monkeypatch, "live")
    now = datetime.now(UTC)
    path = tmp_path / "state_live" / "bot_positions.json"

    write_state(path, "live", 2, (now - timedelta(hours=24)).isoformat())
    bot = bare_bot(path)
    bot._load_position_metadata()
    freeze_gate_clock(monkeypatch, now)
    assert TradeGates(bot)._check_loss_streak("META", {"confidence": 47}, gate_config()) == (False, "")

    path2 = tmp_path / "state_live2" / "bot_positions.json"
    write_state(path2, "live", 2, (now - timedelta(hours=24) + timedelta(seconds=1)).isoformat())
    bot2 = bare_bot(path2)
    bot2._load_position_metadata()
    assert TradeGates(bot2)._check_loss_streak("META", {"confidence": 47}, gate_config()) == (
        True, "LOSS_STREAK_WARN"
    )


# ------------------------------------------------------------ bozuk disk sekli
@pytest.mark.parametrize("bozuk", [
    [], "duz-metin", 42, None,
    {"live": []}, {"live": "bozuk"}, {"live": {"consecutive_losses": "iki"}},
    {"live": {"consecutive_losses": 2, "last_loss_at": "tarih-degil", "symbols": []}},
    {"live": {"consecutive_losses": -7, "last_loss_at": None, "symbols": {"META": 3}}},
])
def test_bozuk_streaks_by_profile_cokertmez_ve_kilitlemez(monkeypatch, tmp_path, bozuk):
    """Bozuk disk sekli ne cokertmeli ne de kalici kilit uretmeli."""
    set_profile(monkeypatch, "live")
    now = datetime.now(UTC)
    path = tmp_path / "state_live" / "bot_positions.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "positions": {}, "short_positions": {}, "options_positions": {},
        "streaks_by_profile": bozuk,
    }), encoding="utf-8")

    bot = bare_bot(path)
    bot._load_position_metadata()
    freeze_gate_clock(monkeypatch, now)
    blocked, reason = TradeGates(bot)._check_loss_streak("META", {"confidence": 47}, gate_config())
    assert reason != "LOSS_STREAK_HALT"
    assert isinstance(bot._consecutive_losses, int) and bot._consecutive_losses >= 0


def test_pozisyon_dosyasi_hic_yoksa_cokmez(monkeypatch, tmp_path):
    """Ilk calisma: dosya yok. Seri 0, kapi acik, cokme yok."""
    set_profile(monkeypatch, "live")
    path = tmp_path / "state_live" / "bot_positions.json"
    bot = bare_bot(path)
    bot._load_position_metadata()
    freeze_gate_clock(monkeypatch, datetime.now(UTC))
    assert TradeGates(bot)._check_loss_streak("META", {"confidence": 47}, gate_config()) == (False, "")
    assert bot._consecutive_losses == 0


# ------------------------------------------------------- devralinmis pozisyon
def test_devralinmis_pozisyonun_KARI_gercek_seriyi_silemez(monkeypatch, tmp_path):
    """Spec (g) ters yonu: etiketsiz pozisyonun KARI canli seriyi sifirlamamali."""
    set_profile(monkeypatch, "live")
    now = datetime.now(UTC)
    path = tmp_path / "state_live" / "bot_positions.json"
    write_state(path, "live", 2, (now - timedelta(hours=2)).isoformat())
    bot = bare_bot(path)
    bot._load_position_metadata()
    assert bot._consecutive_losses == 2

    update_loss_streak(bot, "AAPL", +50.0, entry_profile=None)
    assert bot._consecutive_losses == 2, "devralinmis kar seriyi sildi"
    update_loss_streak(bot, "AAPL", -50.0, entry_profile="paper_aggressive")
    assert bot._consecutive_losses == 2, "yabanci profil zarari seriyi buyuttu"


# --------------------------------------------------------- profil izolasyonu
def test_diger_profil_kayitlari_yazimda_bozulmaz(monkeypatch, tmp_path):
    """Aktif profil yazilirken diger profillerin kaydi birebir korunmali."""
    set_profile(monkeypatch, "live")
    now = datetime.now(UTC)
    path = tmp_path / "state_live" / "bot_positions.json"
    yabanci = {
        "paper_aggressive": {
            "consecutive_losses": 7,
            "last_loss_at": (now - timedelta(hours=3)).isoformat(),
            "symbols": {"NVDA": {"losses": 5, "last_loss_at": (now - timedelta(hours=3)).isoformat()}},
        }
    }
    write_state(path, "live", 2, (now - timedelta(hours=25)).isoformat(), extra_profiles=yabanci)
    bot = bare_bot(path)
    bot._load_position_metadata()
    freeze_gate_clock(monkeypatch, now)
    TradeGates(bot)._check_loss_streak("META", {"confidence": 47}, gate_config())
    bot._save_position_metadata()

    assert read_profile(path, "live")["consecutive_losses"] == 0
    assert read_profile(path, "paper_aggressive") == yabanci["paper_aggressive"], (
        "yabanci profil kaydi degisti"
    )


def test_tuhaf_sembol_anahtarlari_gidis_donus(monkeypatch, tmp_path):
    """Unicode / bosluk / cok uzun sembol anahtarlari kaydi bozmamali."""
    set_profile(monkeypatch, "live")
    now = datetime.now(UTC)
    path = tmp_path / "state_live" / "bot_positions.json"
    write_state(path, "live", 0, None)
    bot = bare_bot(path)
    bot._load_position_metadata()

    semboller = ["SIRKET", "BRK.B", " ", "A" * 200, "NIPPON"]
    for sym in semboller:
        update_loss_streak(bot, sym, -1.0, now=now, entry_profile="live")
    bot._save_position_metadata()

    kayit = read_profile(path, "live")["symbols"]
    for sym in semboller:
        assert kayit[sym]["losses"] == 1, f"{sym!r} kaydi kayboldu"


def test_decay_idempotent(monkeypatch, tmp_path):
    """Ayni decay iki kez kosunca ikinci cagri degisiklik bildirmemeli."""
    set_profile(monkeypatch, "live")
    now = datetime.now(UTC)
    path = tmp_path / "state_live" / "bot_positions.json"
    write_state(path, "live", 2, (now - timedelta(hours=25)).isoformat())
    bot = bare_bot(path)
    bot._load_position_metadata()

    ilk = decay_loss_streaks(bot, gate_config(), now=now)
    ikinci = decay_loss_streaks(bot, gate_config(), now=now)
    assert ikinci is False, "idempotent degil, her cagri diske yaziyor"
    assert bot._consecutive_losses == 0
