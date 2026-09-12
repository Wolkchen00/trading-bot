"""R21 PROOF: guven esigi 45 + bantlar, live VE paper_live_config.

Rock'in vaadi: 47 guvenli, kapilardan gecen bir BUY, HEM live HEM paper-livecfg
profilinde executor'da $100 emir planina doner. paper_aggressive DEGISMEZ.
"""
from __future__ import annotations

import copy

import pytest

import core.run_profile as run_profile
import stock_bot as stock_bot_module
from core.position_sizer import PositionSizer
from stock_bot import canli_karar_boyutlandirmasi_uygula, canli_karar_profili


EQUITY = 491.65          # gercek canli equity
PRICE = 100.0
ATR = 2.0


def set_profile(monkeypatch, profile: str) -> None:
    monkeypatch.setattr(run_profile, "aktif_profil", lambda: profile)


def live_config() -> dict:
    """STOCK_CONFIG kopyasi + bot kurulum yolunun yaptigi boyutlandirma."""
    from config import STOCK_CONFIG
    cfg = copy.deepcopy(STOCK_CONFIG)
    canli_karar_boyutlandirmasi_uygula(cfg)
    return cfg


def size_at(cfg: dict, confidence: float, *, equity: float = EQUITY) -> float:
    return PositionSizer().calculate_position_size(
        equity=equity, price=PRICE, atr=ATR, config=cfg,
        side="LONG", confidence=confidence,
    )["position_usd"]


# ------------------------------------------------------------------ config
def test_esik_45_ve_taban_bant_ayni():
    """Esik ile en dusuk bant AYNI olmali; yoksa esigi gecen sinyal boyutsuz kalir."""
    from config import STOCK_CONFIG
    assert STOCK_CONFIG["min_confidence_score"] == 45
    assert STOCK_CONFIG["live_conf_position_bands"][0][0] == 45, (
        "taban bant esikten farkli , esigi gecen sinyal sizer'da dusecek"
    )


# ------------------------------------------------------------ (a) live 47
def test_a_live_profil_47_guven_100_dolar(monkeypatch):
    set_profile(monkeypatch, "live")
    assert canli_karar_profili() is True
    assert size_at(live_config(), 47) == pytest.approx(100.0)


# -------------------------------------------- (b) paper_live_config 47 -> 100
def test_b_paper_live_config_47_guven_100_dolar_kelly_degil(monkeypatch):
    """KRITIK: paper-livecfg canli kanit uretiyor; Kelly yoluna DUSMEMELI."""
    set_profile(monkeypatch, "paper_live_config")
    assert canli_karar_profili() is True, (
        "paper-livecfg canli karar profili sayilmiyor , bantlar kurulmaz"
    )

    from config import STOCK_CONFIG
    cfg = copy.deepcopy(STOCK_CONFIG)
    max_pos = canli_karar_boyutlandirmasi_uygula(cfg)

    assert cfg["conf_position_bands"] == STOCK_CONFIG["live_conf_position_bands"]
    assert max_pos == STOCK_CONFIG["live_max_position_usd"]
    assert cfg["max_position_usd"] == STOCK_CONFIG["live_max_position_usd"]

    sonuc = PositionSizer().calculate_position_size(
        equity=EQUITY, price=PRICE, atr=ATR, config=cfg,
        side="LONG", confidence=47,
    )
    assert sonuc["position_usd"] == pytest.approx(100.0)
    assert "KADEMELI" in sonuc["reasoning"] or "GÜVEN" in sonuc["reasoning"], (
        f"bant yolundan gecmedi (Kelly?): {sonuc['reasoning']}"
    )


def test_b2_paper_aggressive_canli_yoldan_GECMEZ(monkeypatch):
    """paper_aggressive kendi override'lariyla kalir , canli bantlari kurulmaz."""
    set_profile(monkeypatch, "paper_aggressive")
    assert canli_karar_profili() is False


# ------------------------------------------------ (c) 44.9 esigin altinda
def test_c_44_9_esigin_altinda_executor_cagrilmaz(monkeypatch):
    """Gercek karar yolu: 44.9 BUY dalina HIC girmemeli."""
    bumps, should_buy_calls = _analyze_with_confidence(
        monkeypatch, confidence=44.9, regime="NORMAL"
    )
    assert "conf_below_min_buy" in bumps
    assert should_buy_calls == [], "esigin altindaki sinyal BUY dalina girdi"


def test_c2_44_9_sizer_da_bant_bulamaz():
    """Cift emniyet: esigin altindaki guven bant yolunda da boyut uretmez."""
    assert size_at(live_config(), 44.9) == 0.0


# ------------------------------------------------------- (d) bant kademeleri
@pytest.mark.parametrize("guven,beklenen", [
    (45, 100.0), (47, 100.0), (59.9, 100.0),
    (60, 150.0), (69.9, 150.0),
    (70, 200.0), (79.9, 200.0),
    (80, 300.0), (100, 300.0),
])
def test_d_bant_kademeleri(monkeypatch, guven, beklenen):
    set_profile(monkeypatch, "live")
    # $300 bandi equity tavanina (%62 x 491.65 = $304.8) sigsin diye gercek equity.
    assert size_at(live_config(), guven) == pytest.approx(beklenen)


