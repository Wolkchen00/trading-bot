"""Decision-time market data assembly for the version 5 engine."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
import math

from .broker import BrokerError, BrokerUnavailable
from .clock import ET


RANGE_EMPTY = "RANGE_EMPTY"
RANGE_LATE_START = "RANGE_LATE_START"
RANGE_STALE = "RANGE_STALE"
RANGE_THIN = "RANGE_THIN"


@dataclass(frozen=True)
class DecisionInputs:
    closes: tuple[float, ...]
    close_dates: tuple[date, ...]
    px: float
    px_time: datetime
    px_source: str
    session_high: float | None
    session_low: float | None
    range_known: bool
    range_reason: str | None
    range_bars: int
    range_coverage: float

    def to_bundle(self) -> dict:
        return {
            "closes": list(self.closes),
            "close_dates": [value.isoformat() for value in self.close_dates],
            "px": self.px,
            "px_time": self.px_time.isoformat(),
            "px_source": self.px_source,
            "session_high": self.session_high,
            "session_low": self.session_low,
            "range_known": self.range_known,
            "range_reason": self.range_reason,
            "range_bars": self.range_bars,
            "range_coverage": self.range_coverage,
        }

    @classmethod
    def from_bundle(cls, bundle: dict) -> "DecisionInputs":
        if not isinstance(bundle, dict):
            raise ValueError("input bundle must be an object")
        try:
            result = cls(
                closes=tuple(float(value) for value in bundle["closes"]),
                close_dates=tuple(date.fromisoformat(value) for value in bundle["close_dates"]),
                px=float(bundle["px"]),
                px_time=datetime.fromisoformat(bundle["px_time"]),
                px_source=bundle["px_source"],
                session_high=(None if bundle["session_high"] is None else float(bundle["session_high"])),
                session_low=(None if bundle["session_low"] is None else float(bundle["session_low"])),
                range_known=bundle["range_known"],
                range_reason=bundle["range_reason"],
                range_bars=bundle["range_bars"],
                range_coverage=float(bundle["range_coverage"]),
            )
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise ValueError("invalid input bundle") from exc
        if result.px_time.tzinfo is None:
            raise ValueError("input price time must be timezone-aware")
        return result


@dataclass(frozen=True)
class InputsNotReady:
    code: str
    detail: str = ""


def _positive(value) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return False
    return not isinstance(value, bool) and math.isfinite(number) and number > 0.0


def build_inputs(broker, calendar, session, now, params) -> DecisionInputs | InputsNotReady:
    """Build a complete replayable bundle, returning reason codes for all data failures."""
    try:
        dates = calendar.previous(session.date, 260)
        if len(dates) < 249:
            return InputsNotReady("CALENDAR_MISSING")
        date_set = set(dates)
        daily = broker.get_daily_bars(
            params.signal_symbol, start=dates[0], adjustment="all", feed="sip"
        )
        kept_daily = []
        last_date = None
        for bar in daily:
            try:
                bar_date = bar.t.astimezone(ET).date()
            except (AttributeError, TypeError, ValueError, OverflowError):
                continue
            if bar_date not in date_set:
                continue
            if last_date is not None and bar_date <= last_date:
                return InputsNotReady("HISTORY_GAP")
            last_date = bar_date
            if not _positive(getattr(bar, "c", None)):
                return InputsNotReady("HISTORY_GAP")
            kept_daily.append((bar_date, float(bar.c)))
        present = {bar_date for bar_date, _ in kept_daily}
        if dates[-1] not in present:
            return InputsNotReady("HISTORY_STALE")
        if any(bar_date not in present for bar_date in dates[-249:]):
            return InputsNotReady("HISTORY_GAP")

        minute = broker.get_minute_bars(params.signal_symbol, start=session.open, feed="iex")
        by_time = {}
        for bar in minute:
            try:
                valid = (
                    bar.t.tzinfo is not None
                    and session.open <= bar.t < now
                    and _positive(bar.h)
                    and _positive(bar.l)
                    and float(bar.h) >= float(bar.l)
                )
            except (AttributeError, TypeError, ValueError, OverflowError):
                valid = False
            if valid and bar.t not in by_time:
                by_time[bar.t] = bar
        minutes = [by_time[key] for key in sorted(by_time)]
        elapsed = max(0, int((now - session.open).total_seconds() // 60))
        coverage = len(minutes) / elapsed if elapsed else 0.0
        if not minutes:
            reason = RANGE_EMPTY
        elif minutes[0].t > session.open + timedelta(minutes=10):
            reason = RANGE_LATE_START
        elif minutes[-1].t < now - timedelta(minutes=5):
            reason = RANGE_STALE
        elif len(minutes) < 0.70 * elapsed:
            reason = RANGE_THIN
        else:
            reason = None
        known = reason is None

        trade = broker.get_latest_trade(params.signal_symbol, feed="iex")
        px = px_time = px_source = None
        if trade is not None:
            try:
                if (
                    trade.t.tzinfo is not None
                    and now - timedelta(seconds=120) <= trade.t <= now + timedelta(seconds=5)
                    and _positive(trade.price)
                ):
                    px, px_time, px_source = float(trade.price), trade.t, "iex_trade"
            except (AttributeError, TypeError, ValueError, OverflowError):
                pass
        if px is None and minutes:
            last = minutes[-1]
            if last.t >= now - timedelta(minutes=5) and _positive(getattr(last, "c", None)):
                px, px_time, px_source = float(last.c), last.t, "iex_bar"
        if px is None:
            return InputsNotReady("PRICE_STALE")

        return DecisionInputs(
            closes=tuple(value for _, value in kept_daily),
            close_dates=tuple(value for value, _ in kept_daily),
            px=px,
            px_time=px_time,
            px_source=px_source,
            session_high=max(float(bar.h) for bar in minutes) if known else None,
            session_low=min(float(bar.l) for bar in minutes) if known else None,
            range_known=known,
            range_reason=reason,
            range_bars=len(minutes),
            range_coverage=float(coverage),
        )
    except BrokerUnavailable as exc:
        return InputsNotReady("BROKER_DOWN", type(exc).__name__)
    except BrokerError as exc:
        return InputsNotReady("DATA_ERROR", type(exc).__name__)
    except (AttributeError, TypeError, ValueError, OverflowError, ArithmeticError) as exc:
        return InputsNotReady("DATA_ERROR", type(exc).__name__)
