"""Ajan ve koordinator kararlarinin gunluk, toplu telemetrisi.

Karar basina olay satiri yazmaz. State tek JSON dosyasinda ET gunlerine gore
toplanir; tum IO best-effort'tur ve trading kararina geri besleme yapmaz.
"""
from __future__ import annotations

import copy
from datetime import date, datetime, timedelta
import json
import os
import time
from typing import Any, Callable
from zoneinfo import ZoneInfo

from config import state_path
from core.agent_enable import is_agent_enabled
from utils.logger import logger


SCHEMA_VERSION = 2  # R15: data_ok ikili -> uclu (ok / veri yok / politika ile kapali)
AGENT_NAMES = (
    "TechAgent",
    "FundAgent",
    "SentAgent",
    "SocialAgent",
    "RiskAgent",
)
SIGNALS = ("BUY", "SELL", "HOLD")
TECH_DATA_KEYS = (
    "price",
    "rsi",
    "macd_signal",
    "ichimoku_signal",
    "adx",
    "ema_trend",
    "bb_position",
    "tech_score",
    "atr",
    "signal",
    "buy_score",
    "sell_score",
    "volume_analysis",
)


def _positive_count(value: Any) -> bool:
    try:
        return float(value or 0) > 0
    except (TypeError, ValueError):
        return False


def build_agent_data_ok(
    analysis: dict,
    sent_data: dict,
    social_data: dict,
    risk_data: dict,
) -> dict[str, bool]:
    """Skor sifirini veri yokluguyla karistirmayan acik kaynak bayraklari."""
    analysis = analysis if isinstance(analysis, dict) else {}
    sent_data = sent_data if isinstance(sent_data, dict) else {}
    social_data = social_data if isinstance(social_data, dict) else {}
    risk_data = risk_data if isinstance(risk_data, dict) else {}
    tech_ok = any(
        key in analysis and analysis.get(key) is not None
        for key in TECH_DATA_KEYS
    )
    risk_required = {"daily_pnl_pct", "open_positions", "max_positions"}
    return {
        "TechAgent": tech_ok,
        "FundAgent": bool(analysis.get("fundamental_data_ok", False)),
        "SentAgent": _positive_count(sent_data.get("article_count", 0)),
        "SocialAgent": (
            _positive_count(social_data.get("reddit_posts", 0))
            or _positive_count(social_data.get("x_tweets", 0))
        ),
        "RiskAgent": risk_required.issubset(risk_data),
    }


