"""R25 orta agresif paketinin karar, tarama, nakit ve koruma kanitlari."""
from __future__ import annotations

import copy
from datetime import date
from types import SimpleNamespace

import pytest

import config
import stock_bot as stock_bot_module
from core.agent_coordinator import AgentCoordinator, AgentVote
from core.index_parking import IndexParkingManager
from core.position_sizer import PositionSizer
from stock_bot import canli_karar_boyutlandirmasi_uygula, canli_karar_profili


WEIGHTS = {
    "TechAgent": 0.25,
    "FundAgent": 0.20,
    "SentAgent": 0.20,
    "SocialAgent": 0.15,
    "RiskAgent": 0.20,
}


class _Stub:
    def __init__(self, vote):
        self.vote = vote

    def analyze(self, _data):
        return self.vote


def _vote(name, signal, confidence, *, risk_score=...):
    vote = AgentVote(name, signal, confidence, "R25 test")
    if risk_score is not ...:
        vote.risk_score = risk_score
    vote.short_boost = 0
    return vote


def _decision(monkeypatch, *, tech, fund, sent, risk, social=None):
    monkeypatch.setitem(
        config.AGENT_CONFIG, "social_agent_enabled", social is not None
    )
    coordinator = AgentCoordinator()
    coordinator.WEIGHTS = dict(WEIGHTS)
    coordinator.tech_agent = _Stub(tech)
    coordinator.fund_agent = _Stub(fund)
    coordinator.sent_agent = _Stub(sent)
    coordinator.risk_agent = _Stub(risk)
    if social is not None:
        coordinator.social_agent = _Stub(social)
    return coordinator.decide("GOOGL", {}, {}, {}, {}, {})


def _googl(monkeypatch, *, sent_signal="HOLD", risk_score=0):
    return _decision(
        monkeypatch,
        tech=_vote("TechAgent", "BUY", 59),
        fund=_vote("FundAgent", "BUY", 30),
        sent=_vote("SentAgent", sent_signal, 10),
        risk=_vote("RiskAgent", "HOLD", 30, risk_score=risk_score),
    )


def _live_size(confidence):
    cfg = copy.deepcopy(config.STOCK_CONFIG)
    canli_karar_boyutlandirmasi_uygula(cfg)
    return PositionSizer().calculate_position_size(
        equity=490.15,
        price=100.0,
        atr=2.0,
        config=cfg,
        side="LONG",
        confidence=confidence,
    )["position_usd"]


def test_a_16_eylul_googl_itirazsiz_iki_oy(monkeypatch):
    result = _googl(monkeypatch)
    assert result["signal"] == "BUY"
    assert 59 * 0.25 + 30 * 0.20 == pytest.approx(20.75)
    assert result["weighted_score"] == pytest.approx(20.8)  # mevcut 1 hane API
    assert result["majority"] is True
    assert result["majority_rule"] == "itirazsiz_2_oy"
    assert result["confidence"] == pytest.approx(49.8, abs=0.1)
    assert result["confidence"] >= config.STOCK_CONFIG["min_confidence_score"]
    assert _live_size(result["confidence"]) == pytest.approx(100.0)


def test_b_karsi_oy_cogunluk_bonusunu_kapatir(monkeypatch):
    result = _googl(monkeypatch, sent_signal="SELL")
    assert result["signal"] == "BUY"
    assert result["weighted_score"] == pytest.approx(18.8)
    assert result["majority"] is False
    assert result["majority_rule"] is None
    assert result["confidence"] == pytest.approx(37.5)


@pytest.mark.parametrize("risk_score,expected", [(-15, False), (-14, True)])
def test_c_risk_normal_siniri(monkeypatch, risk_score, expected):
    result = _googl(monkeypatch, risk_score=risk_score)
    assert result["majority"] is expected


def test_d_risk_vetosu_buyi_hold_yapar(monkeypatch):
    result = _decision(
        monkeypatch,
        tech=_vote("TechAgent", "BUY", 59),
        fund=_vote("FundAgent", "BUY", 30),
        sent=_vote("SentAgent", "HOLD", 10),
        risk=_vote("RiskAgent", "SELL", 0, risk_score=-30),
    )
    assert result["risk_veto"] is True
    assert result["signal"] == "HOLD"
    assert result["majority"] is False


