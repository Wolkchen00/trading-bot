"""Durable critical-alarm publisher with optional direct ntfy delivery."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import json
import os
import re
from typing import Callable, Optional
from urllib.parse import quote
from uuid import uuid4

import requests

from utils.logger import logger


NTFY_BASE_URL = "https://ntfy.sh"
DEFAULT_COOLDOWN = timedelta(hours=4)


@dataclass(frozen=True)
class PublishResult:
    """Keep durable persistence distinct from direct phone delivery."""

    alarm_id: str
    persisted: bool
    direct_delivered: bool
    telegram_delivered: bool = False
    ntfy_delivered: bool = False
    delivery_marker_written: bool = False
    cooldown_suppressed: bool = False


class CriticalAlarmPublisher:
    """Persist every alarm before attempting Telegram and env-configured ntfy."""

    def __init__(
        self,
        telegram_send: Optional[Callable[[str], bool]] = None,
        now_fn: Callable[[], datetime] = datetime.now,
    ):
        self._telegram_send = telegram_send
        self._now_fn = now_fn
        self._topic = os.getenv("NTFY_TOPIC", "").strip()
        self._delivered_at: dict[tuple[str, str, str], datetime] = {}
        self._bridge_warning_date = None

    @staticmethod
    def _identity(
        kind: str,
        message: str,
        symbol: Optional[str],
        state_code: Optional[str],
    ) -> tuple[str, str, str]:
        """Build the required kind+symbol+state-code cooldown identity.

        Structured values are preferred.  The small parser keeps the historic
        two-argument notify_critical API useful for existing callers.
        """
        normalized_kind = str(kind).strip().upper() or "CRITICAL"
        parsed_symbol = (symbol or "").strip().upper()
        parsed_state = (state_code or "").strip().lower()

        bracket = re.search(r"\[([^\]]+)\]", message)
        key_parts = bracket.group(1).split(":") if bracket else []
        if not parsed_symbol and key_parts:
            candidate = key_parts[0].strip().upper()
            if re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", candidate):
                parsed_symbol = candidate
        if not parsed_state and key_parts:
            tail = key_parts[-1].strip().lower()
            if len(key_parts) == 2 and tail in {"long", "short"}:
                parsed_state = "naked"
            elif tail:
                parsed_state = tail

        if not parsed_symbol:
            for candidate in re.findall(r"\b[A-Z][A-Z0-9.\-]{0,9}\b", message):
                if candidate not in {
                    "ALARM", "BOT", "CANLI", "CRITICAL", "HATA", "KILL",
                    "KORUMA", "LONG", "NO", "PAPER", "SHORT", "TRADE",
                }:
                    parsed_symbol = candidate
                    break

        if not parsed_state:
            folded = message.casefold()
            state_words = (
                (("naked", "ciplak", "çıplak", "korumasiz", "korumasız"), "naked"),
                (("drift", "sapma"), "drift"),
                (("close_failed", "kapanış başarısız", "kapanis basarisiz"), "close_failed"),
                (("partial",), "partial"),
                (("entry", "giris", "giriş"), "entry"),
            )
            for needles, value in state_words:
                if any(needle in folded for needle in needles):
                    parsed_state = value
                    break

        return normalized_kind, parsed_symbol or "*", parsed_state or "default"

    def _in_cooldown(self, key: tuple[str, str, str], now: datetime) -> bool:
        delivered_at = self._delivered_at.get(key)
        if delivered_at is None:
            return False
        if key[0] == "NO_TRADE":
            return delivered_at.date() == now.date()
        return now - delivered_at < DEFAULT_COOLDOWN

    @staticmethod
    def _append_record(record: dict) -> bool:
        try:
            # Runtime import preserves mode selection and test monkeypatches.
            from config import state_path

            with open(state_path("alarms.jsonl"), "a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            return True
        except Exception:
            return False

    def _post_ntfy(self, kind: str, message: str) -> bool:
        if not self._topic:
            return False
        url = f"{NTFY_BASE_URL}/{quote(self._topic, safe='')}"
        headers = {
            "Title": f"Trading alarm: {kind}",
            "Priority": "urgent",
            "Tags": "rotating_light",
        }
        for attempt in range(2):
            try:
                response = requests.post(
                    url,
                    data=message.encode("utf-8"),
                    headers=headers,
                    timeout=5,
                )
                if 200 <= response.status_code < 300:
                    return True
                logger.debug(
                    f"  ntfy alarm gonderimi HTTP {response.status_code} "
                    f"(deneme {attempt + 1}/2)"
                )
            except Exception as exc:
                logger.debug(
                    f"  ntfy alarm gonderim hatasi (deneme {attempt + 1}/2): {exc}"
                )
        return False

    def _warn_bridge_once_per_day(self, kind: str, now: datetime) -> None:
        today = now.date()
        if self._bridge_warning_date == today:
            return
        self._bridge_warning_date = today
        logger.warning(
            f"  KRITIK ALARM DOGRUDAN TESLIM EDILEMEDI ({kind}); "
            "alarms.jsonl kalici kaydi mevcut, VPS bridge backstop devrede"
        )

    def publish(
        self,
        kind: str,
        message: str,
        *,
        telegram_text: Optional[str] = None,
        symbol: Optional[str] = None,
        state_code: Optional[str] = None,
    ) -> PublishResult:
        """Persist first, then deliver directly with success-only cooldown.

        DELIVERY is intentionally appended only after ntfy accepts the POST.
        The contract is at-least-once with best-effort deduplication: a crash
        after POST but before this marker can produce a duplicate bridge push,
        but it cannot lose the durable alarm.
        """
        now = self._now_fn()
        alarm_id = uuid4().hex
        key = self._identity(kind, message, symbol, state_code)
        record = {
            "ts": now.isoformat(),
            "kind": kind,
            "message": message[:2000],
            "id": alarm_id,
            "symbol": key[1],
            "state": key[2],
        }
        persisted = self._append_record(record)

        if self._in_cooldown(key, now):
            if persisted:
                self._warn_bridge_once_per_day(kind, now)
            else:
                logger.error(
                    f"  KRITIK ALARM TESLIM EDILEMEDI ({kind}): "
                    "alarms.jsonl yazimi basarisiz ve dogrudan teslim cooldown ile bastirildi"
                )
            return PublishResult(
                alarm_id=alarm_id,
                persisted=persisted,
                direct_delivered=False,
                cooldown_suppressed=True,
            )

        telegram_delivered = False
        if self._telegram_send is not None:
            try:
                telegram_delivered = self._telegram_send(
                    telegram_text if telegram_text is not None else message
                ) is True
            except Exception as exc:
                logger.debug(f"  Telegram kritik alarm gonderim hatasi: {exc}")

        ntfy_delivered = self._post_ntfy(str(kind), message[:2000])
        marker_written = False
        if ntfy_delivered:
            marker_written = self._append_record({"kind": "DELIVERY", "ref": alarm_id})
            if not marker_written:
                logger.warning(
                    f"  DELIVERY isareti yazilamadi ({kind}, ref={alarm_id}); "
                    "VPS bridge nadiren cift teslim yapabilir"
                )

        direct_delivered = telegram_delivered or ntfy_delivered
        if direct_delivered:
            self._delivered_at[key] = now

        if not persisted and not direct_delivered:
            logger.error(
                f"  KRITIK ALARM TESLIM EDILEMEDI ({kind}): "
                "alarms.jsonl yazimi ve tum dogrudan kanallar basarisiz"
            )
        elif persisted and not direct_delivered:
            self._warn_bridge_once_per_day(str(kind), now)
        elif not persisted:
            logger.error(
                f"  ALARM KUYRUGA YAZILAMADI ({kind}); dogrudan teslim basarili"
            )

        return PublishResult(
            alarm_id=alarm_id,
            persisted=persisted,
            direct_delivered=direct_delivered,
            telegram_delivered=telegram_delivered,
            ntfy_delivered=ntfy_delivered,
            delivery_marker_written=marker_written,
        )
