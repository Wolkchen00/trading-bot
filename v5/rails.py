"""Persistent safety rails for the version 5 trading engine."""

from __future__ import annotations

import math
import time
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from .broker import BrokerError
from .state import LockBusy, ProcessLock, StateCorrupt, atomic_write_json, read_json
from .strategy import READY, Decision


@dataclass(frozen=True)
class EquityReading:
    time: datetime
    equity: float | None
    last_equity: float | None
    cash: float | None
    positions_value: float | None
    ok: bool
    problem: str | None


class PeakNotInitialised(RuntimeError):
    pass


# The two breaching readings that halt the engine must belong to the same decision window.
CONFIRMATION_MAX_SECONDS = 600.0
# A missing row is different from a corrupt stored value: with no row there is nothing to cross-check.
NO_ROW = object()


def _number(value) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def breaker_cause(reading, stored_close, breaker_day):
    """The breaker's read-only decision, shared by the engine and operator checks."""
    if not reading.ok:
        return None
    baseline = _number(reading.last_equity)
    if baseline is None or baseline <= 0.0:
        return "BASELINE_DOUBT"
    if stored_close is not NO_ROW:
        stored = _number(stored_close)
        if stored is None or abs(stored - baseline) > 0.02 * baseline:
            return "BASELINE_DOUBT"
    if reading.equity <= (1.0 - breaker_day) * baseline:
        return "BREACH"
    return None


def read_equity(broker, now) -> EquityReading:
    """Read and cross-check the account without allowing broker trouble to escape."""
    try:
        account = broker.get_account()
        positions = broker.get_positions()
    except BrokerError:
        return EquityReading(now, None, None, None, None, False, "BROKER_DOWN")

    equity = _number(account.equity)
    last_equity = _number(account.last_equity)
    cash = _number(account.cash)
    values = [_number(position.market_value) for position in positions]
    positions_value = sum(value for value in values if value is not None)
    if account.status != "ACTIVE":
        problem = "ACCOUNT_STATUS"
    elif account.trading_blocked or account.account_blocked:
        problem = "BLOCKED"
    elif equity is None or equity <= 0.0 or cash is None:
        problem = "EQUITY_BAD"
    elif any(value is None for value in values) or abs(equity - (cash + positions_value)) > 0.02 * equity:
        problem = "INCONSISTENT"
    else:
        problem = None
    return EquityReading(
        now,
        equity,
        last_equity,
        cash,
        positions_value,
        problem is None,
        problem,
    )


def _initial_state() -> dict:
    return {
        "peak": None,
        "peak_source": None,
        "peak_time": None,
        "equity_history": [],
        "breaker": None,
        "alert_active": False,
        "floor_state": "OK",
        "floor_confirmation": None,
        "liquidating": False,
        "peak_events": [],
        "paused": False,
        "pause_events": [],
    }


def _iso(value) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _datetime(value) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


