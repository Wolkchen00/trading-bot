"""Restart-safe daily entry funnel observability.

This module records telemetry only. Every write and notification helper is
best-effort so a funnel failure can never affect the trading loop.
"""
from __future__ import annotations

import copy
import json
import os
import time
from datetime import date, datetime, timedelta, timezone
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
        "conf_below_min_buy",
        "conf_below_min_short",
        "sector_block",
        "gate_block",
        "wash_sale_block",
        "index_signal",
        "queued_pullback",
        "queue_dup",
        "reached_executor",
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
        result["stage_symbols"] = {
            stage: [] for stage in DailyFunnel.STAGES
        }
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
        raw_symbols = raw.get("stage_symbols")
        if not isinstance(raw_symbols, dict):
            # R11 oncesi gunlerde sembol kumesi yoktu. Sifir demek yerine
            # bilinmiyor olarak koru; aksi halde eski veri sahte kesinlik uretir.
            day["stage_symbols"] = None
        else:
            normalized_symbols = {}
            for stage in cls.STAGES:
                values = raw_symbols.get(stage)
                if not isinstance(values, (list, tuple, set)):
                    normalized_symbols[stage] = None
                    continue
                symbols = {
                    str(symbol).strip().upper()
                    for symbol in values
                    if str(symbol).strip()
                }
                normalized_symbols[stage] = sorted(symbols)
            day["stage_symbols"] = normalized_symbols
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

    def bump(
        self,
        stage: str,
        reason: Optional[str] = None,
        symbol: Optional[str] = None,
    ) -> None:
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
            symbol_text = str(symbol or "").strip().upper()
            symbols_by_stage = day.get("stage_symbols")
            if symbol_text and isinstance(symbols_by_stage, dict):
                stage_symbols = symbols_by_stage.get(stage)
                if isinstance(stage_symbols, list) and symbol_text not in stage_symbols:
                    stage_symbols.append(symbol_text)
                    stage_symbols.sort()
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

    @staticmethod
    def _unique_symbol_count(data: dict, stage: str) -> Optional[int]:
        symbols_by_stage = data.get("stage_symbols")
        if not isinstance(symbols_by_stage, dict):
            return None
        symbols = symbols_by_stage.get(stage)
        if not isinstance(symbols, list):
            return None
        return len(symbols)

    @classmethod
    def _metric_text(cls, data: dict, stage: str) -> str:
        events = int(data.get(stage, 0) or 0)
        unique = cls._unique_symbol_count(data, stage)
        unique_text = "UNKNOWN" if unique is None else str(unique)
        return f"olay={events} (benzersiz sembol={unique_text})"

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
                    f"  Taranan: {self._metric_text(data, 'scanned')} | "
                    f"BUY: {self._metric_text(data, 'signal_buy')} | "
                    f"SELL: {self._metric_text(data, 'signal_sell')} | "
                    f"HOLD: {self._metric_text(data, 'signal_hold')}"
                ),
                (
                    f"  Dusuk guven toplam: "
                    f"{self._metric_text(data, 'conf_below_min')} | "
                    f"BUY dusuk guven: "
                    f"{self._metric_text(data, 'conf_below_min_buy')} | "
                    f"SHORT dusuk guven: "
                    f"{self._metric_text(data, 'conf_below_min_short')}"
                ),
                (
                    f"  Sektor blok: {self._metric_text(data, 'sector_block')} | "
                    f"Gate blok: {self._metric_text(data, 'gate_block')} | "
                    f"Wash-sale blok: "
                    f"{self._metric_text(data, 'wash_sale_block')}"
                ),
                (
                    f"  Index sinyali: {self._metric_text(data, 'index_signal')} | "
                    f"Pullback kuyruk: "
                    f"{self._metric_text(data, 'queued_pullback')} | "
                    f"Kuyruk tekrari: {self._metric_text(data, 'queue_dup')}"
                ),
                (
                    f"  Executor'a ulasan: "
                    f"{self._metric_text(data, 'reached_executor')} | "
                    f"Giris: {self._metric_text(data, 'entries')} | "
                    f"Cikis: {self._metric_text(data, 'exits')}"
                ),
                f"  Gate nedenleri: {reason_text}",
            ]
            bottleneck = self.downstream_bottleneck(day_str)
            numeric = self.numeric_dominant(day_str)
            lines.append(
                f"  Downstream bloker: {bottleneck[0]} ({bottleneck[1]}) | "
                f"Sayisal baskin: {numeric[0]} ({numeric[1]})"
            )
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

    @staticmethod
    def _ledger_entry_date() -> Optional[str]:
        """R9 defterindeki son strategy BUY dolumunun ET gununu dondur."""
        try:
            from core.fill_ledger import read_fills

            latest = None
            for fill in read_fills():
                if not isinstance(fill, dict):
                    continue
                if fill.get("provenance") != "strategy":
                    continue
                if str(fill.get("side", "")).upper() != "BUY":
                    continue
                raw_ts = str(fill.get("ts_utc", "")).strip()
                if not raw_ts:
                    continue
                if raw_ts.endswith("Z"):
                    raw_ts = raw_ts[:-1] + "+00:00"
                parsed = datetime.fromisoformat(raw_ts)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                if latest is None or parsed > latest:
                    latest = parsed
            if latest is not None:
                return latest.astimezone(
                    ZoneInfo("America/New_York")
                ).date().isoformat()
        except Exception as exc:
            logger.debug(f"  Funnel fill ledger BUY tarihi okuma hatasi: {exc}")
        return None

    def _migrate_last_entry(self, closed_day: str, history_path: str) -> bool:
        # Parametreler imzada geriye uyumluluk icin kalir; R11'de tek fallback
        # kaynagi R9 fill ledger'daki strategy BUY dolumudur.
        del closed_day, history_path
        migrated = self._ledger_entry_date()
        self.last_entry_date = migrated
        self._persist(force=True)
        return migrated is not None

    def _has_trustworthy_entry_date(self) -> bool:
        try:
            if not self.last_entry_date:
                return False
            entry_str = str(self.last_entry_date)
            date.fromisoformat(entry_str)
            # Eski migration funnel'in olusturuldugu gunu entries=0 iken
            # last_entry_date yapardi. Bu imza gorulurse defterden yeniden kur.
            if entry_str in self.days:
                entry_day = self._normalize_day(self.days.get(entry_str))
                if int(entry_day.get("entries", 0) or 0) <= 0:
                    return False
            return True
        except (TypeError, ValueError):
            return False

    def _observed_no_entry_days(self, closed_day: str) -> int:
        """Son giris bilinmiyorsa yalniz kanitli funnel gunlerini say."""
        try:
            cursor = date.fromisoformat(closed_day)
            count = 0
            while True:
                if cursor.weekday() >= 5:
                    cursor -= timedelta(days=1)
                    continue
                day_str = cursor.isoformat()
                if day_str not in self.days:
                    break
                day = self._normalize_day(self.days.get(day_str))
                if int(day.get("entries", 0) or 0) > 0:
                    break
                count += 1
                cursor -= timedelta(days=1)
            return count
        except Exception as exc:
            logger.debug(f"  Funnel bilinmeyen giris gun sayimi hatasi: {exc}")
            return 0

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

    def numeric_dominant(self, date_str: str) -> tuple[str, int]:
        """Hacim gercegi: mevcut dominant_stage ile birebir ayni sonuc."""
        return self.dominant_stage(date_str)

    def downstream_bottleneck(self, date_str: str) -> tuple[str, int]:
        """Aksiyon alinabilir sinyallerden sonraki en sik engeli dondur."""
        data = self.snapshot(date_str)
        priority = (
            "conf_below_min_buy",
            "conf_below_min_short",
            "conf_below_min",
            "sector_block",
            "gate_block",
            "wash_sale_block",
            "queue_dup",
            "queued_pullback",
        )
        bottleneck = max(
            ((stage, int(data.get(stage, 0) or 0)) for stage in priority),
            key=lambda item: item[1],
            default=("veri_yok", 0),
        )
        return bottleneck if bottleneck[1] > 0 else ("veri_yok", 0)

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
            if not self._has_trustworthy_entry_date():
                self.last_entry_date = None
                migrated = self._migrate_last_entry(
                    closed_str, history_path or state_path("trade_history.json")
                )
            else:
                migrated = True

            if migrated:
                days = self.business_days_between(
                    self.last_entry_date, closed_str
                )
                last_entry_text = str(self.last_entry_date)
            else:
                days = self._observed_no_entry_days(closed_str)
                last_entry_text = "bilinmiyor (funnel oncesi)"
            if days < int(threshold):
                self._persist(force=True)
                return False
            if self.last_no_trade_alarm_date == today_str:
                self._persist(force=True)
                return False

            stage, stage_count = self.downstream_bottleneck(closed_str)
            numeric_stage, numeric_count = self.numeric_dominant(closed_str)
            mode = "PAPER" if is_paper else "CANLI"
            message = (
                f"Hesap modu: {mode} | Islemsiz is gunu: {days} | "
                f"Son giris tarihi: {last_entry_text} | "
                f"Baskin downstream bloker: {stage} ({stage_count}) | "
                f"Sayisal baskin asama: {numeric_stage} ({numeric_count})"
            )
            if stage == "gate_block":
                gate_reason, gate_count = self.top_gate_reason(closed_str)
                message += (
                    f" | Baskin gate nedeni: {gate_reason} ({gate_count})"
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