def test_d2_equity_tavani_bandi_kirpar(monkeypatch):
    """Drawdown'da bant otomatik kuculur (fixed_position_max_pct korumasi)."""
    set_profile(monkeypatch, "live")
    cfg = live_config()
    kucuk_equity = 200.0
    tavan = kucuk_equity * cfg["fixed_position_max_pct"]
    assert size_at(cfg, 80, equity=kucuk_equity) == pytest.approx(tavan)


# --------------------------------------------------------------- (e) BEAR
def test_e_bear_rejimde_50_blok_55_gecer(monkeypatch):
    """BEAR +10 aynen: efektif esik 55."""
    from config import MARKET_REGIME_CONFIG, STOCK_CONFIG
    assert (
        STOCK_CONFIG["min_confidence_score"]
        + MARKET_REGIME_CONFIG.get("bear_buy_conf_increase", 10)
    ) == 55

    bumps, calls = _analyze_with_confidence(monkeypatch, confidence=50, regime="BEAR")
    assert "conf_below_min_buy" in bumps
    assert calls == [], "BEAR'da 50 guven BUY dalina girdi"

    bumps, calls = _analyze_with_confidence(monkeypatch, confidence=55, regime="BEAR")
    assert "conf_below_min_buy" not in bumps
    assert calls == ["TEST"], "BEAR'da 55 guven BUY dalina girmedi"


def test_e2_normal_rejimde_45_gecer(monkeypatch):
    bumps, calls = _analyze_with_confidence(monkeypatch, confidence=45, regime="NORMAL")
    assert "conf_below_min_buy" not in bumps
    assert calls == ["TEST"]


# ------------------------------------------------------- (f) paper_aggressive
def test_f_paper_aggressive_esigi_ve_boyutu_degismedi():
    from config import PAPER_AGGRESSIVE_CONFIG
    assert PAPER_AGGRESSIVE_CONFIG["min_confidence_score"] == 30
    assert PAPER_AGGRESSIVE_CONFIG["conf_position_bands"] == [
        [30, 2500], [45, 4000], [60, 6000], [75, 9000],
    ]
    assert PAPER_AGGRESSIVE_CONFIG["max_position_usd"] == 9000


# ---------------------------------------------------------- (g) literal tarama
def test_g_canli_esik_literali_kalmadi():
    """Canli yolda sabit 50 guven esigi varsayimi kalmamali."""
    from pathlib import Path
    import re

    kok = Path(__file__).resolve().parents[1]
    desen = re.compile(r"min_confidence_score[\"']\s*,\s*(\d+)")
    bulunan = []
    for yol in [kok / "stock_bot.py", kok / "core", kok / "tools"]:
        dosyalar = [yol] if yol.is_file() else sorted(yol.rglob("*.py"))
        for dosya in dosyalar:
            for no, satir in enumerate(
                dosya.read_text(encoding="utf-8", errors="ignore").splitlines(), 1
            ):
                for eslesme in desen.finditer(satir):
                    bulunan.append((dosya.relative_to(kok).as_posix(), no, int(eslesme.group(1))))

    # Kabul edilen yedek degerler ve GEREKCELERI:
    #   45 , canli esik (tek dogru deger)
    #   40 , backtest tarafinin KENDI tabani (canli yol degil, parity B kolu)
    #    0 , agent_stats telemetri sentinel'i: "esik kaydedilmedi" demek,
    #        bir esik VARSAYIMI degil (stock_bot.py, AgentStats.kaydet cagrisi)
    kotu = [b for b in bulunan if b[2] not in (0, 40, 45)]
    assert not kotu, f"canli esik varsayimi olan eski literaller: {kotu}"
    # 50 bir daha sizmasin: bu deger artik hicbir yerde yedek olamaz.
    assert not [b for b in bulunan if b[2] == 50], (
        f"eski 50 esigi geri geldi: {[b for b in bulunan if b[2] == 50]}"
    )


# ------------------------------------------------------------------ yardimci
def _analyze_with_confidence(monkeypatch, *, confidence: float, regime: str):
    """Gercek `_analyze_and_trade` yolunu minimal is birlikcilerle kos."""
    from config import STOCK_CONFIG

    set_profile(monkeypatch, "live")
    monkeypatch.setattr(stock_bot_module, "BOT_MODE", "both", raising=False)

    bot = stock_bot_module.StockBot.__new__(stock_bot_module.StockBot)
    bumps: list[str] = []
    should_buy_calls: list[str] = []

    class _Parking:
        def is_parking_symbol(self, _s): return False

    class _Bear:
        def short_conf_relief(self): return 0

    class _Sector:
        current_regime = "NORMAL"
        def should_buy(self, symbol):
            should_buy_calls.append(symbol)
            return False          # BUY dalina girildigini kanitlar, sonra durur

    bot.index_parking = _Parking()
    bot.bear_brain = _Bear()
    bot.sector_rotator = _Sector()
    bot._options_enabled = False
    bot._market_regime = regime
    bot._bear_breadth = {}
    bot._funnel_bump = lambda stage, **kw: bumps.append(stage)
    bot._get_technical_analysis = lambda s, c: {"price": PRICE, "atr": ATR, "confidence": 0}
    bot._get_agent_decision = lambda s, a, c: {
        "signal": "BUY", "confidence": confidence,
        "weighted_score": 25.0, "reasoning": "test",
    }

    cfg = copy.deepcopy(STOCK_CONFIG)
    canli_karar_boyutlandirmasi_uygula(cfg)
    bot._analyze_and_trade("TEST", cfg)
    return bumps, should_buy_calls