class Rails:
    def __init__(self, settings, state_dir, now_fn, sleep_fn=time.sleep):
        self.settings = settings
        self.state_dir = Path(state_dir)
        self.now_fn = now_fn
        self.sleep_fn = sleep_fn
        self.path = self.state_dir / "rails.json"
        self.lock_path = self.state_dir / "rails.lock"

    def _read(self) -> dict:
        document = read_json(self.path, default=_initial_state())
        required = set(_initial_state())
        valid = isinstance(document, dict) and required.issubset(document)
        if valid:
            peak = document["peak"]
            peak_number = _number(peak)
            valid = peak is None or (peak_number is not None and peak_number > 0)
            valid = valid and isinstance(document["equity_history"], list)
            if valid:
                for row in document["equity_history"]:
                    valid = (
                        isinstance(row, dict)
                        and isinstance(row.get("session"), str)
                        and _number(row.get("equity")) is not None
                        and _number(row.get("equity")) > 0
                    )
                    if not valid:
                        break
            valid = valid and document["floor_state"] in {"OK", "CONFIRMING", "HALT"}
            valid = valid and isinstance(document["alert_active"], bool)
            valid = valid and isinstance(document["liquidating"], bool)
            valid = valid and isinstance(document["paused"], bool)
            valid = valid and isinstance(document["peak_events"], list)
            valid = valid and isinstance(document["pause_events"], list)
            valid = valid and (document["breaker"] is None or isinstance(document["breaker"], dict))
            confirmation = document["floor_confirmation"]
            valid = valid and (confirmation is None or isinstance(confirmation, dict))
            if valid and confirmation is not None:
                try:
                    _datetime(confirmation["time"])
                    valid = _number(confirmation["equity"]) is not None
                except (KeyError, TypeError, ValueError, OverflowError):
                    valid = False
            if valid:
                valid = (
                    confirmation is not None
                    if document["floor_state"] == "CONFIRMING"
                    else confirmation is None
                )
        if not valid:
            raise StateCorrupt(self.path)
        return document

    @contextmanager
    def _locked(self):
        lock = ProcessLock(self.lock_path)
        for attempt in range(101):
            if lock.acquire():
                try:
                    yield
                finally:
                    lock.release()
                return
            if attempt == 100:
                break
            self.sleep_fn(0.05)
        raise LockBusy(f"process lock is held elsewhere: {self.lock_path}")

    def _change(self, change):
        with self._locked():
            document = self._read()
            result, changed = change(document)
            if changed:
                atomic_write_json(self.path, document)
            return result

    def peak(self) -> float | None:
        return self._read().get("peak")

    def init_peak(self, value, source, force=False):
        number = _number(value)
        if number is None or number <= 0.0:
            raise ValueError("peak must be a finite number greater than zero")

        def change(document):
            if document.get("peak") is not None and not force:
                raise ValueError("peak is already initialised")
            document["peak"] = number
            document["peak_source"] = str(source)
            document["peak_time"] = _iso(self.now_fn())
            return None, True

        return self._change(change)

    def record_close(self, session_date, equity):
        number = _number(equity)
        if number is None or number <= 0.0:
            raise ValueError("equity must be a finite number greater than zero")
        session = _iso(session_date)

        def change(document):
            history = document.setdefault("equity_history", [])
            if any(row.get("session") == session for row in history):
                return None, False
            history.append({"session": session, "equity": number})
            peak = document.get("peak")
            if peak is not None:
                document["peak"] = max(float(peak), number)
            return None, True

        return self._change(change)

    def check_breaker(self, reading, session_date, previous_session=None) -> bool:
        """`previous_session`: the session right before this one. When it is given, the broker's baseline is
        cross-checked against the stored close of exactly that session; a missing row means no cross-check
        (an older row says nothing about yesterday's close)."""
        session = _iso(session_date)
        previous = None if previous_session is None else _iso(previous_session)
        if not reading.ok:
            breaker = self._read().get("breaker")
            return bool(breaker and breaker.get("session") == session)

        def change(document):
            current = document.get("breaker")
            if current and current.get("session") == session:
                return True, False
            prior = [
                row
                for row in document.get("equity_history", [])
                if (
                    str(row.get("session", "")) == previous
                    if previous is not None
                    else str(row.get("session", "")) < session
                )
            ]
            latest = max(prior, key=lambda row: str(row.get("session", ""))) if prior else None
            stored = latest.get("equity") if latest is not None else NO_ROW
            cause = breaker_cause(reading, stored, self.settings.rails.breaker_day)
            if cause is None:
                return False, False
            baseline = _number(reading.last_equity)
            document["breaker"] = {
                "session": session,
                "cause": cause,
                "equity": reading.equity,
                "baseline": baseline,
                "time": _iso(reading.time),
            }
            return True, True

        return self._change(change)

    def breaker_info(self) -> dict | None:
        return self._read().get("breaker")

    def check_alert(self, reading) -> float | None:
        if not reading.ok:
            return None

        def change(document):
            peak = document.get("peak")
            if peak is None:
                return None, False
            dd = 1.0 - reading.equity / peak
            active = bool(document.get("alert_active", False))
            if not active and dd >= self.settings.rails.alert_dd:
                document["alert_active"] = True
                return dd, True
            if active and dd < self.settings.rails.alert_dd - 0.05:
                document["alert_active"] = False
                return None, True
            return None, False

        return self._change(change)

    def check_floor(self, reading) -> str:
        def change(document):
            peak = document.get("peak")
            if peak is None:
                raise PeakNotInitialised("peak equity has not been initialised")
            state = document.get("floor_state", "OK")
            if state == "HALT" or not reading.ok:
                return state, False
            breach = reading.equity <= (1.0 - self.settings.rails.floor_dd) * peak
            if not breach:
                if state == "CONFIRMING":
                    document["floor_state"] = "OK"
                    document["floor_confirmation"] = None
                    return "OK", True
                return "OK", False
            if state == "OK":
                document["floor_state"] = "CONFIRMING"
                document["floor_confirmation"] = {
                    "time": _iso(reading.time),
                    "equity": reading.equity,
                }
                return "CONFIRMING", True
            confirmation = document.get("floor_confirmation") or {}
            elapsed = (_datetime(reading.time) - _datetime(confirmation["time"])).total_seconds()
            if elapsed > CONFIRMATION_MAX_SECONDS:
                # Too old to be "the reading before this one": this reading starts a new confirmation.
                document["floor_confirmation"] = {"time": _iso(reading.time), "equity": reading.equity}
                return "CONFIRMING", True
            if elapsed >= 60.0:
                document["floor_state"] = "HALT"
                document["floor_confirmation"] = None
                document["liquidating"] = True
                document["halt_equity"] = reading.equity
                document["halt_time"] = _iso(reading.time)
                return "HALT", True
            return "CONFIRMING", False

        return self._change(change)

    def floor_state(self) -> str:
        return self._read().get("floor_state", "OK")

    def liquidating(self) -> bool:
        return bool(self._read().get("liquidating", False))

    def set_liquidating(self, flag):
        flag = bool(flag)

        def change(document):
            if bool(document.get("liquidating", False)) == flag:
                return None, False
            document["liquidating"] = flag
            return None, True

        return self._change(change)

    def clear_halt(self, by, note, new_peak):
        number = _number(new_peak)
        if number is None or number <= 0.0:
            raise ValueError("new peak must be a finite number greater than zero")

        def change(document):
            if document.get("floor_state", "OK") not in {"HALT", "CONFIRMING"}:
                raise ValueError("there is no halt or confirmation to clear")
            old_peak = document.get("peak")
            document["floor_state"] = "OK"
            document["floor_confirmation"] = None
            document["liquidating"] = False
            document["peak"] = number
            document["alert_active"] = False
            event_time = self.now_fn()
            document.setdefault("peak_events", []).append(
                {
                    "time": _iso(event_time),
                    "by": str(by),
                    "note": str(note),
                    "old_peak": old_peak,
                    "new_peak": number,
                }
            )
            return None, True

        return self._change(change)

    def pause(self, by, note):
        return self._set_pause(True, "pause", by, note)

    def resume(self, by, note):
        return self._set_pause(False, "resume", by, note)

    def _set_pause(self, flag, action, by, note):
        def change(document):
            document["paused"] = flag
            document.setdefault("pause_events", []).append(
                {"time": _iso(self.now_fn()), "action": action, "by": str(by), "note": str(note)}
            )
            return None, True

        return self._change(change)

    def paused(self) -> bool:
        return bool(self._read().get("paused", False))

    def state(self) -> dict:
        return self._read()