def test_e_zayif_iki_oy_buy_olmaz(monkeypatch):
    result = _decision(
        monkeypatch,
        tech=_vote("TechAgent", "BUY", 20),
        fund=_vote("FundAgent", "BUY", 20),
        sent=_vote("SentAgent", "HOLD", 0),
        risk=_vote("RiskAgent", "HOLD", 30, risk_score=0),
    )
    assert result["weighted_score"] <= 15
    assert result["signal"] == "HOLD"
    assert result["majority"] is False


def test_f_social_etkinken_iki_oy_cogunluk_degil(monkeypatch):
    result = _decision(
        monkeypatch,
        tech=_vote("TechAgent", "BUY", 59),
        fund=_vote("FundAgent", "BUY", 30),
        sent=_vote("SentAgent", "HOLD", 10),
        social=_vote("SocialAgent", "HOLD", 0),
        risk=_vote("RiskAgent", "HOLD", 30, risk_score=0),
    )
    assert result["weighted_score"] > 15
    assert result["signal"] == "BUY"
    assert result["majority"] is False


@pytest.mark.parametrize(
    "risk_score", [pytest.param(..., id="eksik"), None, float("nan"),
                    float("inf"), True, "0"]
)
def test_g_belirsiz_risk_puani_fail_closed(monkeypatch, risk_score):
    risk = _vote("RiskAgent", "HOLD", 30, risk_score=risk_score)
    result = _decision(
        monkeypatch,
        tech=_vote("TechAgent", "BUY", 59),
        fund=_vote("FundAgent", "BUY", 30),
        sent=_vote("SentAgent", "HOLD", 10),
        risk=risk,
    )
    assert result["signal"] == "BUY"
    assert result["majority"] is False
    assert result["majority_rule"] is None


def test_h_iki_sell_regresyonu(monkeypatch):
    result = _decision(
        monkeypatch,
        tech=_vote("TechAgent", "SELL", 40),
        fund=_vote("FundAgent", "SELL", 40),
        sent=_vote("SentAgent", "HOLD", 0),
        risk=_vote("RiskAgent", "HOLD", 30, risk_score=0),
    )
    assert result["weighted_score"] == pytest.approx(-18.0)
    assert result["signal"] == "SELL"
    assert result["majority"] is False
    assert result["majority_rule"] is None
    assert result["confidence"] == pytest.approx(36.0)


def test_i_uc_buy_eski_cogunluk_yolu(monkeypatch):
    result = _decision(
        monkeypatch,
        tech=_vote("TechAgent", "BUY", 20),
        fund=_vote("FundAgent", "BUY", 20),
        sent=_vote("SentAgent", "BUY", 20),
        social=_vote("SocialAgent", "HOLD", 0),
        risk=_vote("RiskAgent", "HOLD", 30, risk_score=0),
    )
    assert result["signal"] == "BUY"
    assert result["majority"] is True
    assert result["majority_rule"] == "3_oy"


@pytest.mark.parametrize(
    "confidence,expected", [(39.9, 0), (40.0, 100), (59.9, 100),
                            (60, 150), (70, 200), (80, 300)]
)
def test_j_esik_ve_canli_bantlar(confidence, expected):
    assert _live_size(confidence) == pytest.approx(expected)


def test_j_live_ve_paper_live_config_ayni_boyut():
    assert canli_karar_profili("live") is True
    assert canli_karar_profili("paper_live_config") is True
    assert _live_size(49.8) == pytest.approx(100.0)


def _scan_bot(cache):
    bot = stock_bot_module.StockBot.__new__(stock_bot_module.StockBot)
    bot.screener = SimpleNamespace(scan_cache=cache)
    return bot


def test_k_scan_cache_20_sembol_skor_sirali(monkeypatch):
    monkeypatch.setitem(config.STOCK_CONFIG, "scan_max_symbols", 20)
    cache = {f"S{i:02d}": {"score": i} for i in range(20)}
    symbols = _scan_bot(cache)._get_symbols_to_analyze()
    assert len(symbols) == 20
    assert symbols == [f"S{i:02d}" for i in reversed(range(20))]


