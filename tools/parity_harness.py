"""R14 canli/backtest karar yolu parity olcum harness'i.

Normal kosu yalniz versiyonlanmis tape dosyasini okur; ag veya broker istemcisi
olusturmaz. Alpaca bar yenilemesi sadece ``--refresh-tape`` ile yapilir. Bu arac
hicbir esigi veya karar mantigini duzeltmez; mevcut iki yolu cagirip sapmayi
isimlendirir.
"""
from __future__ import annotations

import argparse
import copy
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import sys
from typing import Any, Callable, Optional


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd

from backtest import BacktestEngine
from config import (
    ALPACA_API_KEY,
    ALPACA_SECRET_KEY,
    MARKET_REGIME_CONFIG,
    PAPER_AGGRESSIVE_CONFIG,
    SHORT_CONFIG,
    STOCK_CONFIG,
)
from core.agent_coordinator import AgentCoordinator
from core.agent_stats import build_agent_data_ok
from core.analyzer import TechnicalAnalyzer
from core.decision_trace import DecisionTrace, GateTrace
import core.trade_gates as trade_gates_module
from core.trade_gates import TradeGates
from utils.logger import logger


DEFAULT_TAPE_PATH = ROOT / "tests" / "fixtures" / "parity_tape.json"
DEFAULT_CLOCK_TEXT = "2026-08-26T12:00:00-07:00"
MIN_SYMBOLS = 8
MIN_BARS = 120
REQUIRED_BAR_FIELDS = ("timestamp", "open", "high", "low", "close", "volume")
REQUIRED_AGENT_FIELDS = (
    "news_score",
    "article_count",
    "social_score",
    "reddit_posts",
    "x_tweets",
    "fundamental_score",
    "fundamental_data_ok",
    "fear_greed",
)


@dataclass(frozen=True)
class CoverageRow:
    name: str
    live: bool
    backtest: bool


# Kaynak kod incelemesinin makinece raporlanan kapsami. "Var" davranislarin
# ayni oldugunu DEGIL, o asama/kapi icin bir kod yolu bulundugunu soyler.
DEFAULT_COVERAGE = (
    CoverageRow("technical_analysis", True, True),
    CoverageRow("TechAgent", True, False),
    CoverageRow("FundAgent", True, False),
    CoverageRow("SentAgent", True, False),
    CoverageRow("SocialAgent", True, False),
    CoverageRow("RiskAgent", True, False),
    CoverageRow("AgentCoordinator", True, False),
    CoverageRow("min_confidence_score", True, True),
    CoverageRow("market_regime_adjustment", True, True),
    CoverageRow("max_open_positions", True, True),
    CoverageRow("kill_switch", True, False),
    CoverageRow("equity_floor", True, False),
    CoverageRow("market_safe_zone", True, False),
    CoverageRow("geopolitical_risk", True, False),
    CoverageRow("sector_concentration", True, False),
    CoverageRow("wash_sale", True, False),
    CoverageRow("sector_rotation", True, False),
    CoverageRow("market_hours", True, False),
    CoverageRow("ema200_trend", True, False),
    CoverageRow("fundamental_gate", True, False),
    CoverageRow("earnings_gate", True, False),
    CoverageRow("loss_streak", True, False),
    CoverageRow("stock_filter", True, False),
    CoverageRow("risk_reward_gate", True, False),
    CoverageRow("multi_timeframe", True, False),
    CoverageRow("volatility_gate", True, False),
    CoverageRow("pdt_check", True, False),
    CoverageRow("pullback_queue", True, False),
    CoverageRow("long_trend_gate", False, True),
    CoverageRow("position_execution", True, True),
    CoverageRow("position_management", True, True),
    CoverageRow("partial_profit_exit", True, False),
    CoverageRow("canonical_exit_plan", True, True),
    CoverageRow("local_stop_trigger", True, True),
)

GATE_ORDER = (
    "kill_switch",
    "equity_floor",
    "market_safe_zone",
    "max_open_positions",
    "geopolitical_risk",
    "sector_concentration",
    "wash_sale",
    "min_confidence_score",
    "market_regime_adjustment",
    "sector_rotation",
    "market_hours",
    "ema200_trend",
    "fundamental_gate",
    "earnings_gate",
    "loss_streak",
    "stock_filter",
    "risk_reward_gate",
    "multi_timeframe",
    "volatility_gate",
    "pdt_check",
    "pullback_queue",
    "long_trend_gate",
)

COMMON_FIELDS = (
    "as_of",
    "bar_count",
    "tech_signal",
    "tech_confidence",
    "gate:max_open_positions",
    "gate:min_confidence_score",
    "gate:market_regime_adjustment",
    "final_action",
)


@dataclass(frozen=True)
class LookaheadResult:
    path: str
    checked: int
    violations: tuple[str, ...]

    @property
    def clean(self) -> bool:
        return self.checked > 0 and not self.violations


@dataclass(frozen=True)
class FieldParity:
    field: str
    same: int
    different: int
    data_missing: int
    different_symbols: tuple[str, ...]


@dataclass(frozen=True)
class HarnessOutcome:
    exit_code: int
    report: str
    live_traces: tuple[DecisionTrace, ...] = ()
    backtest_traces: tuple[DecisionTrace, ...] = ()
    field_parity: tuple[FieldParity, ...] = ()
    lookahead: tuple[LookaheadResult, ...] = ()
    coverage: tuple[CoverageRow, ...] = ()
    tape_kind: str = "veri_yok"


class TapeError(ValueError):
    pass


