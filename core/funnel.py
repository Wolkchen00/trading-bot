"""Restart-safe daily entry funnel observability.

This module records telemetry only. Every write and notification helper is
best-effort so a funnel failure can never affect the trading loop.
"""
from __future__ import annotations

import copy
import json
import os
import time
from datetime import date, datetime, timedelta
from typing import Callable, Optional
from zoneinfo import ZoneInfo

from config import state_path
from utils.logger import logger


class DailyFunnel:
    """ET-day counters and no-trade alarm state."""

    STAGES = (
        "scanned",
        "signal_buy",
        "signal_sell",
        "signal_hold",
        "conf_below_min",
        "sector_block",
        "gate_block",
        "queued_pullback",
        "queue_dup",
        "entries",
        "exits",
    )
    WRITE_INTERVAL_SECONDS = 60
    RETENTION_DAYS = 30

    def __init__(
        self,
        enabled: bool = True,
        path: Optional[str] = None,
        today_fn: Optional[Callable[[], object]] = None,
    ):
        self.enabled = bool(enabled)
        self.path = path or state_path("funnel.json")
        self._today_fn = today_fn
        self.days = {}
        self.last_entry_date = None
        self.last_no_trade_alarm_date = None
        self._last_persist_monotonic = 0.0
        if self.enabled:
            self._load()

    @staticmethod
    def _empty_day() -> dict:
        result = {stage: 0 for stage in DailyFunnel.STAGES}
        result["gate_block_reasons"] = {}
        return result

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
    def _date_str(value: object) -> str:
        if isinstance(value, datetime):
            return value.date().isoformat()
        if isinstance(value, date):
            return value.isoformat()
        return str(value)

    @classmethod
    def _normalize_day(cls, raw: object) -> dict:
        day = cls._empty_day()
        if not isinstance(raw, dict):
            return day
        for stage in cls.STAGES:
            try:
                day[stage] = max(0, int(raw.get(stage, 0) or 0))
            except (TypeError, ValueError):
                day[stage] = 0
        reasons = raw.get("gate_block_reasons", {})
        if isinstance(reasons, dict):
            for reason, count in reasons.items():
                try:
                    day["gate_block_reasons"][str(reason)] = max(
                        0, int(count or 0)
                    )
                except (TypeError, ValueError):
                    continue
        return day

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
            last_entry = raw.get("last_entry_date")
            last_alarm = raw.get("last_no_trade_alarm_date")
            self.last_entry_date = str(last_entry) if last_entry else None
            self.last_no_trade_alarm_date = (
                str(last_alarm) if last_alarm else None
            )
        except Exception as exc:
            logger.debug(f"  Funnel state yukleme hatasi: {exc}")

    def _prune(self) -> None:
        cutoff = date.fromisoformat(self._today()) - timedelta(
            days=self.RETENTION_DAYS
        )
        retained = {}
        for day_str, values in self.days.items():
            try:
                if date.fromisoformat(day_str) >= cutoff:
                    retained[day_str] = values
            except (TypeError, ValueError):
                continue
        self.days = retained

    def _persist(self, force: bool = False) -> bool:
        if not self.enabled:
            return False
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
            payload = {
                "days": self.days,
                "last_entry_date": self.last_entry_date,
                "last_no_trade_alarm_date": self.last_no_trade_alarm_date,
            }
            temp_path = f"{self.path}.tmp"
            with open(temp_path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, ensure_ascii=True)
            os.replace(temp_path, self.path)
            self._last_persist_monotonic = now
            return True
        except Exception as exc:
            logger.debug(f"  Funnel state kayit hatasi: {exc}")
            return False

    def save(self) -> bool:
        """Persist immediately, including retention pruning."""
        try:
            return self._persist(force=True)
        except Exception as exc:
            logger.debug(f"  Funnel save hatasi: {exc}")
            return False

    def bump(self, stage: str, reason: Optional[str] = None) -> None:
        """Increment today's counter. Telemetry errors never propagate."""
        try:
            if not self.enabled:
                return
            if stage not in self.STAGES:
                logger.debug(f"  Bilinmeyen funnel asamasi atlandi: {stage}")
                return
            today = self._today()
            day = self.days.setdefault(today, self._empty_day())
            day[stage] = int(day.get(stage, 0) or 0) + 1
            if stage == "gate_block" and reason is not None:
                reason_text = str(reason).strip() or "BILINMIYOR"
                reasons = day.setdefault("gate_block_reasons", {})
                reasons[reason_text] = int(reasons.get(reason_text, 0) or 0) + 1
            if stage == "entries":
                self.last_entry_date = today
            self._persist(force=stage in ("entries", "exits"))
        except Exception as exc:
            logger.debug(f"  Funnel bump hatasi ({stage}): {exc}")

    def snapshot(self, date_str: str) -> dict:
        try:
            if not self.enabled:
                return {}
            normalized = self._normalize_day(
                self.days.get(self._date_str(date_str), {})
            )
            return copy.deepcopy(normalized)
        except Exception as exc:
            logger.debug(f"  Funnel snapshot hatasi: {exc}")
            return {}

    def report_lines(self, date_str: str) -> list[str]:
        """Return Turkish ASCII log lines and persist the report state."""
        try:
            if not self.enabled:
                return []
            day_str = self._date_str(date_str)
            data = self.snapshot(day_str)
            reasons = data.get("gate_block_reasons", {})
            reason_text = ", ".join(
                f"{reason}={count}"
                for reason, count in sorted(
                    reasons.items(), key=lambda item: (-item[1], item[0])
                )
            ) or "yok"
            lines = [
                f"GIRIS HUNISI {day_str}",
                (
                    f"  Taranan: {data.get('scanned', 0)} | "
                    f"BUY: {data.get('signal_buy', 0)} | "
                    f"SELL: {data.get('signal_sell', 0)} | "
                    f"HOLD: {data.get('signal_hold', 0)}"
                ),
                (
                    f"  Dusuk guven: {data.get('conf_below_min', 0)} | "
                    f"Sektor blok: {data.get('sector_block', 0)} | "
                    f"Gate blok: {data.get('gate_block', 0)}"
                ),
                (
                    f"  Pullback kuyruk: {data.get('queued_pullback', 0)} | "
                    f"Kuyruk tekrari: {data.get('queue_dup', 0)} | "
                    f"Giris: {data.get('entries', 0)} | "
                    f"Cikis: {data.get('exits', 0)}"
                ),
                f"  Gate nedenleri: {reason_text}",
            ]
            self._persist(force=True)
            return lines
        except Exception as exc:
            logger.debug(f"  Funnel rapor hatasi: {exc}")
            return []

    @staticmethod
    def business_days_between(d1: object, d2: object) -> int:
        """Count weekdays strictly after d1 through d2, inclusive.

        Market holidays intentionally count as business days. This is a
        documented approximation and avoids a calendar dependency.
        """
        start = d1.date() if isinstance(d1, datetime) else d1
        end = d2.date() if isinstance(d2, datetime) else d2
        if not isinstance(start, date):
            start = date.fromisoformat(str(start))
        if not isinstance(end, date):
            end = date.fromisoformat(str(end))
        if end <= start:
            return 0
        count = 0
        cursor = start + timedelta(days=1)
        while cursor <= end:
            if cursor.weekday() < 5:
                count += 1
            cursor += timedelta(days=1)
        return count

    def _migrate_last_entry(self, closed_day: str, history_path: str) -> bool:
        try:
            trades = []
            if os.path.exists(history_path):
                with open(history_path, "r", encoding="utf-8") as handle:
                    raw = json.load(handle)
                if isinstance(raw, list):
                    trades = raw
                elif isinstance(raw, dict):
                    trades = raw.get("trades", [])
            for trade in reversed(trades):
                if not isinstance(trade, dict):
                    continue
                action = str(trade.get("action", "")).upper()
                if action not in ("BUY", "SHORT"):
                    continue
                trade_date = trade.get("date")
                if not trade_date:
                    trade_date = str(trade.get("timestamp", ""))[:10]
                try:
                    date.fromisoformat(str(trade_date))
                except (TypeError, ValueError):
                    continue
                self.last_entry_date = str(trade_date)
                self._persist(force=True)
                return True
        except Exception as exc:
            logger.debug(f"  Funnel BUY tarihi migration hatasi: {exc}")
        self.last_entry_date = closed_day
        self._persist(force=True)
        return False

    def dominant_stage(self, date_str: str) -> tuple[str, int]:
        """Return the most useful downstream explanation for a no-entry day."""
        data = self.snapshot(date_str)
        priority = (
            "gate_block",
            "conf_below_min",
            "sector_block",
            "queue_dup",
            "queued_pullback",
            "signal_hold",
            "signal_sell",
            "signal_buy",
        )
        dominant = max(
            ((stage, int(data.get(stage, 0) or 0)) for stage in priority),
            key=lambda item: item[1],
            default=("veri_yok", 0),
        )
        return dominant if dominant[1] > 0 else ("veri_yok", 0)

    def top_gate_reason(self, date_str: str) -> tuple[str, int]:
        reasons = self.snapshot(date_str).get("gate_block_reasons", {})
        if not reasons:
            return "yok", 0
        return sorted(reasons.items(), key=lambda item: (-item[1], item[0]))[0]

    def maybe_notify_no_trade(
        self,
        closed_day: object,
        today: object,
        threshold: int,
        is_paper: bool,
        notifier: object,
        history_path: Optional[str] = None,
    ) -> bool:
        """Migrate entry state and emit at most one NO_TRADE alarm per ET day."""
        try:
            if not self.enabled:
                return False
            closed_str = self._date_str(closed_day)
            today_str = self._date_str(today)
            if not self.last_entry_date:
                migrated = self._migrate_last_entry(
                    closed_str, history_path or state_path("trade_history.json")
                )
                if not migrated:
                    return False

            days = self.business_days_between(
                self.last_entry_date, closed_str
            )
            if days < int(threshold):
                self._persist(force=True)
                return False
            if self.last_no_trade_alarm_date == today_str:
                self._persist(force=True)
                return False

            stage, stage_count = self.dominant_stage(closed_str)
            gate_reason, gate_count = self.top_gate_reason(closed_str)
            mode = "PAPER" if is_paper else "CANLI"
            message = (
                f"Hesap modu: {mode} | Islemsiz is gunu: {days} | "
                f"Son giris tarihi: {self.last_entry_date} | "
                f"Baskin funnel asamasi: {stage} ({stage_count}) | "
                f"En sik gate nedeni: {gate_reason} ({gate_count})"
            )

            # Persist the dedupe marker before delivery for at-most-once behavior
            # across process crashes and notifier exceptions.
            self.last_no_trade_alarm_date = today_str
            self._persist(force=True)
            try:
                notifier.notify_critical("NO_TRADE", message)
            except Exception as exc:
                logger.debug(f"  NO_TRADE alarm teslim hatasi: {exc}")
            return True
        except Exception as exc:
            logger.debug(f"  NO_TRADE alarm kontrol hatasi: {exc}")
            return False
