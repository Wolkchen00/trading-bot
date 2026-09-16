"""R25 saldirgan testleri (Claude, Codex'in suite'inden bagimsiz).

Codex suite'i koordinatoru SAHTE ajanlarla surer. Burada GERCEK TechAgent,
FundAgent, SentAgent, RiskAgent analyze() yollari, gercek config ile kurulan
park yoneticisi, sinir degerleri ve risk verisi yokken fail-closed davranis
denenir.
"""
from __future__ import annotations

import re
from decimal import Decimal
from types import SimpleNamespace

import numpy as np
import pytest

import config
import stock_bot as stock_bot_module
from core import index_parking as index_parking_module
from core.agent_coordinator import AgentCoordinator, AgentVote, RiskAgent


WEIGHTS = {
    "TechAgent": 0.25,
    "FundAgent": 0.20,
    "SentAgent": 0.20,
    "SocialAgent": 0.15,
    "RiskAgent": 0.20,
}

# 16 Eyl GOOGL'a benzeyen gercek teknik girdi (MACD + Ichimoku + EMA boga).
GOOGL_TECH = {
    "rsi": 35, "macd_signal": "BULLISH", "ichimoku_signal": "BULLISH",
    "adx": 28, "ema_trend": "BULLISH", "bb_position": "MIDDLE", "tech_score": 5,
}
GOOGL_FUND = {"fundamental_score": 15, "metrics": {"eps": 19.94, "profit_margin": 0.55}}
NOTR_SENT = {"news_score": 0, "fear_greed_value": 50, "fear_greed_signal": "NEUTRAL"}
NORMAL_RISK = {
    "daily_pnl_pct": 0.0, "open_positions": 0, "max_positions": 3,
    "atr_pct": 0.69, "equity_floor_hit": False, "vix": 16.4,
    "geopolitical_risk": "NORMAL", "oil_signal": "STABLE",
}


@pytest.fixture
def koordinator(monkeypatch):
    monkeypatch.setitem(config.AGENT_CONFIG, "social_agent_enabled", False)
    c = AgentCoordinator()
    c.WEIGHTS = dict(WEIGHTS)
    return c


def _karar(c, risk=None, tech=None, fund=None, sent=None, sembol="GOOGL"):
    return c.decide(
        sembol,
        dict(GOOGL_TECH if tech is None else tech),
        dict(GOOGL_FUND if fund is None else fund),
        dict(NOTR_SENT if sent is None else sent),
        {"social_score": 0},
        dict(NORMAL_RISK if risk is None else risk),
    )


# ------------------------------------------------ gercek ajanlarla uctan uca
def test_gercek_ajanlar_googl_benzeri_bonus_alir(koordinator):
    sonuc = _karar(koordinator)
    oylar = {v["agent"]: v for v in sonuc["votes"]}
    assert oylar["TechAgent"]["signal"] == "BUY"
    assert oylar["FundAgent"]["signal"] == "BUY"
    assert oylar["SentAgent"]["signal"] == "HOLD"
    assert oylar["RiskAgent"]["signal"] == "HOLD"
    assert sonuc["signal"] == "BUY"
    assert sonuc["majority"] is True
    assert sonuc["majority_rule"] == "itirazsiz_2_oy"
    ham = abs(sonuc["weighted_score"]) * 2.0
    assert sonuc["confidence"] == pytest.approx(min(ham * 1.2, 100), abs=0.2)
    assert sonuc["confidence"] >= config.STOCK_CONFIG["min_confidence_score"]


def test_gercek_riskagent_risk_score_uretir():
    oy = RiskAgent().analyze(dict(NORMAL_RISK))
    assert oy.risk_score == 0
    assert type(oy.risk_score) is int


@pytest.mark.parametrize("degisiklik,bonus", [
    ({"vix": 26.0}, False),                    # -15: yukselmis VIX
    ({"atr_pct": 5.5}, False),                 # -15: yuksek volatilite
    ({"open_positions": 3}, False),            # -25: pozisyonlar dolu
    ({"oil_signal": "SPIKE"}, False),          # -15
    ({"geopolitical_risk": "ELEVATED"}, True), # -10: hala normal bant
    ({"daily_pnl_pct": -2.5}, False),          # -30: veto
    ({"equity_floor_hit": True}, False),       # -50: veto
])
def test_gercek_riskagent_yukselmis_risk_bonusu_keser(koordinator, degisiklik, bonus):
    risk = dict(NORMAL_RISK)
    risk.update(degisiklik)
    sonuc = _karar(koordinator, risk=risk)
    assert sonuc["majority"] is bonus
    if not bonus:
        assert sonuc["majority_rule"] is None