def _parse_clock(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise TapeError("saat timezone icermeli")
    return parsed


def _finite_number(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def load_tape(path: str | Path) -> dict[str, Any]:
    """Tape'i oku ve eksigi sessizce varsaymak yerine acikca reddet."""
    tape_path = Path(path)
    try:
        raw = json.loads(tape_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise TapeError(f"bant okunamadi: {exc}") from exc
    if not isinstance(raw, dict):
        raise TapeError("bant kok nesnesi sozluk olmali")
    if raw.get("schema_version") != 1:
        raise TapeError("desteklenmeyen schema_version")
    symbols = raw.get("symbols")
    if not isinstance(symbols, dict) or len(symbols) < MIN_SYMBOLS:
        raise TapeError(f"en az {MIN_SYMBOLS} sembol gerekli")
    for symbol, record in symbols.items():
        if not isinstance(record, dict):
            raise TapeError(f"{symbol}: sembol kaydi bozuk")
        bars = record.get("bars")
        if not isinstance(bars, list) or len(bars) < MIN_BARS:
            raise TapeError(f"{symbol}: en az {MIN_BARS} OHLCV bar gerekli")
        previous_stamp = ""
        for index, bar in enumerate(bars):
            if not isinstance(bar, dict):
                raise TapeError(f"{symbol}: bar {index} nesne degil")
            if any(field not in bar for field in REQUIRED_BAR_FIELDS):
                raise TapeError(f"{symbol}: bar {index} OHLCV alani eksik")
            try:
                stamp = _parse_clock(bar["timestamp"]).isoformat()
            except Exception as exc:
                raise TapeError(f"{symbol}: bar {index} zamani bozuk") from exc
            if previous_stamp and stamp <= previous_stamp:
                raise TapeError(f"{symbol}: bar zamanlari artan sirada degil")
            previous_stamp = stamp
            for field in REQUIRED_BAR_FIELDS[1:]:
                if not _finite_number(bar[field]):
                    raise TapeError(f"{symbol}: bar {index} {field}=veri_yok")
        inputs = record.get("agent_inputs")
        if not isinstance(inputs, dict):
            raise TapeError(f"{symbol}: agent_inputs veri_yok")
        missing = [field for field in REQUIRED_AGENT_FIELDS if field not in inputs]
        if missing:
            raise TapeError(f"{symbol}: ajan girdisi eksik: {','.join(missing)}")
    _parse_clock(raw.get("frozen_clock", DEFAULT_CLOCK_TEXT))
    return raw


def _frame(record: dict[str, Any], cutoff: int) -> pd.DataFrame:
    rows = record["bars"][: cutoff + 1]
    frame = pd.DataFrame(rows)
    frame.index = pd.to_datetime(frame.pop("timestamp"), utc=True)
    for field in REQUIRED_BAR_FIELDS[1:]:
        frame[field] = pd.to_numeric(frame[field], errors="coerce")
    return frame


def _live_config() -> dict[str, Any]:
    return copy.deepcopy(STOCK_CONFIG)


def _backtest_config() -> dict[str, Any]:
    # BacktestEngine.__init__ ag istemcisi kurdugu icin onu cagirmaz; asagidaki
    # kopya, constructor'daki varsayilan paper-aggressive override dongusudur.
    config = copy.deepcopy(STOCK_CONFIG)
    for key, value in PAPER_AGGRESSIVE_CONFIG.items():
        if key.startswith("short_"):
            continue
        if key.startswith("enable_") or key.startswith("prefer_"):
            continue
        config[key] = copy.deepcopy(value)
    return config


@contextmanager
def _quiet_runtime_logs():
    old = logger.disabled
    logger.disabled = True
    try:
        yield
    finally:
        logger.disabled = old


@contextmanager
def _frozen_trade_gate_clock(clock: datetime):
    """TradeGates loss-streak saatini disaridan verilen saate bagla."""
    original = trade_gates_module.datetime
    frozen = clock

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is not None:
                return frozen.astimezone(tz)
            return frozen.replace(tzinfo=None)

    trade_gates_module.datetime = FrozenDateTime
    try:
        yield
    finally:
        trade_gates_module.datetime = original


class _FixtureMarketHours:
    def __init__(self, gate_inputs: dict[str, Any]):
        self.inputs = gate_inputs

    def get_market_status(self) -> dict[str, Any]:
        allowed = bool(self.inputs.get("market_trading_allowed", True))
        return {
            "is_trading_allowed": allowed,
            "reason": "fixture_acik" if allowed else "fixture_kapali",
        }

    def should_allow_extended_hours(self, _confidence: float) -> bool:
        return bool(self.inputs.get("extended_hours_allowed", False))


class _FixtureEarnings:
    def __init__(self, gate_inputs: dict[str, Any]):
        self.inputs = gate_inputs

    def should_avoid_trading(self, _symbol: str) -> tuple[bool, str]:
        avoid = bool(self.inputs.get("earnings_avoid", False))
        reason = str(self.inputs.get("earnings_reason", "fixture_earnings"))
        return avoid, reason


class _FixturePDT:
    def __init__(self, gate_inputs: dict[str, Any]):
        self.inputs = gate_inputs

    def can_day_trade(self) -> tuple[bool, str]:
        allowed = bool(self.inputs.get("pdt_allowed", True))
        return allowed, "fixture_pdt_ok" if allowed else "fixture_pdt_limit"


class _FixtureBot:
    def __init__(
        self, frame: pd.DataFrame, gate_inputs: dict[str, Any], clock: datetime
    ) -> None:
        self._frame = frame
        self.market_hours = _FixtureMarketHours(gate_inputs)
        self.earnings_calendar = _FixtureEarnings(gate_inputs)
        self.pdt_tracker = _FixturePDT(gate_inputs)
        self._consecutive_losses = int(gate_inputs.get("consecutive_losses", 0) or 0)
        self._symbol_consecutive_losses = {}
        self._symbol_consecutive_losses[str(gate_inputs.get("symbol", ""))] = int(
            gate_inputs.get("symbol_consecutive_losses", 0) or 0
        )
        halt_until = gate_inputs.get("loss_halt_until")
        self._loss_halt_until = (
            _parse_clock(halt_until).replace(tzinfo=None) if halt_until else None
        )
        self.clock = clock

    def get_stock_bars(self, _symbol: str, days: int = 14) -> pd.DataFrame:
        del days
        return self._frame.copy(deep=True)


def _agent_payloads(
    analysis: dict[str, Any], inputs: dict[str, Any], gate_inputs: dict[str, Any]
) -> tuple[dict, dict, dict, dict]:
    fundamental_ok = bool(inputs.get("fundamental_data_ok", False))
    fund_data = {
        "fundamental_score": inputs.get("fundamental_score", 0),
        "metrics": {"fixture": 1} if fundamental_ok else {},
    }
    news_score = float(inputs.get("news_score", 0) or 0)
    fear_greed = float(inputs.get("fear_greed", 50) or 50)
    fg_signal = "BUY" if fear_greed < 25 else (
        "SELL" if fear_greed > 75 else "NEUTRAL"
    )
    sent_data = {
        "news_score": news_score,
        "article_count": inputs.get("article_count", 0),
        "sentiment_label": (
            "BULLISH" if news_score > 0 else "BEARISH" if news_score < 0 else "NEUTRAL"
        ),
        "fear_greed_value": fear_greed,
        "fear_greed_signal": fg_signal,
    }
    social_data = {
        "social_score": inputs.get("social_score", 0),
        "reddit_posts": inputs.get("reddit_posts", 0),
        "x_tweets": inputs.get("x_tweets", 0),
        "x_sentiment": inputs.get("x_sentiment", 0),
        "wsb_hype": bool(inputs.get("wsb_hype", False)),
        "mentions_trend": inputs.get("mentions_trend", "STABLE"),
    }
    price = float(analysis.get("price", 0) or 0)
    atr = float(analysis.get("atr", 0) or 0)
    risk_data = {
        "daily_pnl_pct": gate_inputs.get("daily_pnl_pct", 0),
        "open_positions": gate_inputs.get("open_positions", 0),
        "max_positions": gate_inputs.get("max_positions", 3),
        "atr_pct": atr / price * 100 if price > 0 else 0,
        "vix": gate_inputs.get("vix", 18),
        "geopolitical_risk": gate_inputs.get("geopolitical_risk", "NORMAL"),
        "oil_signal": gate_inputs.get("oil_signal", "STABLE"),
        "equity_floor_hit": bool(gate_inputs.get("equity_floor_hit", False)),
    }
    return fund_data, sent_data, social_data, risk_data


def _gate_map(gates: list[GateTrace]) -> tuple[GateTrace, ...]:
    by_name = {gate.name: gate for gate in gates}
    return tuple(by_name.get(name, GateTrace.missing(name)) for name in GATE_ORDER)


def _live_gate_traces(
    symbol: str,
    analysis: dict[str, Any],
    decision: dict[str, Any],
    frame: pd.DataFrame,
    config: dict[str, Any],
    gate_inputs: dict[str, Any],
    clock: datetime,
) -> tuple[tuple[GateTrace, ...], bool, str]:
    signal = str(decision.get("signal", "HOLD")).upper()
    confidence = float(decision.get("confidence", 0) or 0)
    regime = str(gate_inputs.get("market_regime", "BULL")).upper()
    max_positions = int(gate_inputs.get("max_positions", config.get("max_open_positions", 3)))
    open_positions = int(gate_inputs.get("open_positions", 0) or 0)
    effective_buy = float(config.get("min_confidence_score", 50))
    effective_short = float(SHORT_CONFIG.get("short_min_confidence", 45))
    if regime == "BEAR":
        effective_buy += float(MARKET_REGIME_CONFIG.get("bear_buy_conf_increase", 10))
        effective_short -= float(MARKET_REGIME_CONFIG.get("bear_short_conf_reduction", 10))

    gates: list[GateTrace] = [
        GateTrace("kill_switch", not bool(gate_inputs.get("kill_switch_active", False)), "fixture"),
        GateTrace("equity_floor", not bool(gate_inputs.get("equity_floor_hit", False)), "fixture"),
        GateTrace("market_safe_zone", bool(gate_inputs.get("market_safe_zone", True)), "fixture"),
        GateTrace("max_open_positions", open_positions < max_positions,
                  f"{open_positions}/{max_positions}"),
        GateTrace("geopolitical_risk",
                  str(gate_inputs.get("geopolitical_risk", "NORMAL")).upper() != "CRITICAL",
                  str(gate_inputs.get("geopolitical_risk", "NORMAL"))),
        GateTrace("sector_concentration",
                  bool(gate_inputs.get("sector_concentration_allowed", True)), "fixture"),
        GateTrace("wash_sale", not bool(gate_inputs.get("wash_sale_blocked", False)),
                  str(gate_inputs.get("wash_sale_reason", "fixture"))),
    ]
    threshold = effective_buy if signal == "BUY" else effective_short
    if signal in ("BUY", "SELL"):
        gates.append(GateTrace("min_confidence_score", confidence >= threshold,
                               f"{confidence:.1f}>={threshold:.1f}"))
    else:
        gates.append(GateTrace("min_confidence_score", None, "uygulanamaz:HOLD"))
    gates.append(GateTrace("market_regime_adjustment", True, regime))

    if signal == "BUY":
        sector_allowed = bool(gate_inputs.get("sector_rotation_allowed", True))
        gates.append(GateTrace("sector_rotation", sector_allowed, "fixture"))
        bot = _FixtureBot(frame, {**gate_inputs, "symbol": symbol}, clock)
        trade_gates = TradeGates(bot)
        with _frozen_trade_gate_clock(clock):
            all_passed, block_reason = trade_gates.check_all_gates(
                symbol, analysis, config
            )

        market_status = bot.market_hours.get_market_status()
        market_pass = bool(market_status["is_trading_allowed"]) or bool(
            bot.market_hours.should_allow_extended_hours(confidence)
        )
        gates.append(GateTrace("market_hours", market_pass, market_status["reason"]))
        if config.get("ema200_trend_gate", True):
            gates.append(GateTrace("ema200_trend", bool(analysis.get("above_ema200", True)),
                                   f"above_ema200={analysis.get('above_ema200', True)}"))
        else:
            gates.append(GateTrace("ema200_trend", None, "devre_disi"))
        if config.get("fundamental_gate_enabled", False):
            data_ok = bool(analysis.get("fundamental_data_ok", False))
            score = analysis.get("fundamental_score", 0)
            try:
                fund_pass = data_ok and float(score) >= float(
                    config.get("fundamental_gate_min_score", 0)
                )
            except (TypeError, ValueError):
                fund_pass = False
            gates.append(GateTrace("fundamental_gate", fund_pass,
                                   f"data_ok={data_ok};score={score}"))
        else:
            gates.append(GateTrace("fundamental_gate", None, "devre_disi"))
        if config.get("earnings_gate_enabled", True):
            avoid, reason = bot.earnings_calendar.should_avoid_trading(symbol)
            gates.append(GateTrace("earnings_gate", not avoid, reason))
        else:
            gates.append(GateTrace("earnings_gate", None, "devre_disi"))
        with _frozen_trade_gate_clock(clock):
            loss_blocked, loss_reason = trade_gates._check_loss_streak(
                symbol, analysis, config
            )
        gates.append(GateTrace("loss_streak", not loss_blocked, loss_reason or "ok"))
        losses = int(gate_inputs.get("symbol_consecutive_losses", 0) or 0)
        stock_limit = int(config.get("coin_max_consecutive_losses", 3))
        stock_pass = not config.get("coin_filter_enabled", True) or losses < stock_limit
        gates.append(GateTrace("stock_filter", stock_pass, f"{losses}/{stock_limit}"))
        if config.get("rr_gate_enabled", True):
            rr_blocked, rr_reason = trade_gates._check_rr_gate(symbol, analysis, config)
            gates.append(GateTrace("risk_reward_gate", not rr_blocked, rr_reason or "ok"))
        else:
            gates.append(GateTrace("risk_reward_gate", None, "devre_disi"))
        if config.get("multi_tf_enabled", True):
            mtf_blocked, mtf_reason = trade_gates._check_mtf(symbol, config)
            gates.append(GateTrace("multi_timeframe", not mtf_blocked, mtf_reason or "ok"))
        else:
            gates.append(GateTrace("multi_timeframe", None, "devre_disi"))
        if config.get("volatility_filter_enabled", True):
            price = float(analysis.get("price", 0) or 0)
            atr = float(analysis.get("atr", 0) or 0)
            atr_pct = atr / price if price > 0 else 0
            max_atr = float(config.get("max_atr_pct", 0.05))
            gates.append(GateTrace("volatility_gate", atr_pct <= max_atr,
                                   f"{atr_pct:.6f}<={max_atr:.6f}"))
        else:
            gates.append(GateTrace("volatility_gate", None, "devre_disi"))
        pdt_allowed, pdt_reason = bot.pdt_tracker.can_day_trade()
        gates.append(GateTrace("pdt_check", True,
                               pdt_reason if pdt_allowed else f"uyari:{pdt_reason}"))
        if config.get("pullback_queue_enabled", False):
            gates.append(GateTrace("pullback_queue", None, "veri_yok:uzamis_giris_erisim_noktasi_yok"))
        else:
            gates.append(GateTrace("pullback_queue", None, "devre_disi"))
        gates.append(GateTrace.missing("long_trend_gate"))

        pre_names = (
            "kill_switch", "equity_floor", "market_safe_zone", "max_open_positions",
            "geopolitical_risk", "sector_concentration", "wash_sale",
            "min_confidence_score", "sector_rotation",
        )
        first_pre = next((g.name for g in gates if g.name in pre_names and g.passed is False), "")
        passed = not first_pre and all_passed
        return _gate_map(gates), passed, first_pre or block_reason

    # StockBot long TradeGates zincirini SELL/HOLD kararinda cagirmez.
    for name in (
        "sector_rotation", "market_hours", "ema200_trend", "fundamental_gate",
        "earnings_gate", "loss_streak", "stock_filter", "risk_reward_gate",
        "multi_timeframe", "volatility_gate", "pdt_check", "pullback_queue",
    ):
        gates.append(GateTrace(name, None, f"uygulanamaz:{signal}"))
    gates.append(GateTrace.missing("long_trend_gate"))
    pre_block = next((g.name for g in gates if g.passed is False), "")
    passed = not pre_block
    return _gate_map(gates), passed, pre_block


def evaluate_live(
    symbol: str, record: dict[str, Any], cutoff: int, clock: datetime
) -> DecisionTrace:
    frame = _frame(record, cutoff)
    config = _live_config()
    analysis = TechnicalAnalyzer(None).analyze(frame, config)
    inputs = copy.deepcopy(record["agent_inputs"])
    gate_inputs = copy.deepcopy(record.get("gate_inputs") or {})
    analysis["fundamental_score"] = inputs.get("fundamental_score", 0)
    analysis["fundamental_data_ok"] = bool(inputs.get("fundamental_data_ok", False))
    fund_data, sent_data, social_data, risk_data = _agent_payloads(
        analysis, inputs, gate_inputs
    )
    coordinator = AgentCoordinator()
    coordinator.WEIGHTS = copy.deepcopy(
        record.get("agent_weights") or AgentCoordinator.WEIGHTS
    )
    decision = coordinator.decide(
        symbol, analysis, fund_data, sent_data, social_data, risk_data
    )
    data_ok = build_agent_data_ok(analysis, sent_data, social_data, risk_data)
    votes = {
        vote["agent"]: {
            "signal": vote["signal"],
            "confidence": float(vote["confidence"]),
            "data_ok": bool(data_ok.get(vote["agent"], False)),
        }
        for vote in decision.get("votes", [])
    }
    gates, gates_passed, block_reason = _live_gate_traces(
        symbol, analysis, decision, frame, config, gate_inputs, clock
    )
    coord_signal = str(decision.get("signal", "HOLD")).upper()
    if not gates_passed:
        final = "BLOCKED"
    elif coord_signal == "BUY":
        final = "BUY"
    elif coord_signal == "SELL":
        final = "SELL"
    else:
        final = "HOLD"
    notes = ["canli_siniflari_cagirildi", "ajan_girdileri_banttan"]
    if block_reason:
        notes.append(f"blok:{block_reason}")
    return DecisionTrace(
        symbol=symbol,
        as_of=str(record["bars"][cutoff]["timestamp"]),
        bar_count=len(frame),
        tech_signal=str(analysis.get("signal")) if analysis.get("signal") is not None else None,
        tech_confidence=float(analysis.get("confidence")) if analysis.get("confidence") is not None else None,
        agent_votes=votes,
        weighted_score=float(decision.get("weighted_score")) if decision.get("weighted_score") is not None else None,
        coordinator_confidence=float(decision.get("confidence")) if decision.get("confidence") is not None else None,
        coordinator_signal=coord_signal,
        gates=gates,
        final_action=final,
        path="live",
        notes=tuple(notes),
    )


def evaluate_backtest(
    symbol: str, record: dict[str, Any], cutoff: int, clock: datetime
) -> DecisionTrace:
    del clock
    frame = _frame(record, cutoff)
    config = _backtest_config()
    # Gercek BacktestEngine metodu; constructor cagirilmaz, dolayisiyla Alpaca
    # istemcisi kurulmaz ve normal harness kosusu agdan tamamen kopuk kalir.
    engine = BacktestEngine.__new__(BacktestEngine)
    analysis = BacktestEngine._technical_analysis(engine, frame.tail(100), config)
    if analysis is None:
        raise TapeError(f"{symbol}: backtest teknik analiz veri_yok")
    signal = str(analysis.get("signal", "HOLD")).upper()
    confidence = float(analysis.get("confidence", 0) or 0)
    gate_inputs = record.get("gate_inputs") or {}
    regime = str(gate_inputs.get("market_regime", "BULL")).upper()
    buy_threshold = float(config.get("min_confidence_score", 40))
    short_threshold = float(SHORT_CONFIG.get("short_min_confidence", 45))
    if regime == "BEAR":
        buy_threshold += 10
        short_threshold -= 10
    threshold = buy_threshold if signal == "BUY" else short_threshold
    threshold_gate = (
        GateTrace("min_confidence_score", confidence >= threshold,
                  f"{confidence:.1f}>={threshold:.1f}")
        if signal in ("BUY", "SHORT")
        else GateTrace("min_confidence_score", None, "uygulanamaz:HOLD")
    )
    max_positions = int(config.get("max_open_positions", 8))
    open_positions = int(gate_inputs.get("open_positions", 0) or 0)
    long_trend_enabled = False  # Backtest'in env hook'u varsayilan kosuda kapali.
    gates = [
        GateTrace.missing("kill_switch"),
        GateTrace.missing("equity_floor"),
        GateTrace.missing("market_safe_zone"),
        GateTrace("max_open_positions", open_positions < max_positions,
                  f"{open_positions}/{max_positions}"),
        GateTrace.missing("geopolitical_risk"),
        GateTrace.missing("sector_concentration"),
        GateTrace.missing("wash_sale"),
        threshold_gate,
        GateTrace("market_regime_adjustment", True, regime),
        GateTrace.missing("sector_rotation"),
        GateTrace.missing("market_hours"),
        GateTrace.missing("ema200_trend"),
        GateTrace.missing("fundamental_gate"),
        GateTrace.missing("earnings_gate"),
        GateTrace.missing("loss_streak"),
        GateTrace.missing("stock_filter"),
        GateTrace.missing("risk_reward_gate"),
        GateTrace.missing("multi_timeframe"),
        GateTrace.missing("volatility_gate"),
        GateTrace.missing("pdt_check"),
        GateTrace.missing("pullback_queue"),
        GateTrace("long_trend_gate", None, "devre_disi") if not long_trend_enabled else
        GateTrace("long_trend_gate", analysis.get("trend") == "UPTREND", str(analysis.get("trend"))),
    ]
    max_pass = open_positions < max_positions
    threshold_pass = threshold_gate.passed is not False
    trend_pass = not long_trend_enabled or signal != "BUY" or analysis.get("trend") == "UPTREND"
    if signal == "BUY" and max_pass and threshold_pass and trend_pass:
        final = "BUY"
    elif signal == "SHORT" and max_pass and threshold_pass:
        final = "SELL"
    elif signal == "HOLD":
        final = "HOLD"
    else:
        final = "BLOCKED"
    return DecisionTrace(
        symbol=symbol,
        as_of=str(record["bars"][cutoff]["timestamp"]),
        bar_count=len(frame),
        tech_signal=signal,
        tech_confidence=confidence,
        agent_votes=None,
        weighted_score=None,
        coordinator_confidence=None,
        coordinator_signal=None,
        gates=_gate_map(gates),
        final_action=final,
        path="backtest",
        notes=("BacktestEngine._technical_analysis_cagirildi", "paper_aggressive_config"),
    )


Evaluator = Callable[[str, dict[str, Any], int, datetime], DecisionTrace]


def _corrupt_future(record: dict[str, Any], cutoff: int) -> dict[str, Any]:
    corrupted = copy.deepcopy(record)
    for offset, bar in enumerate(corrupted["bars"][cutoff + 1 :], start=1):
        absurd = 1_000_000_000_000.0 + offset
        bar.update({
            "open": absurd,
            "high": absurd * 1.01,
            "low": absurd * 0.99,
            "close": absurd,
            "volume": 9_999_999_999_999.0,
        })
    return corrupted


def check_lookahead(
    tape: dict[str, Any], evaluator: Evaluator, path: str, clock: datetime
) -> LookaheadResult:
    violations: list[str] = []
    checked = 0
    requested = int(tape.get("lookahead_cutoff", 89))
    for symbol in sorted(tape["symbols"]):
        record = tape["symbols"][symbol]
        cutoff = min(max(29, requested), len(record["bars"]) - 2)
        if cutoff < 29 or cutoff >= len(record["bars"]) - 1:
            continue
        checked += 1
        try:
            original = evaluator(symbol, record, cutoff, clock).to_json()
            corrupted = evaluator(
                symbol, _corrupt_future(record, cutoff), cutoff, clock
            ).to_json()
            if original != corrupted:
                violations.append(symbol)
        except Exception as exc:
            violations.append(f"{symbol}:hata:{type(exc).__name__}")
    return LookaheadResult(path, checked, tuple(violations))


def _gate_value(trace: DecisionTrace, name: str) -> Optional[bool]:
    for gate in trace.gates:
        if gate.name == name:
            return gate.passed
    return None


def _field_value(trace: DecisionTrace, field: str) -> Any:
    if field.startswith("gate:"):
        return _gate_value(trace, field.split(":", 1)[1])
    return getattr(trace, field)


def compare_fields(
    live: tuple[DecisionTrace, ...], backtest: tuple[DecisionTrace, ...]
) -> tuple[FieldParity, ...]:
    live_by_key = {(item.symbol, item.as_of): item for item in live}
    backtest_by_key = {(item.symbol, item.as_of): item for item in backtest}
    keys = sorted(set(live_by_key) | set(backtest_by_key))
    rows: list[FieldParity] = []
    for field in COMMON_FIELDS:
        same = different = data_missing = 0
        symbols: list[str] = []
        for key in keys:
            left = live_by_key.get(key)
            right = backtest_by_key.get(key)
            if left is None or right is None:
                data_missing += 1
                continue
            a = _field_value(left, field)
            b = _field_value(right, field)
            if a is None or b is None:
                if a is None and b is None:
                    same += 1
                else:
                    data_missing += 1
            elif a == b:
                same += 1
            else:
                different += 1
                symbols.append(key[0])
        rows.append(FieldParity(field, same, different, data_missing, tuple(symbols)))
    return tuple(rows)


TRADING_ACTIONS = {"BUY", "SELL"}


def effective_action(action: Any) -> str:
    """Etiketi degil DAVRANISI dondur: islem acilir mi, acilmaz mi.

    `HOLD` ve `BLOCKED` farkli SEBEPLERDIR ama ayni SONUCTUR (islem yok).
    Etiket mutabakati bu ikisini fark sayar; bu yuzden davranis mutabakati
    ayrica raporlanir , yoksa "%12.5 mutabakat" satiri, iki yol da hicbir
    islem acmadigi halde davranis sapmasi varmis gibi okunur.
    """
    return "ISLEM" if str(action or "").upper() in TRADING_ACTIONS else "ISLEM_YOK"


def _blocking_gates(trace: Any) -> str:
    """Bir iz'i bloklayan kapilari isimleriyle dondur (yoksa bos metin)."""
    try:
        names = [
            str(gate.name)
            for gate in (getattr(trace, "gates", None) or ())
            if getattr(gate, "passed", None) is False
        ]
    except Exception:
        return ""
    return ",".join(names)


def parity_exit_code(
    field_rows: tuple[FieldParity, ...],
    coverage: tuple[CoverageRow, ...],
    lookahead: tuple[LookaheadResult, ...],
) -> int:
    field_problem = any(row.different or row.data_missing for row in field_rows)
    coverage_problem = any(row.live != row.backtest for row in coverage)
    lookahead_problem = any(not result.clean for result in lookahead)
    return 1 if field_problem or coverage_problem or lookahead_problem else 0


def _pct(numerator: int, denominator: int) -> str:
    return "veri_yok" if denominator <= 0 else f"{numerator / denominator * 100:.2f}%"


def render_report(
    tape: dict[str, Any],
    clock: datetime,
    live: tuple[DecisionTrace, ...],
    backtest: tuple[DecisionTrace, ...],
    fields: tuple[FieldParity, ...],
    lookahead: tuple[LookaheadResult, ...],
    coverage: tuple[CoverageRow, ...],
    exit_code: int,
) -> str:
    kind = str(tape.get("tape_kind", "veri_yok")).upper()
    lines = [
        "R14 PARITY RAPORU",
        f"Saat (enjekte): {clock.isoformat()}",
        f"Bant: {kind} | {tape.get('source_detail', 'veri_yok')}",
        f"Karar sayisi: {len(live)}",
        "",
        "KAPSAM TABLOSU",
    ]
    only_live = sum(row.live and not row.backtest for row in coverage)
    lines.append(f"Yalniz CANLI A yolunda bulunan kapi/asama: {only_live}")
    lines.append("kapi/asama | CANLI A | BACKTEST B")
    for row in coverage:
        lines.append(
            f"{row.name} | {'VAR' if row.live else 'YOK'} | "
            f"{'VAR' if row.backtest else 'YOK'}"
        )

    lines.extend(["", "SAPMA TABLOSU", "alan | ayni | farkli | veri_yok | toplam | fark_sembolleri"])
    for row in fields:
        total = row.same + row.different + row.data_missing
        symbols = ",".join(row.different_symbols) or "-"
        lines.append(
            f"{row.field} | {row.same} | {row.different} | "
            f"{row.data_missing} | {total} | {symbols}"
        )

    live_by = {trace.symbol: trace for trace in live}
    backtest_by = {trace.symbol: trace for trace in backtest}
    symbols = sorted(set(live_by) & set(backtest_by))
    same_final = sum(
        live_by[symbol].final_action == backtest_by[symbol].final_action
        for symbol in symbols
    )
    lines.extend([
        "",
        "NIHAI AKSIYON MUTABAKATI",
        f"{same_final}/{len(symbols)} = {_pct(same_final, len(symbols))}",
    ])
    for symbol in symbols:
        left = live_by[symbol].final_action
        right = backtest_by[symbol].final_action
        marker = "AYNI" if left == right else "FARK"
        why = _blocking_gates(live_by[symbol])
        why_text = f" | bloklayan={why}" if why else ""
        lines.append(
            f"{symbol} | CANLI={left} | BACKTEST={right} | {marker}{why_text}"
        )

    same_effective = sum(
        effective_action(live_by[symbol].final_action)
        == effective_action(backtest_by[symbol].final_action)
        for symbol in symbols
    )
    lines.extend([
        "",
        "ETKIN AKSIYON MUTABAKATI (islem acilir mi / acilmaz mi)",
        f"{same_effective}/{len(symbols)} = {_pct(same_effective, len(symbols))}",
    ])
    for symbol in symbols:
        left = effective_action(live_by[symbol].final_action)
        right = effective_action(backtest_by[symbol].final_action)
        marker = "AYNI" if left == right else "FARK"
        lines.append(f"{symbol} | CANLI={left} | BACKTEST={right} | {marker}")
    if symbols and same_effective == len(symbols) and same_final != len(symbols):
        lines.append(
            "NOT: iki yol da bu bantta AYNI seyi yapti (hicbir islem acilmadi); "
            "etiket farki SEBEP farkidir, davranis farki DEGILDIR. Kapsam "
            "tablosundaki eksik kapilar bu mutabakatin bu banda ozgu oldugunu "
            "gosterir , genel bir parity kaniti SAYILMAZ."
        )

    lines.extend(["", "ILERIYE-BAKIS TESTI"])
    for result in lookahead:
        if result.clean:
            lines.append(
                f"{result.path.upper()}: ileriye bakis yok "
                f"({result.checked}/{result.checked} karar degismedi)"
            )
        elif result.checked <= 0:
            lines.append(f"{result.path.upper()}: veri_yok")
        else:
            lines.append(
                f"{result.path.upper()}: ILERIYE BAKIS BULGUSU "
                f"({len(result.violations)}/{result.checked}) | "
                f"{','.join(result.violations)}"
            )
    lines.extend(["", f"SONUC: {'TAM MUTABAKAT' if exit_code == 0 else 'SAPMA VAR'}", f"Cikis kodu: {exit_code}"])
    return "\n".join(lines) + "\n"


def _data_error_report(detail: str) -> str:
    return (
        "R14 PARITY RAPORU\n"
        f"veri_yok: {detail}\n"
        "SONUC: veri_yok\n"
        "Cikis kodu: 2\n"
    )


def run_harness(
    tape_path: str | Path = DEFAULT_TAPE_PATH,
    *,
    clock: str | datetime | None = None,
    live_evaluator: Evaluator = evaluate_live,
    backtest_evaluator: Evaluator = evaluate_backtest,
    coverage: tuple[CoverageRow, ...] = DEFAULT_COVERAGE,
    network_client: Any = None,
    broker_client: Any = None,
) -> HarnessOutcome:
    # Bu iki parametre testte patlayan sahte istemci enjekte edebilmek icindir.
    # Normal karar yolunda bilerek okunmaz/cagirilmazlar.
    del network_client, broker_client
    try:
        tape = load_tape(tape_path)
        frozen_clock = _parse_clock(clock or tape.get("frozen_clock", DEFAULT_CLOCK_TEXT))
        live: list[DecisionTrace] = []
        backtest: list[DecisionTrace] = []
        with _quiet_runtime_logs():
            for symbol in sorted(tape["symbols"]):
                record = tape["symbols"][symbol]
                cutoff = len(record["bars"]) - 1
                live.append(live_evaluator(symbol, record, cutoff, frozen_clock))
                backtest.append(backtest_evaluator(symbol, record, cutoff, frozen_clock))
            lookahead = (
                check_lookahead(tape, live_evaluator, "live", frozen_clock),
                check_lookahead(tape, backtest_evaluator, "backtest", frozen_clock),
            )
        live_tuple = tuple(live)
        backtest_tuple = tuple(backtest)
        fields = compare_fields(live_tuple, backtest_tuple)
        code = parity_exit_code(fields, coverage, lookahead)
        report = render_report(
            tape, frozen_clock, live_tuple, backtest_tuple, fields,
            lookahead, coverage, code,
        )
        return HarnessOutcome(
            code, report, live_tuple, backtest_tuple, fields, lookahead,
            coverage, str(tape.get("tape_kind", "veri_yok")),
        )
    except Exception as exc:
        return HarnessOutcome(2, _data_error_report(str(exc)))


def _fixture_inputs(index: int) -> dict[str, Any]:
    patterns = (
        (18, 16, 12, 14, 7, 48),
        (-18, -16, -12, 9, 6, 82),
        (0, 0, 0, 4, 2, 50),
        (12, 14, 11, 12, 8, 42),
        (24, 20, 18, 18, 12, 35),
        (-14, -18, -11, 6, 7, 78),
        (0, 5, 22, 30, 18, 55),
        (16, 13, 14, 15, 9, 45),
    )
    fund, news, social, reddit, tweets, fear = patterns[index % len(patterns)]
    return {
        "news_score": news,
        "article_count": 6 + index,
        "social_score": social,
        "reddit_posts": reddit,
        "x_tweets": tweets,
        "fundamental_score": fund,
        "fundamental_data_ok": True,
        "fear_greed": fear,
        "x_sentiment": round(social / 100, 3),
    }


def _fixture_gates(symbol: str) -> dict[str, Any]:
    result = {
        "kill_switch_active": False,
        "equity_floor_hit": False,
        "market_safe_zone": True,
        "market_trading_allowed": True,
        "extended_hours_allowed": False,
        "open_positions": 0,
        "max_positions": 3,
        "geopolitical_risk": "NORMAL",
        "sector_concentration_allowed": True,
        "wash_sale_blocked": False,
        "wash_sale_reason": "fixture_temiz",
        "sector_rotation_allowed": True,
        "earnings_avoid": False,
        "earnings_reason": "fixture_uzak",
        "consecutive_losses": 0,
        "symbol_consecutive_losses": 0,
        "pdt_allowed": True,
        "daily_pnl_pct": 0,
        "vix": 18,
        "oil_signal": "STABLE",
        "market_regime": "BULL",
    }
    # Sentetik bant, kapilarin sadece VARLIGINI degil sonuc etkisini de olcer.
    if symbol == "META":
        result["wash_sale_blocked"] = True
        result["wash_sale_reason"] = "fixture_wash_sale"
    elif symbol == "NVDA":
        result["sector_rotation_allowed"] = False
    elif symbol == "TSLA":
        result["earnings_avoid"] = True
        result["earnings_reason"] = "fixture_earnings_yakin"
    return result


def build_synthetic_tape(clock: datetime, reason: str) -> dict[str, Any]:
    symbols = ("AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "AMD")
    bases = (180, 420, 175, 190, 125, 520, 240, 155)
    slopes = (0.0014, -0.0009, 0.0002, 0.0010, 0.0018, -0.0011, 0.0001, 0.0012)
    amplitudes = (0.025, 0.020, 0.035, 0.028, 0.055, 0.030, 0.070, 0.045)
    dates = pd.bdate_range(end=clock.date(), periods=MIN_BARS, tz="UTC")
    records: dict[str, Any] = {}
    for index, symbol in enumerate(symbols):
        bars = []
        previous = float(bases[index])
        for bar_index, stamp in enumerate(dates):
            wave = amplitudes[index] * math.sin((bar_index + index * 3) / 7.0)
            micro = 0.006 * math.cos((bar_index + index) / 3.0)
            close = bases[index] * (1 + slopes[index] * bar_index + wave + micro)
            close = max(close, 5.0)
            open_price = previous * (1 + 0.0015 * math.sin(bar_index + index))
            high = max(open_price, close) * (1.008 + (bar_index % 3) * 0.001)
            low = min(open_price, close) * (0.992 - (bar_index % 2) * 0.001)
            volume = 1_000_000 + index * 170_000 + (bar_index % 17) * 41_000
            bars.append({
                "timestamp": stamp.to_pydatetime().replace(hour=20).isoformat(),
                "open": round(open_price, 4),
                "high": round(high, 4),
                "low": round(low, 4),
                "close": round(close, 4),
                "volume": float(volume),
            })
            previous = close
        records[symbol] = {
            "bars": bars,
            "agent_inputs": _fixture_inputs(index),
            "agent_weights": copy.deepcopy(AgentCoordinator.WEIGHTS),
            "gate_inputs": _fixture_gates(symbol),
        }
    return {
        "schema_version": 1,
        "tape_kind": "sentetik",
        "source_detail": f"deterministik sentetik bant; {reason}",
        "bar_timeframe": "1Day",
        "frozen_clock": clock.isoformat(),
        "lookahead_cutoff": 89,
        "symbols": records,
    }


def _real_tape(clock: datetime, data_client: Any = None) -> dict[str, Any]:
    if data_client is None:
        if not ALPACA_API_KEY or not ALPACA_SECRET_KEY:
            raise TapeError("Alpaca anahtari yok")
        from alpaca.data.historical.stock import StockHistoricalDataClient
        data_client = StockHistoricalDataClient(ALPACA_API_KEY, ALPACA_SECRET_KEY)
    from alpaca.common.enums import Sort
    from alpaca.data.enums import DataFeed
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame

    symbols = ("AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "AMD")
    start = clock.astimezone(timezone.utc) - timedelta(days=260)
    end = clock.astimezone(timezone.utc)
    records = {}
    for index, symbol in enumerate(symbols):
        response = data_client.get_stock_bars(StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=TimeFrame.Day,
            start=start,
            end=end,
            sort=Sort.ASC,
            feed=DataFeed.IEX,
        ))
        raw_bars = list((getattr(response, "data", {}) or {}).get(symbol, []))
        if len(raw_bars) < MIN_BARS:
            raise TapeError(f"{symbol}: Alpaca yalniz {len(raw_bars)} bar dondurdu")
        bars = [{
            "timestamp": getattr(bar, "timestamp").isoformat(),
            "open": float(getattr(bar, "open")),
            "high": float(getattr(bar, "high")),
            "low": float(getattr(bar, "low")),
            "close": float(getattr(bar, "close")),
            "volume": float(getattr(bar, "volume")),
        } for bar in raw_bars[-MIN_BARS:]]
        records[symbol] = {
            "bars": bars,
            "agent_inputs": _fixture_inputs(index),
            "agent_weights": copy.deepcopy(AgentCoordinator.WEIGHTS),
            "gate_inputs": _fixture_gates(symbol),
        }
    return {
        "schema_version": 1,
        "tape_kind": "gercek",
        "source_detail": "Alpaca IEX gercek gunluk barlari; ajan girdileri sabit fixture",
        "bar_timeframe": "1Day",
        "frozen_clock": clock.isoformat(),
        "lookahead_cutoff": 89,
        "symbols": records,
    }


def refresh_tape(
    path: str | Path, clock: datetime, data_client: Any = None
) -> tuple[dict[str, Any], str]:
    try:
        tape = _real_tape(clock, data_client=data_client)
        detail = "gercek Alpaca bandi yazildi"
    except Exception as exc:
        safe_reason = " ".join(str(exc).split())[:240] or type(exc).__name__
        tape = build_synthetic_tape(clock, f"gercek veri alinamadi: {safe_reason}")
        detail = "gercek veri alinamadi; deterministik sentetik bant yazildi"
    tape_path = Path(path)
    tape_path.parent.mkdir(parents=True, exist_ok=True)
    tape_path.write_text(
        json.dumps(tape, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return tape, detail


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="R14 canli/backtest parity raporu")
    parser.add_argument("--tape", default=str(DEFAULT_TAPE_PATH))
    parser.add_argument("--clock", default=None, help="ISO-8601, timezone zorunlu")
    parser.add_argument("--refresh-tape", action="store_true")
    args = parser.parse_args(argv)
    if args.refresh_tape:
        try:
            clock = _parse_clock(args.clock or DEFAULT_CLOCK_TEXT)
            _tape, detail = refresh_tape(args.tape, clock)
            print(f"Bant yenileme: {detail}")
        except Exception as exc:
            print(_data_error_report(str(exc)), end="")
            return 2
    outcome = run_harness(args.tape, clock=args.clock)
    print(outcome.report, end="")
    return outcome.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
