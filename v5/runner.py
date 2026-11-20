"""The v5 loop: it decides WHEN things happen and WHICH rule has precedence.

Two ways to fail killed the previous bot: doing nothing without telling anyone, and acting on a state that was
never verified. So every tick writes a heartbeat, every section is guarded on its own, every blocked state ends
in a record and a message, and no order is placed unless every check before it passed in this very tick. That
last rule also holds for an order chain that began in an earlier tick: its next submission is gated again.
"""

from __future__ import annotations

import logging
import math
import re
import sys
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .broker import AlpacaBroker, BrokerError
from .clock import ET, SessionCalendar, parse_calendar, window_for
from .config import ConfigError, load_settings
from .data import InputsNotReady, build_inputs
from .executor import (
    CAPPED, DONE, DRY_RUN, FAILED, HOLD_NOT_READY, MISSED, NOT_READY, NOT_SUBMITTED, NO_TRADE, PARTIAL, UNRESOLVED,
    Executor, SessionRecord, SnapshotInvalid, take_snapshot,
)
from .health import write_heartbeat
from .notify import FLOOR_WAIT_CAUSES, Notifier, Outbox, build_alert, cause_for, default_post
from .packages import get_package
from .rails import Rails, floor_order_allowed, last_equity_is_close_of, read_equity, reduce_only
from .state import ProcessLock, StateCorrupt, StateDirError, atomic_write_json, ensure_state_dir, read_json
from .strategy import READY, decide


log = logging.getLogger("v5.runner")

# Holds the owner already knows about (or chose himself): the cutoff adds no second message for them.
QUIET_CUTOFF_CODES = {"PAUSED", "FOREIGN_ASSET", "UNRESOLVED", "FLOOR_HALT"}
ACCOUNT_PROBLEMS = {"BROKER_DOWN", "ACCOUNT_STATUS", "BLOCKED"}
FIVE_MINUTES = timedelta(minutes=5)
# This long after the reconcile time an account reading no longer shows the closing value of the session.
LATE_CLOSE = timedelta(minutes=5)
# Failing ticks are counted inside this span, so a fault that comes and goes is reported too.
ERROR_WINDOW = timedelta(minutes=30)
# How many sessions back a process that was away still reports (one dated summary each).
CATCH_UP_SESSIONS = 5


def _item_symbol(item) -> str:
    """The ticker of a foreign item text ("position:SPY:0.5:300.0" or "order:SPY:<client id>:<id>")."""
    parts = str(item).split(":")
    return parts[1] if len(parts) > 1 and parts[1] else str(item)


def _open(record) -> bool:
    """A record whose order chain is under way: it has a target and is not final."""
    return record is not None and not record.is_final and record.data.get("target") is not None