# --------------------------------------------- risk verisi YOKKEN fail-closed
def test_risk_verisi_bos_sozluk_bonus_vermez(koordinator):
    """stock_bot._build_risk_data istisnada {} doner (hesap sorgusu duserse).
    Bos veriyle RiskAgent 'risk normal' der; bu KOR veridir, bonus verilmemeli."""
    sonuc = _karar(koordinator, risk={})
    assert sonuc["majority"] is False
    assert sonuc["majority_rule"] is None


@pytest.mark.parametrize("vix", [0, -1.0, float("nan")])
def test_vix_bilinmiyorken_bonus_vermez(koordinator, vix):
    """Makro cagrisi duserse vix=0 kalir. VIX gercekte 0 olamaz; bilinmeyen
    piyasa riski 'normal' sayilip gevsetilmemeli."""
    risk = dict(NORMAL_RISK)
    risk["vix"] = vix
    sonuc = _karar(koordinator, risk=risk)
    assert sonuc["majority"] is False


def test_bos_risk_verisi_eski_davranisi_bozmaz():
    """Fail-closed yalniz yeni bonusu keser: RiskAgent sinyali/guveni AYNEN."""
    oy = RiskAgent().analyze({})
    assert oy.signal == "HOLD"
    assert oy.confidence == 30
    assert oy.short_boost == 0


# ------------------------------------------------------------ sinir degerleri
class _Stub:
    def __init__(self, oy):
        self.oy = oy

    def analyze(self, _):
        return self.oy


def _oy(ad, sinyal, guven, risk_score=0):
    v = AgentVote(ad, sinyal, guven, "saldirgan")
    v.short_boost = 0
    if ad == "RiskAgent":
        v.risk_score = risk_score
    return v


def _stub_karar(koordinator, tech, fund, sent=("HOLD", 0), risk_score=0):
    koordinator.tech_agent = _Stub(_oy("TechAgent", *tech))
    koordinator.fund_agent = _Stub(_oy("FundAgent", *fund))
    koordinator.sent_agent = _Stub(_oy("SentAgent", *sent))
    koordinator.risk_agent = _Stub(_oy("RiskAgent", "HOLD", 30, risk_score))
    return koordinator.decide("X", {}, {}, {}, {}, {})


def test_ws_tam_15_hold_kalir(koordinator):
    sonuc = _stub_karar(koordinator, ("BUY", 44), ("BUY", 20))
    assert 0.25 * 44 + 0.20 * 20 == 15.0
    assert sonuc["signal"] == "HOLD"
    assert sonuc["majority"] is False


def test_ws_15_ustu_bonuslu_buy(koordinator):
    sonuc = _stub_karar(koordinator, ("BUY", 44.1), ("BUY", 20))
    assert sonuc["signal"] == "BUY"
    assert sonuc["majority"] is True
    assert sonuc["confidence"] == pytest.approx((0.25 * 44.1 + 4.0) * 2.4, abs=0.1)


@pytest.mark.parametrize("skor,beklenen", [
    (np.float64(0.0), True), (np.int64(-14), True), (np.float64(-15.0), False),
    (Decimal("0"), False), (float("-inf"), False),
])
def test_risk_score_tip_sinirlari(koordinator, skor, beklenen):
    sonuc = _stub_karar(koordinator, ("BUY", 59), ("BUY", 30), risk_score=skor)
    assert sonuc["majority"] is beklenen


def test_iki_sell_itirazsiz_olsa_da_cogunluk_degil(koordinator):
    """Asimetri bilincli: SELL tarafi 3 oy ister, R25 SELL'i gevsetmez."""
    sonuc = _stub_karar(koordinator, ("SELL", 60), ("SELL", 60))
    assert sonuc["signal"] == "SELL"
    assert sonuc["majority"] is False
    assert sonuc["confidence"] == pytest.approx((15 + 12) * 2.0)