def histogram_bucket(value: Any) -> str:
    """0-100 olcegini 10 puanlik, siralanabilir kovalara ayir."""
    try:
        number = max(0.0, float(value or 0))
    except (TypeError, ValueError):
        number = 0.0
    if number >= 100:
        return "100+"
    lower = int(number // 10) * 10
    return f"{lower}-{lower + 9}"


def _count(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _number(value: Any, default: float | None = None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class AgentStats:
    """ET gun bazli ajan/koordinator histogramlarini kalici tutar."""

    WRITE_INTERVAL_SECONDS = 60
    RETENTION_DAYS = 30

    def __init__(
        self,
        path: str | None = None,
        today_fn: Callable[[], object] | None = None,
    ) -> None:
        self.path = path or state_path("agent_stats.json")
        self._today_fn = today_fn
        self.days: dict[str, dict] = {}
        self._last_persist_monotonic = 0.0
        self._load()

    @staticmethod
    def _empty_agent() -> dict:
        return {
            "votes": {signal: 0 for signal in SIGNALS},
            # R15 uclu durum: "true"=veri geldi, "false"=kaynak sustu,
            # "disabled"=politika geregi kapali. Yokluk susmayla karisamaz.
            "data_ok": {"true": 0, "false": 0, "disabled": 0},
            "confidence_histogram": {},
            "last_dynamic_weight": None,
        }

    @classmethod
    def _empty_day(cls) -> dict:
        return {
            "agents": {name: cls._empty_agent() for name in AGENT_NAMES},
            "coordinator": {
                "decisions": 0,
                "abs_weighted_score_histogram": {},
                "confidence_histogram": {},
                "confidence_gte_threshold": 0,
                "ws_gt_15": 0,
                "ws_lt_neg15": 0,
                "majority": 0,
                "risk_veto": 0,
                "final_signal": {signal: 0 for signal in SIGNALS},
                "min_confidence_score": None,
                "min_confidence_score_counts": {},
            },
        }

    def _today(self) -> str:
        value = self._today_fn() if self._today_fn is not None else None
        if value is None:
            value = datetime.now(ZoneInfo("America/New_York")).date()
        if isinstance(value, datetime):
            value = value.date()
        if isinstance(value, date):
            return value.isoformat()
        return str(value)

    @staticmethod
    def _normalize_histogram(raw: Any) -> dict[str, int]:
        if not isinstance(raw, dict):
            return {}
        return {
            str(bucket): _count(count)
            for bucket, count in raw.items()
            if _count(count) > 0
        }

    @classmethod
    def _normalize_agent(cls, raw: Any) -> dict:
        result = cls._empty_agent()
        if not isinstance(raw, dict):
            return result
        votes = raw.get("votes", {})
        if isinstance(votes, dict):
            result["votes"] = {
                signal: _count(votes.get(signal, 0)) for signal in SIGNALS
            }
        data_ok = raw.get("data_ok", {})
        if isinstance(data_ok, dict):
            # Eski (surum 1) dosyalarda "disabled" anahtari YOK; 0'a duser.
            # Goc kayipsiz: eski true/false sayaclari aynen tasinir.
            result["data_ok"] = {
                "true": _count(data_ok.get("true", 0)),
                "false": _count(data_ok.get("false", 0)),
                "disabled": _count(data_ok.get("disabled", 0)),
            }
        result["confidence_histogram"] = cls._normalize_histogram(
            raw.get("confidence_histogram")
        )
        result["last_dynamic_weight"] = _number(
            raw.get("last_dynamic_weight")
        )
        return result

    @classmethod
    def _normalize_day(cls, raw: Any) -> dict:
        result = cls._empty_day()
        if not isinstance(raw, dict):
            return result
        raw_agents = raw.get("agents", {})
        if isinstance(raw_agents, dict):
            result["agents"] = {
                name: cls._normalize_agent(raw_agents.get(name))
                for name in AGENT_NAMES
            }
        raw_coord = raw.get("coordinator", {})
        if not isinstance(raw_coord, dict):
            return result
        coord = result["coordinator"]
        for field in (
            "decisions",
            "confidence_gte_threshold",
            "ws_gt_15",
            "ws_lt_neg15",
            "majority",
            "risk_veto",
        ):
            coord[field] = _count(raw_coord.get(field, 0))
        for field in (
            "abs_weighted_score_histogram",
            "confidence_histogram",
            "min_confidence_score_counts",
        ):
            coord[field] = cls._normalize_histogram(raw_coord.get(field))
        raw_signals = raw_coord.get("final_signal", {})
        if isinstance(raw_signals, dict):
            coord["final_signal"] = {
                signal: _count(raw_signals.get(signal, 0))
                for signal in SIGNALS
            }
        coord["min_confidence_score"] = _number(
            raw_coord.get("min_confidence_score")
        )
        return result

    def _load(self) -> None:
        try:
            if not os.path.exists(self.path):
                return
            with open(self.path, "r", encoding="utf-8") as handle:
                raw = json.load(handle)
            if not isinstance(raw, dict):
                return
            raw_days = raw.get("days", {})
            if isinstance(raw_days, dict):
                self.days = {
                    str(day): self._normalize_day(values)
                    for day, values in raw_days.items()
                }
        except Exception as exc:
            logger.debug(f"  Agent stats yukleme hatasi: {exc}")
            self.days = {}

    def _prune(self) -> None:
        cutoff = date.fromisoformat(self._today()) - timedelta(
            days=self.RETENTION_DAYS
        )
        retained = {}
        for day_text, values in self.days.items():
            try:
                if date.fromisoformat(day_text) >= cutoff:
                    retained[day_text] = values
            except (TypeError, ValueError):
                continue
        self.days = retained

    def _persist(self, force: bool = False) -> bool:
        try:
            now = time.monotonic()
            if (
                not force
                and self._last_persist_monotonic
                and now - self._last_persist_monotonic
                < self.WRITE_INTERVAL_SECONDS
            ):
                return False
            self._prune()
            directory = os.path.dirname(os.path.abspath(self.path))
            os.makedirs(directory, exist_ok=True)
            temp_path = f"{self.path}.tmp"
            with open(temp_path, "w", encoding="utf-8") as handle:
                json.dump(
                    {"schema_version": SCHEMA_VERSION, "days": self.days},
                    handle,
                    indent=2,
                    ensure_ascii=True,
                )
            os.replace(temp_path, self.path)
            self._last_persist_monotonic = now
            return True
        except Exception as exc:
            logger.debug(f"  Agent stats kayit hatasi: {exc}")
            return False

    def save(self) -> bool:
        return self._persist(force=True)

    @staticmethod
    def _vote_value(vote: Any, field: str, default: Any = None) -> Any:
        if isinstance(vote, dict):
            return vote.get(field, default)
        return getattr(vote, field, default)

    @staticmethod
    def _bump_histogram(histogram: dict, value: Any) -> None:
        bucket = histogram_bucket(value)
        histogram[bucket] = _count(histogram.get(bucket, 0)) + 1

    def record_decision(
        self,
        decision: dict,
        *,
        data_ok: dict[str, bool],
        dynamic_weights: dict[str, float],
        min_confidence_score: float,
    ) -> bool:
        """Bir karari gunluk toplamlara ekle; hata asla disari cikmaz."""
        try:
            if not isinstance(decision, dict):
                return False
            today = self._today()
            day = self.days.setdefault(today, self._empty_day())
            votes = decision.get("votes") or []
            for vote in votes:
                name = str(
                    self._vote_value(vote, "agent", None)
                    or self._vote_value(vote, "agent_name", "")
                )
                if name not in AGENT_NAMES:
                    continue
                signal = str(
                    self._vote_value(vote, "signal", "HOLD")
                ).upper()
                if signal not in SIGNALS:
                    signal = "HOLD"
                agent = day["agents"][name]
                agent["votes"][signal] += 1
                ok_key = "true" if bool(data_ok.get(name, False)) else "false"
                agent["data_ok"][ok_key] += 1
                self._bump_histogram(
                    agent["confidence_histogram"],
                    self._vote_value(vote, "confidence", 0),
                )
                agent["last_dynamic_weight"] = _number(
                    dynamic_weights.get(name)
                )

            # R15: politika geregi kapali ajanlar oy kumesinde HIC yok, bu yuzden
            # yukaridaki dongu onlara ugramaz. Sayacini bumplamazsak telemetride
            # "hic calismamis" ile "kapatilmis" ayirt edilemez ve kaynak geri
            # geldiginde kimse fark etmez. DISABLED_BY_POLICY ayri sayilir.
            voted = {
                str(
                    self._vote_value(v, "agent", None)
                    or self._vote_value(v, "agent_name", "")
                )
                for v in votes
            }
            for name in AGENT_NAMES:
                if name in voted:
                    continue
                if is_agent_enabled(name):
                    # Oy yok ama kapali da degil: bu bir politika durumu degildir,
                    # sessizce gecilir (mevcut davranis korunur).
                    continue
                day["agents"][name]["data_ok"]["disabled"] += 1

            coord = day["coordinator"]
            confidence = float(decision.get("confidence", 0) or 0)
            weighted_score = float(decision.get("weighted_score", 0) or 0)
            threshold = float(min_confidence_score)
            coord["decisions"] += 1
            self._bump_histogram(
                coord["abs_weighted_score_histogram"], abs(weighted_score)
            )
            self._bump_histogram(coord["confidence_histogram"], confidence)
            if confidence >= threshold:
                coord["confidence_gte_threshold"] += 1
            if weighted_score > 15:
                coord["ws_gt_15"] += 1
            if weighted_score < -15:
                coord["ws_lt_neg15"] += 1
            if bool(decision.get("majority", False)):
                coord["majority"] += 1
            if bool(decision.get("risk_veto", False)):
                coord["risk_veto"] += 1
            final_signal = str(decision.get("signal", "HOLD")).upper()
            if final_signal not in SIGNALS:
                final_signal = "HOLD"
            coord["final_signal"][final_signal] += 1
            coord["min_confidence_score"] = threshold
            threshold_key = str(int(threshold)) if threshold.is_integer() else str(threshold)
            counts = coord["min_confidence_score_counts"]
            counts[threshold_key] = _count(counts.get(threshold_key, 0)) + 1
            self._persist()
            return True
        except Exception as exc:
            logger.debug(f"  Agent stats karar kayit hatasi: {exc}")
            return False

    def snapshot(self, day: object) -> dict:
        try:
            day_text = day.date().isoformat() if isinstance(day, datetime) else (
                day.isoformat() if isinstance(day, date) else str(day)
            )
            return copy.deepcopy(self._normalize_day(self.days.get(day_text, {})))
        except Exception as exc:
            logger.debug(f"  Agent stats snapshot hatasi: {exc}")
            return {}