def _positive(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


class Runner:
    def __init__(self, settings, broker, now_fn, sleep_fn, post_fn):
        self.settings, self.broker = settings, broker
        self.now_fn, self.sleep_fn = now_fn, sleep_fn
        self.state_dir = Path(settings.state_dir)
        self.params = get_package(settings.package)
        self.dry = not settings.orders_enabled
        self.owner_symbols = tuple(getattr(settings, "owner_symbols", ()) or ())
        self.rails = Rails(settings, self.state_dir, now_fn, sleep_fn)
        self.outbox = Outbox(self.state_dir)
        self.notifier = Notifier(settings, self.outbox, post_fn, now_fn)
        self.executor = Executor(broker, settings, self.state_dir, self.params, now_fn, sleep_fn)
        self.calendar = None
        self._calendar_loaded = False
        self._calendar_fetched_at = None
        self._calendar_tried_at = None
        self.clock_ok = None                    # None: the broker clock was never read
        self.clock_skewed = False               # a broker timestamp was read and disagreed with the host
        self._broker_now = None
        self.market_open = False
        self._clock_checked_at = None
        self._readiness_session = None
        self._readiness_problems = []
        self._breaker_checked_at = None
        self._error_times = []
        self._error_state = None
        self._digest_pending = False
        self._phase = "start"
        self._session = None
        self._last_decision = None
        self._seen_session_days = set()

    # ------------------------------------------------------------------ small helpers
    @staticmethod
    def _state_shape_ok(obj) -> bool:
        def is_date(value):
            try:
                return isinstance(value, str) and bool(date.fromisoformat(value))
            except ValueError:
                return False

        def is_moment(value):
            try:
                return isinstance(value, str) and bool(datetime.fromisoformat(value))
            except ValueError:
                return False

        if obj.get("started_at") is not None and not is_moment(obj["started_at"]):
            return False
        if obj.get("closed_session") is not None and not is_date(obj["closed_session"]):
            return False
        for key in ("closed_sessions", "unknown_sessions"):
            if key in obj and not (isinstance(obj[key], list) and all(is_date(item) for item in obj[key])):
                return False
        if obj.get("cutoff_alerted") is not None and not isinstance(obj["cutoff_alerted"], str):
            return False
        streak = obj.get("overlay_missing_streak", 0)
        return isinstance(streak, int) and not isinstance(streak, bool) and streak >= 0

    def _load_state(self) -> dict:
        path = self.state_dir / "runner.json"
        obj = read_json(path, default=None)
        if obj is None:
            obj = {}                    # no file yet: a new engine
        elif not isinstance(obj, dict) or not self._state_shape_ok(obj):
            # Never replaced by defaults: that would forget which summaries are still owed and call it healthy.
            raise StateCorrupt(path)
        obj.setdefault("started_at", None)
        obj.setdefault("closed_session", None)
        sessions = obj.get("closed_sessions")
        if not isinstance(sessions, list):
            sessions = [obj["closed_session"]] if obj["closed_session"] else []
        sessions = sorted({item for item in sessions if isinstance(item, str)})[-15:]
        obj["closed_sessions"] = sessions
        obj["closed_session"] = sessions[-1] if sessions else None
        obj.setdefault("cutoff_alerted", None)
        obj.setdefault("overlay_missing_streak", 0)
        unknown = obj.get("unknown_sessions")
        obj["unknown_sessions"] = (
            sorted({item for item in unknown if isinstance(item, str)}) if isinstance(unknown, list) else []
        )
        return obj

    def _save_state(self, obj) -> None:
        atomic_write_json(self.state_dir / "runner.json", obj)

    def _finish_session(self, state, day) -> None:
        sessions = set(state.get("closed_sessions") or [])
        sessions.add(day.isoformat())
        ordered = sorted(sessions)[-15:]
        state["closed_sessions"] = ordered
        state["closed_session"] = ordered[-1]
        self._save_state(state)

    def _record(self, day, purpose="dec", create=False, dry_run=None):
        dry_run = self.dry if dry_run is None else bool(dry_run)
        record = SessionRecord.load(self.state_dir, day, purpose=purpose, dry_run=dry_run)
        if record is None and create:
            record = SessionRecord.new(self.state_dir, day, self.settings.mode, purpose=purpose, dry_run=dry_run)
        return record

    @staticmethod
    def _submitted(record) -> bool:
        return bool(
            record is not None
            and any(int(intent.get("submit_count") or 0) > 0 for intent in record.data.get("intents", []))
        )

    def _reported_records(self, day):
        records = [self._record(day), self._record(day, "flr")]
        if self.dry:
            real = [
                self._record(day, dry_run=False),
                self._record(day, "flr", dry_run=False),
            ]
            records = [actual if self._submitted(actual) else dry for dry, actual in zip(records, real)]
        return tuple(records)

    def _snapshot(self, floor=False):
        return take_snapshot(
            self.broker,
            self.params,
            self.executor.own_client_ids(skip_corrupt=floor),
            self.owner_symbols,
        )

    def _not_ready(self, day, code, detail="") -> None:
        record = self._record(day, create=True)
        if record.is_final or record.data.get("target") is not None:
            return
        self.executor.mark_not_ready(record, code, detail)

    @staticmethod
    def _hold_code(record):
        inputs = record.data.get("inputs") if record is not None else None
        if record is not None and record.status in (NOT_READY, HOLD_NOT_READY) and isinstance(inputs, dict):
            return inputs.get("code")
        return None

    def _session_for(self, now):
        if self.calendar is None:
            return None
        return self.calendar.get(now.astimezone(ET).date())

    def _previous_day(self, day):
        earlier = self.calendar.previous(day, 1)
        return earlier[-1] if earlier else None

    def _next_session(self, day):
        for offset in range(1, 15):
            session = self.calendar.get(day + timedelta(days=offset))
            if session is not None:
                return session
        return None

    def _other_open(self, day, purpose) -> bool:
        """An unfinished order chain other than this day's own record: its order may exist, its books are open."""
        own = (day.isoformat(), purpose)
        return any(
            self.dry or (record.data.get("session"), record.data.get("purpose")) != own
            for record in self.executor.open_records(skip_corrupt=purpose == "flr")
        )

    def _stuck(self, now) -> bool:
        """An order of ours whose outcome is still not booked although its session is over (or it is UNRESOLVED)."""
        for record in self.executor.open_records():
            if record.status == UNRESOLVED:
                return True
            try:
                session = self.calendar.get(date.fromisoformat(record.data["session"])) if self.calendar else None
            except (KeyError, TypeError, ValueError):
                session = None
            if session is None or now >= window_for(session).reconcile_until:
                return True
        return False

    # ------------------------------------------------------------------ the tick
    def tick(self) -> None:
        now = self.now_fn()
        self._beat(now)
        failure = None
        for step in (self._calendar_step, self._clock_step):
            try:
                step(now)
            except Exception as exc:            # one broken section never stops the others
                failure = exc
                log.exception("tick section %s failed (%s)", step.__name__, type(exc).__name__)
        effective = now
        if self.clock_skewed and self._broker_now is not None:
            effective = min(now, self._broker_now)
        for step in (self._housekeeping_step, self._session_step):
            try:
                step(effective)
            except Exception as exc:
                failure = exc
                log.exception("tick section %s failed (%s)", step.__name__, type(exc).__name__)
        try:
            self._account_for(failure, now)
        except Exception:
            log.exception("error accounting failed")
        self._beat(self.now_fn())

    def run_forever(self, tick_seconds=20, max_ticks=None) -> None:
        count = 0
        while max_ticks is None or count < max_ticks:
            self.tick()
            count += 1
            self.sleep_fn(tick_seconds)

    def _account_for(self, failure, now) -> None:
        self._error_times = [moment for moment in self._error_times if now - moment <= ERROR_WINDOW]
        if failure is None:
            self._error_state = None
            return
        corrupt = isinstance(failure, StateCorrupt)
        self._error_state = "STATE_CORRUPT" if corrupt else "TICK_ERROR"
        self._error_times.append(now)
        # A broken state file is told at once (the rails cannot work without it). Anything else is told when it
        # keeps coming back, even with good ticks in between.
        if corrupt or len(self._error_times) >= 3:
            self.notifier.alert("ERROR", state_key=now.astimezone(ET).date().isoformat())

    # ------------------------------------------------------------------ heartbeat
    def _states(self, now) -> list:
        states = set()
        if self._phase == "no_calendar":
            states.add("CALENDAR_MISSING")
        if self._error_state:
            states.add(self._error_state)
        if self.dry:
            states.add("DRY_RUN")
        if not self.notifier.configured:
            states.add("NOTIFY_MISSING")
        if self.clock_ok is False:
            states.add("CLOCK_DOUBT")
        if (
            self.clock_ok is True and not self.market_open and self._session is not None
            and self._session.open <= now < self._session.close
        ):
            states.add("MARKET_CLOSED")
        if self._digest_pending:
            states.add("DIGEST_PENDING")
        try:
            document = self.rails.state()
            if document.get("peak") is None:
                states.add("PEAK_MISSING")
            floor = document.get("floor_state")
            if floor == "HALT":
                states.add("FLOOR_HALT")
                if document.get("liquidating"):
                    states.add("RAIL_LIQUIDATING")
            elif floor == "CONFIRMING":
                states.add("RAIL_CONFIRMING")
            if document.get("paused"):
                states.add("PAUSED")
            breaker = document.get("breaker")
            if self._session is not None and breaker and breaker.get("session") == self._session.date.isoformat():
                states.add("BREAKER_TODAY")
            if self._stuck(now):
                states.add("UNRESOLVED")
            if self._load_state().get("overlay_missing_streak", 0) >= 2:
                states.add("OVERLAY_DATA_MISSING")
            self.executor.load_overlay_fraction()
            if self._session is not None:
                record = self._record(self._session.date)
                if record is not None:
                    self._last_decision = {"session": record.data["session"], "status": record.status}
                    code = self._hold_code(record)
                    if code:
                        states.add(code)
        except StateCorrupt:
            states.add("STATE_CORRUPT")
        except Exception:
            states.add("TICK_ERROR")
        return sorted(states)

    def _readiness(self) -> dict:
        problems = list(self._readiness_problems)
        if self.clock_ok is True and "CLOCK_DOUBT" in problems:
            problems.remove("CLOCK_DOUBT")
        try:
            if "PEAK_MISSING" in problems and self.rails.peak() is not None:
                problems.remove("PEAK_MISSING")
        except Exception:
            pass
        return {"ok": not problems, "problems": problems}

    def _beat(self, now) -> None:
        try:
            oldest = None
            try:
                oldest = self.outbox.oldest_undelivered_minutes(now)
            except Exception:
                pass
            write_heartbeat(self.state_dir, {
                "time": now.isoformat(),
                "mode": self.settings.mode,
                "phase": self._phase,
                "session": None if self._session is None else self._session.date.isoformat(),
                "readiness": self._readiness(),
                "states": self._states(now),
                "last_decision": self._last_decision,
                "outbox_oldest_minutes": oldest,
                "ntfy_configured": self.notifier.configured,
                "orders_enabled": self.settings.orders_enabled,
                "package": self.settings.package,
            })
        except Exception as exc:
            log.error("heartbeat could not be written (%s)", type(exc).__name__)

    # ------------------------------------------------------------------ (2) calendar
    def _calendar_step(self, now) -> None:
        today = now.astimezone(ET).date()
        if not self._calendar_loaded:
            self._calendar_loaded = True
            try:
                cached = read_json(self.state_dir / "calendar.json", default=None)
                if isinstance(cached, dict):
                    self.calendar = SessionCalendar.from_json(cached["sessions"])
                    self._calendar_fetched_at = datetime.fromisoformat(cached["fetched_at"])
            except (StateCorrupt, KeyError, TypeError, ValueError):
                log.warning("calendar cache is unreadable; it will be fetched again")
                self.calendar, self._calendar_fetched_at = None, None
        fresh = (
            self.calendar is not None and self.calendar.covers(today)
            and self._calendar_fetched_at is not None
            and now - self._calendar_fetched_at <= timedelta(hours=24)
        )
        if fresh:
            return
        if self._calendar_tried_at is not None and now - self._calendar_tried_at < FIVE_MINUTES:
            return
        self._calendar_tried_at = now
        try:
            rows = self.broker.get_calendar(today - timedelta(days=400), today + timedelta(days=16))
            sessions = parse_calendar(rows)
        except (BrokerError, ValueError) as exc:
            log.warning("calendar fetch failed (%s); the cache is kept", type(exc).__name__)
            return
        if not sessions:
            return
        self.calendar = SessionCalendar(sessions)
        self._calendar_fetched_at = now
        atomic_write_json(
            self.state_dir / "calendar.json",
            {"fetched_at": now.isoformat(), "sessions": self.calendar.to_json()},
        )

    # ------------------------------------------------------------------ (3) broker clock
    def _clock_step(self, now) -> None:
        session = self._session_for(now)
        in_window = session is not None and window_for(session).decide_from <= now < session.close
        # During a session the broker must agree that the market is open: while it does not, ask on every tick.
        disagrees = session is not None and session.open <= now < session.close and not self.market_open
        due = (
            self._clock_checked_at is None or self.clock_ok is not True or in_window or disagrees
            or now - self._clock_checked_at >= FIVE_MINUTES
        )
        if not due:
            return
        self._clock_checked_at = now
        try:
            before = self.now_fn()
            clock = self.broker.get_clock()
            after = self.now_fn()
            self._broker_now = clock.timestamp
            self.clock_ok = before - timedelta(seconds=60) <= clock.timestamp <= after + timedelta(seconds=60)
            self.clock_skewed = not self.clock_ok
            self.market_open = bool(clock.is_open)
        except BrokerError:
            self._broker_now = None
            self.clock_skewed = False
            self.clock_ok, self.market_open = False, False
        except Exception:
            self._broker_now = None
            self.clock_skewed = False
            self.clock_ok, self.market_open = False, False
            raise

    # ------------------------------------------------------------------ (4) housekeeping
    def _housekeeping_step(self, now) -> None:
        self.notifier.flush()
        if self.calendar is None:
            return
        # Every order chain that outlived its session is settled here, whatever its status: a record left open
        # would keep its order id alive, leave the overlay books stale and hide the outcome from the owner.
        for record in self.executor.open_records():
            try:
                session = self.calendar.get(date.fromisoformat(record.data["session"]))
            except (KeyError, TypeError, ValueError):
                session = None
            if session is None:
                log.error("an open order record has no known session: %s", record.path.name)
                continue
            if now < window_for(session).reconcile_from:
                continue                # the session's own business: the decision step drives it, gated
            before = record.status
            settled = self.executor.reconcile(record, session)
            if settled.status != before:
                late = now > window_for(session).reconcile_until + LATE_CLOSE
                self._after_chain(settled, session, late=late)
        self._correction_step(now)

    def _correction_step(self, now) -> None:
        state = self._load_state()
        for day_text in tuple(state["unknown_sessions"]):
            try:
                day = date.fromisoformat(day_text)
            except ValueError:
                state["unknown_sessions"].remove(day_text)
                self._save_state(state)
                continue
            session = self.calendar.get(day)
            if session is None:
                continue
            record, floor_record = self._reported_records(day)
            if any(_open(item) for item in (record, floor_record)):
                continue
            reading = read_equity(self.broker, self.now_fn())
            if not reading.ok:
                continue
            try:
                context = self._digest_context(
                    now, day, reading, reading.equity, None, record, floor_record,
                    as_of_now=True, correction=True,
                )
            except BrokerError:
                continue                # the holdings could not be read: like a failed reading, try again
            self.notifier.digest(day, context)
            state["unknown_sessions"].remove(day_text)
            self._save_state(state)

    # ------------------------------------------------------------------ (5) the session by time
    def _session_step(self, now) -> None:
        today = now.astimezone(ET).date()
        first_tick_today = today not in self._seen_session_days
        self._seen_session_days.add(today)
        try:
            state = self._load_state()
        except StateCorrupt:
            session = self.calendar.get(today) if self.calendar is not None else None
            if session is not None and now < session.open:
                self.notifier.alert(
                    "READINESS", state_key=f"{today.isoformat()}:STATE_CORRUPT",
                    cause=cause_for("STATE_CORRUPT"),
                )
            if session is not None:
                window = window_for(session)
                if window.decide_from <= now < session.close:
                    reading = read_equity(self.broker, self.now_fn())
                    if reading.ok and self.rails.check_floor(reading) == "HALT":
                        self._floor_flow(now, session, window, reading)
            raise
        calendar = self.calendar
        if calendar is None or not calendar.covers(today):
            if state["started_at"] is None and not self.clock_skewed:
                state["started_at"] = now.isoformat()
                self._save_state(state)
            self._phase, self._session = "no_calendar", None
            self._no_calendar_cutoff(now, today, state)
            return
        session = calendar.get(today)
        if state["started_at"] is None and not (
            self.clock_skewed and session is not None and now < session.open
        ):
            state["started_at"] = now.isoformat()
            self._save_state(state)
        self._session = session
        if session is None:
            self._phase = "closed"
            self._digest_pending = not self._catch_up(now, today, state)
            return
        window = window_for(session)
        if (
            first_tick_today and session.open <= now < session.close
            and self._readiness_session != session.date
        ):
            self._readiness_step(now, session)
        if now < session.open - timedelta(minutes=45):
            self._phase = "pre"
            self._digest_pending = not self._catch_up(now, today, state)
        elif now < session.open:
            self._phase = "readiness"
            try:
                self._readiness_step(now, session)
            finally:
                self._digest_pending = not self._catch_up(now, today, state)
        elif now < window.decide_from:
            self._phase = "session"
            try:
                self._breaker_step(now, session)
            finally:
                self._digest_pending = not self._catch_up(now, today, state)
        elif now < session.close:
            self._phase = "decision"
            try:
                self._decision_step(now, session, window, state)
            finally:
                self._digest_pending = not self._catch_up(now, today, state)
        elif now < window.reconcile_from:
            self._phase = "closing"
        else:
            self._phase = "after_close"
            older_done = True
            try:
                older_done = self._catch_up(now, today, state)
            finally:
                today_done = self._after_close(now, session, state)
                self._digest_pending = not (older_done and today_done)

    def _no_calendar_cutoff(self, now, today, state) -> None:
        """A weekday whose session times are unknown: after the usual cutoff the owner is told, once."""
        local = now.astimezone(ET)
        if today.weekday() >= 5 or (local.hour, local.minute) <= (15, 56):
            return
        started = state.get("started_at")
        usual_cutoff = datetime(today.year, today.month, today.day, 15, 56, tzinfo=ET)
        if started is not None and datetime.fromisoformat(started) >= usual_cutoff:
            return                      # the engine was not running when this day's decision was due
        self._not_ready(today, "CALENDAR_MISSING")
        self._cutoff(today, state)

    def _catch_up(self, now, today, state) -> bool:
        """The after-close work of the sessions before today that never got it (the process was not running at
        their close, or the account could not be read): oldest first, at most the last five. A session whose
        report is still pending is retried without holding the other sessions back."""
        if self.clock_skewed:
            return True                 # only current-session work is safe until both clocks agree
        candidates = self.calendar.previous(today, CATCH_UP_SESSIONS + 1)
        if len(candidates) > CATCH_UP_SESSIONS:
            outside = candidates[0]
            started = state.get("started_at")
            session = self.calendar.get(outside)
            if (
                outside.isoformat() not in state["closed_sessions"]
                and session is not None
                and started is not None
                and session.close > datetime.fromisoformat(started)
            ):
                log.error("no summary could be made for %s", outside)
                self._finish_session(state, outside)
        owed = False
        for day in candidates[-CATCH_UP_SESSIONS:]:
            session = self.calendar.get(day)
            if session is None:
                continue
            if not self._after_close(now, session, state):
                owed = True
        return not owed

    # ------------------------------------------------------------------ readiness before the open
    def _readiness_step(self, now, session) -> None:
        if self._readiness_session == session.date:
            return
        self._readiness_session = session.date
        problems = []
        reading = read_equity(self.broker, self.now_fn())
        if not reading.ok:
            problems.append(reading.problem if reading.problem in ACCOUNT_PROBLEMS else "EQUITY_DOUBT")
        try:
            ensure_state_dir(self.state_dir)
        except StateDirError:
            problems.append("STATE_CORRUPT")
        if self.clock_ok is False and "BROKER_DOWN" not in problems:
            problems.append("CLOCK_DOUBT")
        if not self.notifier.configured:
            problems.append("NOTIFY_MISSING")
        try:
            if self.rails.peak() is None:
                problems.append("PEAK_MISSING")
        except StateCorrupt:
            problems.append("STATE_CORRUPT")
        try:
            self.executor.load_overlay_fraction()
        except StateCorrupt:
            problems.append("STATE_CORRUPT")
        self._readiness_problems = problems
        if problems:
            log.warning("readiness problems before the open: %s", ", ".join(problems))
            if now < session.open:
                self.notifier.alert(
                    "READINESS", state_key=f"{session.date.isoformat()}:{problems[0]}",
                    cause=cause_for(problems[0]),
                )

    # ------------------------------------------------------------------ breaker
    def _breaker_step(self, now, session) -> None:
        if self._breaker_checked_at is not None and now - self._breaker_checked_at < FIVE_MINUTES:
            return
        self._breaker_checked_at = now
        self._watch_breaker(read_equity(self.broker, self.now_fn()), session, decisive=False)

    def _backfill_close(self, reading, session) -> None:
        """The closing value of the session before `session`, taken from the broker when the engine has no row
        of its own for it (it was not running at that close, or this is its first day). Call it only while the
        broker's `last_equity` still means exactly that close: during `session`, or shortly after its close."""
        previous = self._previous_day(session.date)
        value = reading.last_equity
        if (
            previous is None or not _positive(value)
            or last_equity_is_close_of(reading, previous) is False
        ):
            return
        history = self.rails.state().get("equity_history", [])
        if history and str(history[-1].get("session", "")) >= previous.isoformat():
            return
        result = self.rails.record_close(
            previous, float(value), raise_peak=True, current_equity=reading.equity,
        )
        if result == "stored_peak_refused":
            self._tell_doubt(session.date.isoformat())
        log.info("closing value of %s taken from the broker: %.2f", previous, value)

    def _tell_doubt(self, state_key) -> None:
        """`DATA_DOUBT` says that no safety sale was made. While the engine is halted that sale has been made
        or is under way: the floor messages and the status line of the summary speak for it instead."""
        if self.rails.floor_state() != "HALT":
            self.notifier.alert("DATA_DOUBT", state_key=state_key)

    def _watch_breaker(self, reading, session, decisive=False) -> bool:
        day = session.date.isoformat()
        info = self.rails.breaker_info()
        was = bool(info and info.get("session") == day)
        if not reading.ok:
            if reading.problem in ("EQUITY_BAD", "INCONSISTENT"):
                self._tell_doubt(day)
            return was
        if "BROKER_DOWN" in self._readiness_problems or "EQUITY_DOUBT" in self._readiness_problems:
            self._readiness_problems = [p for p in self._readiness_problems if p not in ("BROKER_DOWN", "EQUITY_DOUBT")]
        self._backfill_close(reading, session)
        previous = self._previous_day(session.date)
        known = None if previous is None else last_equity_is_close_of(reading, previous)
        if previous is not None and _positive(reading.last_equity) and known is not False:
            confirmed = self.rails.confirm_close(
                previous, reading.last_equity, known is True, reading.equity,
            )
            if confirmed == "corrected":
                self.notifier.alert(
                    "CLOSE_CORRECTED", state_key=previous.isoformat(), date=previous,
                    equity=reading.last_equity,
                )
            elif confirmed == "confirmed_peak_refused":
                self._tell_doubt(day)
        active = self.rails.check_breaker(
            reading, session.date, self._previous_day(session.date), decisive=decisive
        )
        if active:
            info = self.rails.breaker_info() or {}
            baseline, equity = info.get("baseline"), info.get("equity")
            if info.get("cause") == "BREACH" and baseline and equity is not None:
                self.notifier.alert("BREAKER", state_key=day, pct=1.0 - equity / baseline)
            else:
                self._tell_doubt(day)
        return active

    def _breaker_hold(self, session, active):
        if not active:
            return None
        info = self.rails.breaker_info() or {}
        if info.get("session") != session.date.isoformat():
            return None
        return "BREAKER" if info.get("cause") == "BREACH" else "EQUITY_DOUBT"

    # ------------------------------------------------------------------ the decision step
    def _decision_step(self, now, session, window, state) -> None:
        day = session.date
        if self.dry and self._record(day, dry_run=False) is not None:
            return                      # a flag-off process never invents a second, dry decision for a real day
        record, floor_record = self._record(day), self._record(day, "flr")
        if _open(floor_record):
            # A safety sale is under way. A strategy order of the same day stops sending first.
            if _open(record):
                self._after_chain(self.executor.run_chain(record, window, blocked="FLOOR_HALT"), session)
            blocked, problem = self._floor_block(now, session)
            self.executor.run_chain(floor_record, window, blocked=blocked)
            if blocked != "HALT_CLEARED":
                self._floor_report(now, session, blocked)
            if problem is not None:
                raise problem
            return
        if record is not None and record.is_final:
            return
        if _open(record):
            # The chain began in an earlier tick. What it may still send is decided by the state of THIS tick.
            blocked, problem, breaker = self._continue_block(now, session, record)
            self._after_chain(
                self.executor.run_chain(record, window, blocked=blocked, buys_blocked=breaker), session
            )
            if problem is not None:
                raise problem
            return
        if now > window.decide_until:
            self._cutoff(day, state)
            return
        try:
            self._fresh_decision(now, session, window, state)
        except StateCorrupt:
            # Whatever file it was: the day is marked, so the cutoff says "kayıt dosyam bozuk" and not "missed".
            try:
                if self._hold_code(self._record(day)) != "FLOOR_HALT":
                    self._not_ready(day, "STATE_CORRUPT")
            except Exception:
                pass
            raise

    def _fresh_decision(self, now, session, window, state) -> None:
        day = session.date
        reading = read_equity(self.broker, self.now_fn())
        try:
            peak = self.rails.peak()
        except StateCorrupt:
            self._not_ready(day, "STATE_CORRUPT")
            raise
        if peak is None:
            self._not_ready(day, "PEAK_MISSING")
            return
        if reading.ok:
            floor = self.rails.check_floor(reading)
        else:
            floor = self.rails.floor_state()
            if floor == "OK":
                if reading.problem in ACCOUNT_PROBLEMS:
                    self._not_ready(day, reading.problem)
                else:
                    self._tell_doubt(day.isoformat())
                    self._not_ready(day, "EQUITY_DOUBT")
                return
        if floor == "HALT":
            self._floor_flow(now, session, window, reading)
            return
        if floor == "CONFIRMING":
            self._not_ready(day, "RAIL_CONFIRMING")
            return
        breaker = self._watch_breaker(reading, session, decisive=True)

        if self.clock_ok is not True:
            self._not_ready(day, "CLOCK_DOUBT")
            return
        if not self.market_open:
            self._not_ready(day, "MARKET_CLOSED")
            return
        if not self.notifier.configured:
            self._not_ready(day, "NOTIFY_MISSING")
            return
        if self._other_open(day, "dec"):
            self._not_ready(day, "UNRESOLVED")
            self.notifier.alert("UNRESOLVED", state_key=day.isoformat())
            return
        try:
            snapshot = self._snapshot()
        except SnapshotInvalid:
            self._not_ready(day, "SNAPSHOT_INVALID")
            return
        except BrokerError:
            self._not_ready(day, "BROKER_DOWN")
            return
        if snapshot.foreign:
            item = snapshot.foreign[0]
            symbol = _item_symbol(item)
            self._not_ready(day, "FOREIGN_ASSET", item)
            self.notifier.alert(
                "FOREIGN_ASSET", state_key=f"{day.isoformat()}:{symbol}", symbol=symbol,
            )
            return
        if self.rails.paused():
            self._not_ready(day, "PAUSED")
            return
        inputs = build_inputs(self.broker, self.calendar, session, now, self.params)
        if isinstance(inputs, InputsNotReady):
            self._not_ready(day, inputs.code, inputs.detail)
            return
        try:
            fraction = self.executor.load_overlay_fraction()
        except StateCorrupt:
            self._not_ready(day, "STATE_CORRUPT")
            raise
        decision = decide(
            inputs.closes, inputs.px, inputs.session_high, inputs.session_low,
            snapshot.product_weight, fraction, self.params,
        )
        if decision.status != READY:
            self._not_ready(day, decision.reason_code or "DERIVED_BAD")
            return
        breaker_hold = self._breaker_hold(session, breaker)
        if breaker:
            decision = reduce_only(decision, doubt=breaker_hold != "BREAKER")
        record = self._record(day, create=True)
        if not decision.trade:
            self.executor.finalize(record, NO_TRADE, inputs=inputs, decision=decision)
            log.info("decision %s: no trade (%s)", day, ",".join(decision.reason_keys))
            return
        self.executor.attach_decision(record, inputs, decision)
        log.info("decision %s: target %.4f (%s)", day, decision.total_new, ",".join(decision.reason_keys))
        self._after_chain(self.executor.run_chain(record, window, buys_blocked=breaker_hold), session)

    def _continue_block(self, now, session, record):
        """Why a strategy chain that is already under way may not send a NEW order in this tick (or None), and
        the state fault to report afterwards. Its accepted order is still followed, cancelled and booked."""
        try:
            reading = read_equity(self.broker, self.now_fn())
            floor = self.rails.check_floor(reading) if reading.ok else self.rails.floor_state()
            paused = self.rails.paused()
            breaker = self._watch_breaker(reading, session, decisive=True)   # measured again, not remembered
        except Exception as exc:        # whatever broke: nothing new is sent, the order already out is still followed
            return ("STATE_CORRUPT" if isinstance(exc, StateCorrupt) else "ENGINE_FAULT"), exc, None
        breaker_hold = self._breaker_hold(session, breaker)
        if floor == "HALT":
            return "FLOOR_HALT", None, breaker_hold
        if floor == "CONFIRMING":
            return "RAIL_CONFIRMING", None, breaker_hold
        if not reading.ok:
            code = reading.problem if reading.problem in ACCOUNT_PROBLEMS else "EQUITY_DOUBT"
            return code, None, breaker_hold
        if self.clock_ok is not True:
            return "CLOCK_DOUBT", None, breaker_hold
        if not self.market_open:
            return "MARKET_CLOSED", None, breaker_hold
        if not self.notifier.configured:
            return "NOTIFY_MISSING", None, breaker_hold
        try:
            snapshot = self._snapshot()
        except SnapshotInvalid:
            return "SNAPSHOT_INVALID", None, breaker_hold
        except BrokerError:
            return "BROKER_DOWN", None, breaker_hold
        except Exception as exc:
            return ("STATE_CORRUPT" if isinstance(exc, StateCorrupt) else "ENGINE_FAULT"), exc, breaker_hold
        if snapshot.foreign:
            symbol = _item_symbol(snapshot.foreign[0])
            self.notifier.alert(
                "FOREIGN_ASSET", state_key=f"{session.date.isoformat()}:{symbol}", symbol=symbol,
            )
            return "FOREIGN_ASSET", None, breaker_hold
        if paused:
            return "PAUSED", None, breaker_hold
        return None, None, breaker_hold

    def _floor_block(self, now, session):
        """The same question for a safety sale that is under way."""
        try:
            if self.rails.floor_state() != "HALT":
                return "HALT_CLEARED", None     # the owner lifted the halt: nothing more is sold
        except Exception as exc:
            return ("STATE_CORRUPT" if isinstance(exc, StateCorrupt) else "ENGINE_FAULT"), exc
        try:
            snapshot = self._snapshot(floor=True)
        except SnapshotInvalid:
            return "SNAPSHOT_INVALID", None
        except BrokerError:
            return "BROKER_DOWN", None
        except Exception as exc:
            return ("STATE_CORRUPT" if isinstance(exc, StateCorrupt) else "ENGINE_FAULT"), exc
        allowed, reason = floor_order_allowed(
            self.clock_ok is True, True, self.market_open, (session.close - self.now_fn()).total_seconds(),
            self._other_open(session.date, "flr"), bool(snapshot.foreign_product_orders),
        )
        return (None if allowed else reason), None

    @staticmethod
    def _account_weight(record) -> float:
        """The fund's share of the WHOLE account as the record last saw it (the record stores the engine's share)."""
        shown = record.data.get("after") or record.data.get("before") or {}
        weight = float(shown.get("product_weight") or 0.0)
        engine, account = shown.get("equity"), shown.get("account_equity")
        if _positive(engine) and _positive(account):
            weight = weight * float(engine) / float(account)
        return weight

    @staticmethod
    def _not_sent_cause(record) -> str:
        code = str(record.data.get("status_detail") or "").split(":")[0].split("|")[0]
        if code in ("", "CUTOFF_BEFORE_SUBMIT"):
            code = "TIME_OUT"
        return cause_for(code)

    @staticmethod
    def _failure_kind(record) -> str:
        detail = str(record.data.get("status_detail") or "")
        if re.match(r"^\d{3}:", detail):
            return "REFUSED"
        if detail in {"NOT_FOUND", "VALIDATION"}:
            return "NOT_SENT"
        return "UNFILLED"

    @staticmethod
    def _all_submitted_filled(record) -> bool:
        sent = [intent for intent in record.data.get("intents", []) if int(intent.get("submit_count") or 0) > 0]
        return bool(sent) and all(intent.get("order_status") == "filled" for intent in sent)

    def _after_chain(self, record, session, late=False) -> None:
        """Messages that cannot wait for the evening: an order with an unknown outcome, a refused order, and a
        trade that was decided but never sent. `late`: the record was settled long after its session; "today I
        could not send" would then be wrong, and the dated summary of that session says it instead."""
        day = session.date.isoformat()
        real = self.dry and not record.data.get("dry_run", False)
        if record.status == UNRESOLVED:
            self.notifier.alert("UNRESOLVED", state_key=day, real=real)
        elif record.status == FAILED:
            kind = self._failure_kind(record)
            if kind == "REFUSED":
                self.notifier.alert(
                    "ORDER_FAILED", state_key=day, product_pct=self._account_weight(record), real=real,
                )
            elif record.data.get("purpose") == "dec" and kind == "NOT_SENT" and not late:
                self.notifier.alert(
                    "NOT_SENT", state_key=day, cause=self._not_sent_cause(record), real=real,
                )
            elif record.data.get("purpose") == "dec" and kind == "UNFILLED" and not late:
                self.notifier.alert(
                    "ORDER_UNFILLED", state_key=day, product_pct=self._account_weight(record), real=real,
                )
        elif record.status == NOT_SUBMITTED and record.data.get("purpose") == "dec" and not late:
            self.notifier.alert("NOT_SENT", state_key=day, cause=self._not_sent_cause(record), real=real)

    def _cutoff(self, day, state, quiet=False) -> None:
        """Nothing was decided in time: the record is closed and the owner is told, once per session. `quiet`:
        the dated summary that follows carries the news instead of a message that says "today"."""
        if self.dry and any(self._submitted(item) for item in self._reported_records(day)):
            return
        record = self._record(day, create=True)
        if record.is_final or record.data.get("target") is not None:
            return
        code = self._hold_code(record)
        if code is None:
            self.executor.finalize(record, MISSED)
            if not quiet:
                self._cutoff_alert(day, state, "MISSED")
            return
        self.executor.finalize(record, HOLD_NOT_READY, code)
        if code not in QUIET_CUTOFF_CODES and not quiet:
            self._cutoff_alert(day, state, "HOLD_NOT_READY", cause=cause_for(code))

    def _cutoff_alert(self, day, state, kind, **fields) -> None:
        if state.get("cutoff_alerted") == day.isoformat():
            return
        self.notifier.alert(kind, state_key=day.isoformat(), **fields)
        state["cutoff_alerted"] = day.isoformat()
        self._save_state(state)

    # ------------------------------------------------------------------ the floor
    def _floor_numbers(self, reading, snapshot):
        peak = self.rails.peak()
        equity = reading.equity if reading.ok else (snapshot.account_equity or snapshot.equity)
        drawdown = 0.0 if not peak else max(0.0, 1.0 - equity / peak)
        account = snapshot.account_equity or snapshot.equity
        held = snapshot.product_weight * snapshot.equity / account if account else 0.0
        return drawdown, equity, held

    def _floor_done(self, drawdown, equity) -> None:
        """The account is flat: said once, when the sale is first seen complete."""
        if self.rails.floor_state() == "HALT" and self.rails.liquidating():
            filled = self._floor_filled()
            self.rails.set_liquidating(False)
            self.notifier.alert(
                "FLOOR_DONE" if filled else "FLOOR_FLAT",
                state_key=f"halt:{self.now_fn().astimezone(ET).date().isoformat()}",
                pct=drawdown, equity=equity,
            )

    def _floor_filled(self, record=None) -> bool:
        halt_day = None
        halt_time = self.rails.state().get("halt_time")
        if halt_time:
            try:
                halt_day = datetime.fromisoformat(halt_time).astimezone(ET).date()
            except (TypeError, ValueError):
                halt_day = None
        records = [record] if record is not None else [
            SessionRecord(path, obj)
            for path, obj in self.executor._records(refresh=True, skip_corrupt=True)
            if obj.get("purpose") == "flr"
            and (
                halt_day is None
                or date.fromisoformat(obj.get("session", "0001-01-01")) >= halt_day
            )
        ]
        return any(
            Decimal(str(intent.get("filled_qty") or "0")) > 0
            for item in records if item is not None
            for intent in item.data.get("intents", [])
        )

    @staticmethod
    def _floor_submitted(record) -> bool:
        return bool(
            record is not None
            and any(int(intent.get("submit_count") or 0) > 0 for intent in record.data.get("intents", []))
        )

    def _remember_floor_wait(self, day, reason) -> None:
        record = self._record(day, create=True)
        if record.data.get("target") is None and not record.is_final:
            self.executor.mark_not_ready(record, "FLOOR_HALT", reason)

    def _floor_pending(self, day, drawdown, equity, held, reason=None, holding_known=True) -> None:
        record = self._record(day, "flr")
        if self._floor_submitted(record):
            self.notifier.alert(
                "FLOOR_PENDING", state_key=f"{day.isoformat()}:pending",
                pct=drawdown, equity=equity, product_pct=held,
            )
            return
        recorded = record.data.get("hold") if record is not None else None
        reason = "DRY_RUN" if self.dry else (reason or recorded or "SNAPSHOT_INVALID")
        self._remember_floor_wait(day, reason)
        fields = {
            "pct": drawdown,
            "equity": equity,
            "cause": FLOOR_WAIT_CAUSES.get(reason, FLOOR_WAIT_CAUSES["STATE_CORRUPT"]),
        }
        if holding_known:
            fields["product_pct"] = held
        self.notifier.alert(
            "FLOOR_WAITING" if holding_known else "FLOOR_WAITING_BLIND",
            state_key=f"{day.isoformat()}:pending", **fields,
        )

    def _floor_flow(self, now, session, window, reading) -> None:
        day = session.date
        try:
            snapshot = self._snapshot(floor=True)
        except (SnapshotInvalid, BrokerError, StateCorrupt) as exc:
            # The sale cannot even be looked at. While it is still owed, the day's record says why, so that the
            # cutoff tells the owner instead of staying quiet.
            code = (
                "SNAPSHOT_INVALID" if isinstance(exc, SnapshotInvalid)
                else "STATE_CORRUPT" if isinstance(exc, StateCorrupt)
                else "BROKER_DOWN"
            )
            self._not_ready(day, code if self.rails.liquidating() else "FLOOR_HALT")
            peak = self.rails.peak()
            drawdown = 0.0 if not peak or not reading.ok else max(0.0, 1.0 - reading.equity / peak)
            if reading.ok:
                self._floor_pending(day, drawdown, reading.equity, 0.0, code, holding_known=False)
            return
        self._not_ready(day, "FLOOR_HALT")
        drawdown, equity, held = self._floor_numbers(reading, snapshot)
        if Decimal(snapshot.product_qty) <= 0:
            self._floor_done(drawdown, equity)
            return
        # The fund is held during a halt (also when it came back after an earlier complete sale): a sale is owed
        # again, and the owner hears about it like the first time.
        if not self.rails.set_liquidating(True):
            return
        allowed, reason = floor_order_allowed(
            self.clock_ok is True, True, self.market_open, (session.close - self.now_fn()).total_seconds(),
            self._other_open(day, "flr"), bool(snapshot.foreign_product_orders),
        )
        if not allowed:
            log.warning("floor sale is waiting (%s)", reason)
            self._floor_pending(day, drawdown, equity, held, reason)
            return
        record = self._record(day, "flr", create=True)
        if record.data.get("target") is None:
            self.executor.attach_floor(record)
        self.executor.run_chain(record, window)
        self._floor_report(now, session)

    def _floor_report(self, now, session, reason=None) -> None:
        try:
            snapshot = self._snapshot(floor=True)
        except (SnapshotInvalid, BrokerError):
            return
        reading = read_equity(self.broker, self.now_fn())
        drawdown, equity, held = self._floor_numbers(reading, snapshot)
        if Decimal(snapshot.product_qty) <= 0:
            self._floor_done(drawdown, equity)
        else:
            self._floor_pending(session.date, drawdown, equity, held, reason)

    def _settle_floor(self, reading) -> None:
        """After the close: a safety sale that finished while nobody was looking is reported as finished."""
        if self.rails.floor_state() != "HALT":
            return
        try:
            snapshot = self._snapshot(floor=True)
        except (SnapshotInvalid, BrokerError):
            return
        if Decimal(snapshot.product_qty) > 0:
            if not self.rails.set_liquidating(True):
                return
        elif self.rails.liquidating():
            drawdown, equity, _ = self._floor_numbers(reading, snapshot)
            self._floor_done(drawdown, equity)

    # ------------------------------------------------------------------ after the close
    def _after_close(self, now, session, state) -> bool:
        day = session.date
        if day.isoformat() in state["closed_sessions"]:
            return True
        started = state.get("started_at")
        if started is not None and session.close <= datetime.fromisoformat(started):
            # This session ended before the engine ever ran: it is not ours to report.
            self._finish_session(state, day)
            return True
        if self.clock_ok is not True and self.market_open:
            return False                    # the host may only think this session has closed; retry next tick
        doubtful_closed_clock = self.clock_ok is not True
        window = window_for(session)
        late = now > window.reconcile_until + LATE_CLOSE
        if self.dry:
            # A dry-run record has no order at the broker: one that a dead process left open is simply closed
            # (housekeeping only settles records of real orders).
            for purpose in ("flr", "dec"):
                leftover = self._record(day, purpose)
                if _open(leftover):
                    self._after_chain(self.executor.reconcile(leftover, session), session, late=late)
        record, floor_record = self._reported_records(day)
        if any(_open(item) for item in (floor_record, record)) and now < window.reconcile_until:
            # Housekeeping is still asking the broker what became of the day's order: give it a few minutes
            # before a summary that would have to call the outcome unknown.
            return False
        self._cutoff(day, state, quiet=late)

        reading = read_equity(self.broker, self.now_fn())
        if not reading.ok:
            return False
        self._settle_floor(reading)

        # Which number is the closing value of this session? On time: the reading itself. Late, during the next
        # session: the broker's own `last_equity`. Late and before the next open: nothing is stored (the value
        # now may hold an overnight move); the row is taken from the broker once the next session is on.
        previous, following = self._previous_day(day), self._next_session(day)
        if doubtful_closed_clock:
            close_equity, shown = None, reading.equity
        elif not late:
            self._backfill_close(reading, session)
            close_equity = shown = reading.equity
        elif (
            _positive(reading.last_equity)
            and (
                last_equity_is_close_of(reading, day) is True
                or (
                    last_equity_is_close_of(reading, day) is None
                    and following is not None and following.open <= now < following.close
                )
            )
        ):
            close_equity = shown = float(reading.last_equity)
        else:
            close_equity, shown = None, reading.equity
        base = None
        if close_equity is not None:
            rows = {str(row.get("session")): row.get("equity") for row in self.rails.state().get("equity_history", [])}
            if previous is not None and previous.isoformat() in rows:
                base = rows[previous.isoformat()]
            elif not late and last_equity_is_close_of(reading, previous) is not False:
                base = reading.last_equity
            result = self.rails.record_close(
                day,
                close_equity,
                raise_peak=late,
                current_equity=reading.equity if late else None,
            )
            if result == "stored_peak_refused":
                self._tell_doubt(now.astimezone(ET).date().isoformat())
        if self.rails.floor_state() != "HALT":
            drawdown = self.rails.check_alert(reading)
            if self.rails.alert_pending():
                peak = self.rails.peak()
                drawdown = 0.0 if not peak else max(0.0, 1.0 - reading.equity / peak)
                self.notifier.alert("DRAWDOWN_ALERT", state_key=day.isoformat(), pct=drawdown, equity=reading.equity)
                self.rails.mark_alert_told()

        record, floor_record = self._reported_records(day)
        if any(_open(item) for item in (record, floor_record)):
            unknown = set(state.get("unknown_sessions") or [])
            unknown.add(day.isoformat())
            state["unknown_sessions"] = sorted(unknown)
        decision = record.data.get("decision") if record is not None else None
        missing = bool(isinstance(decision, dict) and decision.get("overlay_data_missing"))
        state["overlay_missing_streak"] = int(state.get("overlay_missing_streak") or 0) + 1 if missing else 0

        try:
            context = self._digest_context(
                now, day, reading, shown, base, record, floor_record,
                as_of_now=close_equity is None, late=late,
            )
        except BrokerError:
            return False                # the holdings could not be read: like a failed reading, try again
        self.notifier.digest(day, context)
        self._finish_session(state, day)
        log.info("session %s closed: equity %.2f%s", day, shown, " (reported late)" if late else "")
        return True

    def _digest_context(
        self, now, day, reading, equity, base, record, floor_record,
        as_of_now=False, correction=False, late=False,
    ) -> dict:
        try:
            snapshot = self._snapshot()
            account = snapshot.account_equity or snapshot.equity
            product_weight = snapshot.product_weight * snapshot.equity / account
            owner_weight = snapshot.owner_value / account
            owner_label = ", ".join(_item_symbol(item) for item in snapshot.owner_positions)
            foreign_value = sum(
                float(str(item).rsplit(":", 1)[-1]) for item in snapshot.foreign_positions
            )
            other_weight = foreign_value / account
            cash_weight = snapshot.cash / account
            flat = Decimal(snapshot.product_qty) <= 0
        except (SnapshotInvalid, BrokerError, InvalidOperation, StateCorrupt, ValueError):
            try:
                positions = self.broker.get_positions()
                product = next((item for item in positions if item.symbol == self.params.product_symbol), None)
                owners = [item for item in positions if item.symbol.upper() in self.owner_symbols]
                foreign = [
                    item for item in positions
                    if item.symbol != self.params.product_symbol and item.symbol.upper() not in self.owner_symbols
                ]
                product_value = 0.0 if product is None else float(product.market_value)
                owner_value = sum(float(item.market_value) for item in owners)
                other_value = sum(float(item.market_value) for item in foreign)
                product_weight = product_value / reading.equity
                owner_weight = owner_value / reading.equity
                other_weight = other_value / reading.equity
                cash_weight = reading.cash / reading.equity
                owner_label = ", ".join(item.symbol for item in owners)
                flat = product is None or Decimal(str(product.qty)) <= 0
            except (InvalidOperation, TypeError, ZeroDivisionError):
                product_weight = owner_weight = other_weight = 0.0
                cash_weight = reading.cash / reading.equity
                owner_label, flat = "", True

        bought = sold = 0.0
        last_filled_kind = last_filled_status = None
        for item in (record, floor_record):
            if item is None:
                continue
            for intent in item.data.get("intents", []):
                try:
                    quantity = Decimal(str(intent.get("filled_qty") or "0"))
                except InvalidOperation:
                    continue
                price = intent.get("filled_avg_price")
                if quantity <= 0 or not price:
                    continue
                last_filled_kind, last_filled_status = intent.get("kind"), item.status
                if intent.get("side") == "buy":
                    bought += float(quantity) * float(price)
                elif intent.get("side") == "sell":
                    sold += float(quantity) * float(price)
        open_day = any(_open(item) for item in (record, floor_record))
        if open_day:
            action, action_usd = "unknown", 0.0         # an order went out and its outcome is not known yet
        elif last_filled_kind == "sell_all" and last_filled_status == DONE:
            action, action_usd = "sell_all", sold
        elif sold > 0 and sold >= bought:
            action, action_usd = "sell", sold
        elif bought > 0:
            action, action_usd = "buy", bought
        else:
            action, action_usd = "none", 0.0

        dry_record = (
            floor_record if floor_record is not None and floor_record.status == DRY_RUN
            else record if record is not None and record.status == DRY_RUN
            else None
        )
        if dry_record is not None:
            intents = dry_record.data.get("intents")
            intent = intents[-1] if isinstance(intents, list) and intents else None
            if isinstance(intent, dict):
                action = {
                    "buy": "would_buy", "sell": "would_sell", "sell_all": "would_sell_all", "none": "none",
                }.get(intent.get("kind"), "none")
                if intent.get("side") == "buy":
                    action_usd = intent.get("notional") or intent.get("desired_usd") or 0.0
                else:
                    action_usd = intent.get("desired_usd") or 0.0
            else:
                action, action_usd = "none", 0.0

        # The reasons of a decision are only shown when the decision was carried out. Otherwise the summary says
        # what happened to it, never what it would have done.
        outcome = None
        if _open(record):
            outcome = "UNKNOWN"
        elif record is not None and record.status == NOT_SUBMITTED:
            outcome = "NOT_SENT"
        elif record is not None and record.status == FAILED:
            outcome = self._failure_kind(record)
        elif record is not None and record.status == PARTIAL:
            outcome = "PARTIAL"
        elif record is not None and record.status == CAPPED:
            intents = record.data.get("intents") or []
            if not self._submitted(record) and intents and intents[-1].get("kind") == "none":
                outcome = "NO_CASH"
        elif floor_record is not None or self._hold_code(record) == "FLOOR_HALT":
            outcome = "FLOOR"
        decision = record.data.get("decision") if record is not None else None
        reason_keys = None
        if outcome is None and isinstance(decision, dict):
            reason_keys = list(decision.get("reason_keys") or [])
        if isinstance(reason_keys, list):
            if record is not None and record.status == DRY_RUN:
                reason_keys = ["OVERLAY_WOULD" if key == "OVERLAY_ENTER" else key for key in reason_keys]
            if "OVERLAY_ENTER" in reason_keys and bought <= 0:
                before = record.data.get("before") if record is not None else None
                entered = (
                    isinstance(before, dict)
                    and float(record.data.get("overlay_fraction_committed") or 0.0) > 1e-12
                    and float(before.get("overlay_fraction_committed") or 0.0) <= 1e-12
                )
                reason_keys = [
                    "OVERLAY_HELD" if key == "OVERLAY_ENTER" and entered else key
                    for key in reason_keys
                    if key != "OVERLAY_ENTER" or entered
                ]
            if sold <= 0:
                reason_keys = [key for key in reason_keys if key != "OVERLAY_EXIT"]

        history = self.rails.state().get("equity_history", [])
        month_start = day.replace(day=1).isoformat()
        before_month = [row for row in history if row["session"] < month_start]
        month_base = before_month[-1]["equity"] if before_month else (history[0]["equity"] if history else None)
        total_base = history[0]["equity"] if history else None

        real_run = any(self._submitted(item) and not item.data.get("dry_run") for item in (record, floor_record) if item)
        if self.settings.mode == "paper":
            prefix = "paper"
        else:
            prefix = "dry_run" if self.dry and not real_run else ""
        day_usd = equity - base if _positive(base) else None
        return {
            "date": day,
            "prefix": prefix,
            "equity": equity,
            "as_of_now": bool(as_of_now),
            "holdings_now": bool(late),
            "correction": bool(correction),
            "day_usd": day_usd,
            "day_pct": None if day_usd is None else day_usd / base,
            "product_weight": product_weight,
            "owner_weight": owner_weight,
            "owner_label": owner_label,
            "other_weight": other_weight,
            "cash_weight": cash_weight,
            "action": action,
            "action_usd": action_usd,
            "reason_keys": reason_keys,
            "outcome": outcome,
            "month_pct": None if not month_base else equity / month_base - 1.0,
            "total_pct": None if not total_base else equity / total_base - 1.0,
            "status_text": self._status_text(now, reading, record, floor_record, product_weight, open_day, flat),
        }

    def _status_text(self, now, reading, record, floor_record, product_weight, open_day, flat):
        """The sentence of the highest active state, or None when everything is normal."""
        document = self.rails.state()
        if document.get("floor_state") == "HALT":
            if not flat:
                if self._floor_submitted(floor_record):
                    return build_alert("RAIL_LIQUIDATING", product_pct=product_weight)
                inputs = record.data.get("inputs") if record is not None else None
                reason = inputs.get("detail") if isinstance(inputs, dict) else None
                reason = "DRY_RUN" if self.dry else (reason or "SNAPSHOT_INVALID")
                peak = document.get("peak")
                drawdown = 0.0 if not peak else max(0.0, 1.0 - reading.equity / peak)
                return build_alert(
                    "FLOOR_WAITING",
                    pct=drawdown,
                    equity=reading.equity,
                    product_pct=product_weight,
                    cause=FLOOR_WAIT_CAUSES.get(reason, FLOOR_WAIT_CAUSES["STATE_CORRUPT"]),
                )
            peak = document.get("peak")
            drawdown = 0.0 if not peak else max(0.0, 1.0 - reading.equity / peak)
            return build_alert(
                "FLOOR_DONE" if self._floor_filled(floor_record) else "FLOOR_FLAT",
                pct=drawdown,
                equity=reading.equity,
            )
        if floor_record is not None and self._floor_filled(floor_record):
            return None                 # the sale is in lines 4/5; a lifted halt is no longer an active status
        if open_day or self._stuck(now):
            return build_alert("UNRESOLVED")
        code = self._hold_code(record)
        if code == "FLOOR_HALT":
            return None
        if code == "FOREIGN_ASSET":
            return build_alert("FOREIGN_ASSET", symbol=_item_symbol(record.data["inputs"].get("detail") or ""))
        records = [item for item in (record, floor_record) if item is not None]
        for item in records:
            if item.status != FAILED:
                continue
            kind = self._failure_kind(item)
            if kind == "REFUSED":
                return build_alert("ORDER_FAILED", product_pct=product_weight)
            if kind == "NOT_SENT":
                return build_alert("NOT_SENT", cause=self._not_sent_cause(item))
            return build_alert("ORDER_UNFILLED", product_pct=product_weight)
        for item in records:
            if item.status == PARTIAL or (
                item.status == CAPPED and self._submitted(item) and not self._all_submitted_filled(item)
            ):
                return build_alert("ORDER_PARTIAL", product_pct=product_weight)
            if item.status == CAPPED and self._all_submitted_filled(item):
                return build_alert("CAPPED_FILLED")
            intents = item.data.get("intents") or []
            if item.status == CAPPED and not self._submitted(item) and intents and intents[-1].get("kind") == "none":
                return build_alert("CAPPED_IDLE")
        if record is not None and record.status == NOT_SUBMITTED:
            return build_alert("NOT_SENT", cause=self._not_sent_cause(record))
        if document.get("paused"):
            return build_alert("PAUSED")
        if record is not None and record.status == HOLD_NOT_READY:
            return build_alert("HOLD_NOT_READY", cause=cause_for(code))
        if record is not None and record.status == MISSED:
            return build_alert("MISSED")
        return None


def main() -> int:
    logging.basicConfig(
        level=logging.INFO, stream=sys.stdout,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"v5 cannot start: {exc}", file=sys.stderr)
        return 2
    try:
        ensure_state_dir(settings.state_dir)
    except StateDirError as exc:
        print(f"v5 cannot start: {exc}", file=sys.stderr)
        return 2
    lock = ProcessLock(Path(settings.state_dir) / "process.lock")
    if not lock.acquire():
        print("v5 cannot start: another process holds the state directory", file=sys.stderr)
        return 3

    def utc_now():
        return datetime.now(timezone.utc)

    try:
        log.info("v5 starting: %r", settings)
        runner = Runner(settings, AlpacaBroker(settings), utc_now, time.sleep, default_post)
        runner.run_forever()
        return 0
    except Exception as exc:
        log.exception("v5 stopped (%s)", type(exc).__name__)
        try:
            notifier = Notifier(settings, Outbox(settings.state_dir), default_post, utc_now)
            notifier.alert("NOT_STARTED")
            notifier.flush()
        except Exception:
            log.error("the NOT_STARTED message could not be queued")
        return 1
    finally:
        lock.release()


if __name__ == "__main__":
    raise SystemExit(main())