def test_karar_durumsuz_ard_arda_ayni(koordinator):
    a = _karar(koordinator)
    b = _karar(koordinator, risk={})
    c = _karar(koordinator)
    assert a["majority"] is True and c["majority"] is True
    assert a["confidence"] == c["confidence"]
    assert b["majority"] is False


def test_agirlik_invaryanti_hala_fail_closed(koordinator):
    koordinator.WEIGHTS = {k: v for k, v in WEIGHTS.items() if k != "SentAgent"}
    with pytest.raises(RuntimeError, match="AJAN INVARYANTI"):
        _karar(koordinator)


def test_unicode_sembol_ve_log_bicimi(koordinator, monkeypatch):
    satirlar = []
    import core.agent_coordinator as ac
    monkeypatch.setattr(ac.logger, "info", lambda m, *a, **k: satirlar.append(m))
    sonuc = _karar(koordinator, sembol="ÇİĞ_Ş")
    assert sonuc["majority_rule"] == "itirazsiz_2_oy"
    satir = next(s for s in satirlar if "Coordinator ÇİĞ_Ş" in s)
    # Canli log ayristiricilarinin (ve 16 Eyl tekrar oynatmasinin) kalibi:
    assert re.search(r"\[BUY:2 SELL:0 HOLD:2\] (COGUNLUK|tekil)", satir), satir


# ------------------------------------------------- park: GERCEK config ile kurulum
def _park_kur(monkeypatch, tmp_path, cfg):
    monkeypatch.setattr(
        index_parking_module, "state_path", lambda ad: str(tmp_path / ad)
    )
    bot = SimpleNamespace(client=None, _floor_block=False, bear_brain=None)
    return index_parking_module.IndexParkingManager(bot, cfg)


def test_park_gercek_stock_config_ile_yuzde_60(monkeypatch, tmp_path):
    yonetici = _park_kur(monkeypatch, tmp_path, dict(config.STOCK_CONFIG))
    assert yonetici.reserve_pct == pytest.approx(0.60)


def test_park_paper_agresif_birlesmesi_yuzde_30(monkeypatch, tmp_path):
    """stock_bot.__init__ birlesme kurali: short_ -> SHORT_CONFIG,
    enable_/prefer_ atlanir, geri kalan config'e yazilir."""
    cfg = dict(config.STOCK_CONFIG)
    for anahtar, deger in config.PAPER_AGGRESSIVE_CONFIG.items():
        if anahtar.startswith(("short_", "enable_", "prefer_")):
            continue
        cfg[anahtar] = deger
    assert _park_kur(monkeypatch, tmp_path, cfg).reserve_pct == pytest.approx(0.30)


# ------------------------------------------------------------------ tarama
def test_tarama_cache_20den_az_ise_hepsi(monkeypatch):
    monkeypatch.setitem(config.STOCK_CONFIG, "scan_max_symbols", 20)
    bot = stock_bot_module.StockBot.__new__(stock_bot_module.StockBot)
    bot.screener = SimpleNamespace(scan_cache={f"S{i}": {"score": i} for i in range(12)})
    assert len(bot._get_symbols_to_analyze()) == 12


def test_tarama_gercek_evren_20_ve_etfsiz():
    from core.stock_screener import StockScreener
    evren = StockScreener.get_all_symbols(SimpleNamespace())
    assert len(evren) == 20
    assert config.STOCK_CONFIG["scan_max_symbols"] >= len(evren)
    assert not set(evren) & {"SPY", "QQQ", "SQQQ", "SH", "SPXS"}


# ------------------------------------- bozuk girdide ESKI sert davranis korunur
@pytest.mark.parametrize("vix", ["abc", None, "16.4"])
def test_bozuk_vix_tipi_eskisi_gibi_kararı_durdurur(vix):
    """R25 oncesi: sayi olmayan VIX TypeError verir, _get_agent_decision bunu
    HOLD/0 yapar (islem yok). R25 bunu 'VIX 0 say, devam et'e cevirmemeli."""
    risk = dict(NORMAL_RISK)
    risk["vix"] = vix
    with pytest.raises(TypeError):
        RiskAgent().analyze(risk)


@pytest.mark.parametrize("veri", [None, [], "risk"])
def test_sozluk_olmayan_risk_verisi_eskisi_gibi_durdurur(veri):
    with pytest.raises(AttributeError):
        RiskAgent().analyze(veri)