def test_k_cache_bosken_varsayilan_havuzdan_20(monkeypatch):
    monkeypatch.setitem(config.STOCK_CONFIG, "scan_max_symbols", 20)
    monkeypatch.setitem(
        config.STOCK_CONFIG, "symbols", [f"S{i:02d}" for i in range(25)]
    )
    assert _scan_bot({})._get_symbols_to_analyze() == [
        f"S{i:02d}" for i in range(20)
    ]


@pytest.mark.parametrize("invalid", [None, True, 20.0, float("nan"), 0, -1, "20"])
def test_k_gecersiz_scan_degeri_eski_10(monkeypatch, invalid):
    monkeypatch.setitem(config.STOCK_CONFIG, "scan_max_symbols", invalid)
    cache = {f"S{i:02d}": {"score": i} for i in range(35)}
    assert len(_scan_bot(cache)._get_symbols_to_analyze()) == 10


def test_k_eksik_scan_degeri_eski_10_ve_bir_warning(monkeypatch):
    monkeypatch.delitem(config.STOCK_CONFIG, "scan_max_symbols", raising=False)
    warnings = []
    monkeypatch.setattr(stock_bot_module.logger, "warning", warnings.append)
    bot = _scan_bot({f"S{i:02d}": {"score": i} for i in range(35)})
    assert len(bot._get_symbols_to_analyze()) == 10
    assert len(bot._get_symbols_to_analyze()) == 10
    assert len(warnings) == 1


def test_k_scan_tavani_30_ve_ters_etf_filtresi(monkeypatch):
    monkeypatch.setitem(config.STOCK_CONFIG, "scan_max_symbols", 99)
    cache = {f"S{i:02d}": {"score": 100 - i} for i in range(35)}
    assert len(_scan_bot(cache)._get_symbols_to_analyze()) == 30

    monkeypatch.setitem(config.STOCK_CONFIG, "scan_max_symbols", 20)
    monkeypatch.setitem(
        stock_bot_module.MARKET_REGIME_CONFIG, "inverse_etf_symbols", ["S00"]
    )
    assert "S00" not in _scan_bot(cache)._get_symbols_to_analyze()


def test_l_park_rezervi_148_27_dolar_cozer():
    account = SimpleNamespace(equity="490.15", cash="145.82")

    class _Client:
        def get_account(self):
            return account

        def get_open_position(self, _symbol):
            raise RuntimeError("testte park pozisyonu yok")

    manager = IndexParkingManager.__new__(IndexParkingManager)
    manager.bot = SimpleNamespace(client=_Client(), _floor_block=False, bear_brain=None)
    manager.enabled = True
    manager.symbol = "SPY"
    manager.reserve_pct = 0.60
    manager.min_trade = 50.0
    manager._last_rebalance = None
    manager._last_buy_date = None
    manager._bear_unwind_date = None
    manager._save_dates = lambda: None
    buys, sells = [], []
    manager._buy = buys.append
    manager._sell = sells.append

    manager.maybe_rebalance()
    assert sells == [pytest.approx(148.27)]
    assert buys == []
    assert manager._last_rebalance == date.today()
    assert config.PAPER_AGGRESSIVE_CONFIG["index_parking_reserve_pct"] == 0.30


def test_m_canli_korumalari_aynen():
    stock = config.STOCK_CONFIG
    assert stock["max_daily_loss_pct"] == 0.05
    assert stock["equity_floor_pct"] == 0.85
    assert stock["stop_loss_pct"] == 0.04
    assert stock["stop_loss_max_pct"] == 0.06
    assert stock["loss_streak_warn"] == 2
    assert stock["loss_streak_halt"] == 4
    assert stock["loss_streak_elevated_conf"] == 70
    assert stock["max_open_positions"] == 3
    assert stock["live_max_position_usd"] == 300
    assert config.OPTIONS_CONFIG["options_enabled"] is False