def reduce_only(decision: Decision) -> Decision:
    if decision.status != READY:
        return decision
    core_new = min(decision.core_new, decision.core_held)
    ovl_new = min(decision.ovl_new, decision.ovl_held)
    total_new = core_new + ovl_new
    core_trade = abs(core_new - decision.core_held) > 1e-12
    trade = core_trade or abs(ovl_new - decision.ovl_held) > 1e-12
    fraction = ovl_new / total_new if total_new != 0.0 else 0.0
    changed = core_new != decision.core_new or ovl_new != decision.ovl_new
    keys = decision.reason_keys
    if changed:
        if ovl_new <= 1e-12:
            # The extra buy was suppressed: the owner must not read that it was made.
            keys = tuple(key for key in keys if key != "OVERLAY_ENTER")
        if "BREAKER_REDUCE_ONLY" not in keys:
            keys = keys + ("BREAKER_REDUCE_ONLY",)
    return replace(
        decision,
        core_new=core_new,
        ovl_new=ovl_new,
        total_new=total_new,
        core_trade=core_trade,
        trade=trade,
        overlay_fraction_new=fraction,
        reason_keys=keys,
    )


def floor_order_allowed(
    clock_ok,
    calendar_ok,
    market_open,
    seconds_to_close,
    unresolved,
    foreign_product_order,
):
    checks = (
        (not clock_ok, "CLOCK_DOUBT"),
        (not calendar_ok, "CALENDAR_MISSING"),
        (not market_open, "MARKET_CLOSED"),
        (seconds_to_close < 120, "TOO_LATE"),
        (unresolved, "UNRESOLVED"),
        (foreign_product_order, "FOREIGN_PRODUCT_ORDER"),
    )
    for blocked, reason in checks:
        if blocked:
            return False, reason
    return True, None
