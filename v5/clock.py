"""Exchange sessions and decision-window calculations."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Iterable
from zoneinfo import ZoneInfo


ET = ZoneInfo("America/New_York")


def _parse_timestamp(value: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError("timestamp must be a string")
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    match = re.fullmatch(r"(.+?\.)(\d+)([+-]\d\d:\d\d)", normalized)
    if match:
        fraction = (match.group(2) + "000000")[:6]
        normalized = match.group(1) + fraction + match.group(3)
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"invalid timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a UTC offset")
    return parsed


@dataclass(frozen=True)
class Session:
    date: date
    open: datetime
    close: datetime

    def __post_init__(self) -> None:
        if self.open.tzinfo is None or self.close.tzinfo is None:
            raise ValueError("session timestamps must be timezone-aware")
        if self.open.utcoffset() != timedelta(0) or self.close.utcoffset() != timedelta(0):
            raise ValueError("session timestamps must be in UTC")
        if self.open >= self.close:
            raise ValueError("session open must be before close")


def parse_calendar(rows: Iterable[dict]) -> list[Session]:
    sessions: dict[date, Session] = {}
    for row in rows:
        try:
            session_date = date.fromisoformat(row["date"])
            open_time = time.fromisoformat(row["open"])
            close_time = time.fromisoformat(row["close"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"malformed calendar row: {row!r}") from exc
        if open_time.tzinfo is not None or close_time.tzinfo is not None:
            raise ValueError(f"calendar times must be ET wall times: {row!r}")
        opened = datetime.combine(session_date, open_time, ET).astimezone(timezone.utc)
        closed = datetime.combine(session_date, close_time, ET).astimezone(timezone.utc)
        session = Session(session_date, opened, closed)
        sessions[session_date] = session
    return [sessions[key] for key in sorted(sessions)]


class SessionCalendar:
    def __init__(self, sessions: Iterable[Session]):
        self._sessions = {session.date: session for session in sessions}
        self._dates = sorted(self._sessions)

    def get(self, session_date: date) -> Session | None:
        return self._sessions.get(session_date)

    def previous(self, session_date: date, n: int) -> list[date]:
        if n < 0:
            raise ValueError("n must be non-negative")
        candidates = [item for item in self._dates if item < session_date]
        return candidates[-n:] if n else []

    def to_json(self) -> list[dict[str, str]]:
        return [
            {
                "date": session.date.isoformat(),
                "open": session.open.isoformat(),
                "close": session.close.isoformat(),
            }
            for session in (self._sessions[item] for item in self._dates)
        ]

    @classmethod
    def from_json(cls, obj) -> "SessionCalendar":
        if not isinstance(obj, list):
            raise ValueError("calendar cache must be a list")
        sessions = []
        for row in obj:
            try:
                session_date = date.fromisoformat(row["date"])
                opened = _parse_timestamp(row["open"]).astimezone(timezone.utc)
                closed = _parse_timestamp(row["close"]).astimezone(timezone.utc)
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"malformed cached calendar row: {row!r}") from exc
            sessions.append(Session(session_date, opened, closed))
        return cls(sessions)

    def covers(self, session_date: date) -> bool:
        return bool(self._dates) and self._dates[0] <= session_date <= self._dates[-1]


@dataclass(frozen=True)
class Window:
    decide_from: datetime
    decide_until: datetime
    submit_until: datetime
    cancel_at: datetime
    reconcile_from: datetime
    reconcile_until: datetime


def window_for(session: Session) -> Window:
    close = session.close
    return Window(
        decide_from=close - timedelta(minutes=10),
        decide_until=close - timedelta(minutes=4),
        submit_until=close - timedelta(minutes=3),
        cancel_at=close - timedelta(seconds=90),
        reconcile_from=close + timedelta(seconds=60),
        reconcile_until=close + timedelta(minutes=5),
    )


def clock_skew_seconds(broker_timestamp: str, local_now: datetime) -> float:
    if local_now.tzinfo is None:
        raise ValueError("local_now must be timezone-aware")
    broker_now = _parse_timestamp(broker_timestamp)
    return (broker_now - local_now).total_seconds()
