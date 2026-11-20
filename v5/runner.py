"""The v5 loop: it decides WHEN things happen and WHICH rule has precedence.

Two ways to fail killed the previous bot: doing nothing without telling anyone, and acting on a state that was
never verified. So every tick writes a heartbeat, every section is guarded on its own, every blocked state ends
in a record and a message, and no order is placed unless every check before it passed in this very tick. That
last rule also holds for an order chain that began in an earlier tick: its next submission is gated again.
"""

from __future__ import annotations

import logging
import math
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
    CAPPED, DRY_RUN, FAILED, HOLD_NOT_READY, MISSED, NOT_READY, NOT_SUBMITTED, NO_TRADE, PARTIAL, UNRESOLVED,
    Executor, SessionRecord, SnapshotInvalid, take_snapshot,
)
from .health import write_heartbeat
from .notify import Notifier, Outbox, build_alert, cause_for, default_post
from .packages import get_package
from .rails import Rails, floor_order_allowed, read_equity, reduce_only
from .state import ProcessLock, StateCorrupt, StateDirError, atomic_write_json, ensure_state_dir, read_json
from .strategy import READY, decide


log = logging.getLogger("v5.runner")

# Holds the owner already knows about (or chose himself): the cutoff adds no second message for them.
QUIET_CUTOFF_CODES = {"PAUSED", "FOREIGN_ASSET", "UNRESOLVED", "FLOOR_HALT"}
ACCOUNT_PROBLEMS = {"BROKER_DOWN", "ACCOUNT_STATUS", "BLOCKED"}
FIVE_MINUTES = timedelta(minutes=5)
# This long after the reconcile time an account reading no longer shows the closing value of the session.
LATE_CLOSE = timedelta(minutes=30)
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

    # ------------------------------------------------------------------ small helpers
    def _load_state(self) -> dict:
        obj = read_json(self.state_dir / "runner.json", default=None)
        if not isinstance(obj, dict):
            obj = {}
        obj.setdefault("started_at", None)
        obj.setdefault("closed_session", None)
        obj.setdefault("cutoff_alerted", None)
        obj.setdefault("overlay_missing_streak", 0)
        return obj

    def _save_state(self, obj) -> None:
        atomic_write_json(self.state_dir / "runner.json", obj)

    def _record(self, day, purpose="dec", create=False):
        record = SessionRecord.load(self.state_dir, day, purpose=purpose, dry_run=self.dry)
        if record is None and create:
            record = SessionRecord.new(self.state_dir, day, self.settings.mode, purpose=purpose, dry_run=self.dry)
        return record

    def _snapshot(self):
        return take_snapshot(self.broker, self.params, self.executor.own_client_ids(), self.owner_symbols)

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
            for record in self.executor.open_records()
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
        for step in (self._calendar_step, self._clock_step, self._housekeeping_step, self._session_step):
            try:
                step(now)
            except Exception as exc:            # one broken section never stops the others
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
            self.notifier.alert("ERROR")

    # ------------------------------------------------------------------ heartbeat
    def _states(self, now) -> list:
        states = set()
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
            if self._session is not None:
                record = self._record(self._session.date)
                if record is not None:
                    self._last_decision = {"session": record.data["session"], "status": record.status}
                    if self._hold_code(record) == "FOREIGN_ASSET":
                        states.add("FOREIGN_ASSET")
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
            self._clock_checked_at is None or in_window or disagrees
            or now - self._clock_checked_at >= FIVE_MINUTES
        )
        if not due:
            return
        self._clock_checked_at = now
        try:
            clock = self.broker.get_clock()
            self.clock_ok = abs((clock.timestamp - now).total_seconds()) <= 60.0
            self.market_open = bool(clock.is_open)
        except BrokerError:
            self.clock_ok, self.market_open = False, False
        except Exception:
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

    # ------------------------------------------------------------------ (5) the session by time
    def _session_step(self, now) -> None:
        today = now.astimezone(ET).date()
        state = self._load_state()
        if state["started_at"] is None:
            state["started_at"] = now.isoformat()
            self._save_state(state)
        calendar = self.calendar
        if calendar is None or not calendar.covers(today):
            self._phase, self._session = "no_calendar", None
            self._no_calendar_cutoff(now, today, state)
            return
        session = calendar.get(today)
        self._session = session
        if session is None:
            self._phase = "closed"
            self._catch_up(now, today, state)
            return
        window = window_for(session)
        if now < session.open - timedelta(minutes=45):
            self._phase = "pre"
            self._catch_up(now, today, state)
        elif now < session.open:
            self._phase = "readiness"
            try:
                self._readiness_step(now, session)
            finally:
                self._catch_up(now, today, state)
        elif now < window.decide_from:
            self._phase = "session"
            try:
                self._breaker_step(now, session)
            finally:
                self._catch_up(now, today, state)       # a process that came back after the open still owes it
        elif now < session.close:
            self._phase = "decision"
            try:
                self._decision_step(now, session, window, state)
            finally:
                self._catch_up(now, today, state)       # today's decision never waits for yesterday's report
        elif now < window.reconcile_from:
            self._phase = "closing"
        else:
            self._phase = "after_close"
            try:
                # The reports still owed for earlier sessions go first, and this is their last chance: the
                # marker of closed sessions only moves forward.
                self._catch_up(now, today, state, give_up=True)
            finally:
                self._after_close(now, session, state)

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

    def _catch_up(self, now, today, state, give_up=False) -> None:
        """The after-close work of the sessions before today that never got it (the process was not running at
        their close, or the account could not be read): oldest first, at most the last five. A session whose
        report is still pending holds the later ones back, so the marker of closed sessions moves in order."""
        for day in self.calendar.previous(today, CATCH_UP_SESSIONS):
            session = self.calendar.get(day)
            if session is None:
                continue
            self._after_close(now, session, state, give_up=give_up)
            if (state.get("closed_session") or "") < day.isoformat():
                return

    # ------------------------------------------------------------------ readiness before the open
    def _readiness_step(self, now, session) -> None:
        if self._readiness_session == session.date:
            return
        self._readiness_session = session.date
        problems = []
        reading = read_equity(self.broker, now)
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
        self._readiness_problems = problems
        if problems:
            log.warning("readiness problems before the open: %s", ", ".join(problems))
            self.notifier.alert(
                "READINESS", state_key=f"{session.date.isoformat()}:{problems[0]}", cause=cause_for(problems[0])
            )

    # ------------------------------------------------------------------ breaker
    def _breaker_step(self, now, session) -> None:
        if self._breaker_checked_at is not None and now - self._breaker_checked_at < FIVE_MINUTES:
            return
        self._breaker_checked_at = now
        self._watch_breaker(read_equity(self.broker, now), session)

    def _backfill_close(self, reading, session) -> None:
        """The closing value of the session before `session`, taken from the broker when the engine has no row
        of its own for it (it was not running at that close, or this is its first day). Call it only while the
        broker's `last_equity` still means exactly that close: during `session`, or shortly after its close."""
        previous = self._previous_day(session.date)
        value = reading.last_equity
        if previous is None or not _positive(value):
            return
        history = self.rails.state().get("equity_history", [])
        if history and str(history[-1].get("session", "")) >= previous.isoformat():
            return
        self.rails.record_close(previous, float(value))
        log.info("closing value of %s taken from the broker: %.2f", previous, value)

    def _watch_breaker(self, reading, session) -> bool:
        day = session.date.isoformat()
        info = self.rails.breaker_info()
        was = bool(info and info.get("session") == day)
        if not reading.ok:
            if reading.problem in ("EQUITY_BAD", "INCONSISTENT"):
                self.notifier.alert("DATA_DOUBT", state_key=day)
            return was
        if "BROKER_DOWN" in self._readiness_problems or "EQUITY_DOUBT" in self._readiness_problems:
            self._readiness_problems = [p for p in self._readiness_problems if p not in ("BROKER_DOWN", "EQUITY_DOUBT")]
        self._backfill_close(reading, session)
        active = self.rails.check_breaker(reading, session.date, self._previous_day(session.date))
        if active and not was:
            info = self.rails.breaker_info() or {}
            baseline, equity = info.get("baseline"), info.get("equity")
            if info.get("cause") == "BREACH" and baseline and equity is not None:
                self.notifier.alert("BREAKER", state_key=day, pct=1.0 - equity / baseline)
            else:
                self.notifier.alert("DATA_DOUBT", state_key=day)
        return active

    # ------------------------------------------------------------------ the decision step
    def _decision_step(self, now, session, window, state) -> None:
        day = session.date
        record, floor_record = self._record(day), self._record(day, "flr")
        if _open(floor_record):
            # A safety sale is under way. A strategy order of the same day stops sending first.
            if _open(record):
                self._after_chain(self.executor.run_chain(record, window, blocked="FLOOR_HALT"), session)
            blocked, problem = self._floor_block(now, session)
            self.executor.run_chain(floor_record, window, blocked=blocked)
            if blocked != "HALT_CLEARED":
                self._floor_report(now, session)
            if problem is not None:
                raise problem
            return
        if record is not None and record.is_final:
            return
        if _open(record):
            # The chain began in an earlier tick. What it may still send is decided by the state of THIS tick.
            blocked, problem = self._continue_block(now, session, record)
            self._after_chain(self.executor.run_chain(record, window, blocked=blocked), session)
            if problem is not None:
                raise problem
            return
        if now > window.decide_until:
            self._cutoff(day, state)
            return

        reading = read_equity(self.broker, now)
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
                    self.notifier.alert("DATA_DOUBT", state_key=day.isoformat())
                    self._not_ready(day, "EQUITY_DOUBT")
                return
        if floor == "HALT":
            self._floor_flow(now, session, window, reading)
            return
        if floor == "CONFIRMING":
            self._not_ready(day, "RAIL_CONFIRMING")
            return
        breaker = self._watch_breaker(reading, session)

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
            self.notifier.alert("FOREIGN_ASSET", state_key=symbol, symbol=symbol)
            return
        if self.rails.paused():
            self._not_ready(day, "PAUSED")
            return
        inputs = build_inputs(self.broker, self.calendar, session, now, self.params)
        if isinstance(inputs, InputsNotReady):
            self._not_ready(day, inputs.code, inputs.detail)
            return
        decision = decide(
            inputs.closes, inputs.px, inputs.session_high, inputs.session_low,
            snapshot.product_weight, self.executor.load_overlay_fraction(), self.params,
        )
        if decision.status != READY:
            self._not_ready(day, decision.reason_code or "DERIVED_BAD")
            return
        if breaker:
            decision = reduce_only(decision)
        record = self._record(day, create=True)
        if not decision.trade:
            self.executor.finalize(record, NO_TRADE, inputs=inputs, decision=decision)
            log.info("decision %s: no trade (%s)", day, ",".join(decision.reason_keys))
            return
        self.executor.attach_decision(record, inputs, decision)
        log.info("decision %s: target %.4f (%s)", day, decision.total_new, ",".join(decision.reason_keys))
        self._after_chain(self.executor.run_chain(record, window), session)

    @staticmethod
    def _is_buy(record) -> bool:
        decision = record.data.get("decision") or {}
        try:
            held = float(decision["core_held"]) + float(decision["ovl_held"])
            return float(record.data["target"]["total_new"]) > held + 1e-9
        except (KeyError, TypeError, ValueError):
            return True                 # unknown: the cautious reading is that it buys

    def _continue_block(self, now, session, record):
        """Why a strategy chain that is already under way may not send a NEW order in this tick (or None), and
        the state fault to report afterwards. Its accepted order is still followed, cancelled and booked."""
        try:
            floor = self.rails.floor_state()
            paused = self.rails.paused()
            breaker = self._watch_breaker(read_equity(self.broker, now), session)   # measured again, not remembered
        except Exception as exc:        # whatever broke: nothing new is sent, the order already out is still followed
            return ("STATE_CORRUPT" if isinstance(exc, StateCorrupt) else "ENGINE_FAULT"), exc
        if floor == "HALT":
            return "FLOOR_HALT", None
        if floor == "CONFIRMING":
            return "RAIL_CONFIRMING", None
        if self.clock_ok is not True:
            return "CLOCK_DOUBT", None
        if not self.market_open:
            return "MARKET_CLOSED", None
        if not self.notifier.configured:
            return "NOTIFY_MISSING", None
        try:
            snapshot = self._snapshot()
        except SnapshotInvalid:
            return "SNAPSHOT_INVALID", None
        except BrokerError:
            return "BROKER_DOWN", None
        except Exception as exc:
            return ("STATE_CORRUPT" if isinstance(exc, StateCorrupt) else "ENGINE_FAULT"), exc
        if snapshot.foreign:
            symbol = _item_symbol(snapshot.foreign[0])
            self.notifier.alert("FOREIGN_ASSET", state_key=symbol, symbol=symbol)
            return "FOREIGN_ASSET", None
        if paused:
            return "PAUSED", None
        if breaker and self._is_buy(record):
            return "BREAKER", None
        return None, None

    def _floor_block(self, now, session):
        """The same question for a safety sale that is under way."""
        try:
            if self.rails.floor_state() != "HALT":
                return "HALT_CLEARED", None     # the owner lifted the halt: nothing more is sold
        except Exception as exc:
            return ("STATE_CORRUPT" if isinstance(exc, StateCorrupt) else "ENGINE_FAULT"), exc
        allowed, reason = floor_order_allowed(
            self.clock_ok is True, True, self.market_open, (session.close - now).total_seconds(), False, False,
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

    def _after_chain(self, record, session, late=False) -> None:
        """Messages that cannot wait for the evening: an order with an unknown outcome, a refused order, and a
        trade that was decided but never sent. `late`: the record was settled long after its session; "today I
        could not send" would then be wrong, and the dated summary of that session says it instead."""
        day = session.date.isoformat()
        if record.status == UNRESOLVED:
            self.notifier.alert("UNRESOLVED", state_key=day)
        elif (
            record.status == FAILED and record.data.get("status_detail") == "NOT_FOUND"
            and record.data.get("purpose") == "dec"
        ):
            self.notifier.alert("NOT_SENT", state_key=day, cause=self._not_sent_cause(record))
        elif record.status == FAILED:
            self.notifier.alert("ORDER_FAILED", state_key=day, product_pct=self._account_weight(record))
        elif record.status == NOT_SUBMITTED and record.data.get("purpose") == "dec" and not late:
            self.notifier.alert("NOT_SENT", state_key=day, cause=self._not_sent_cause(record))

    def _cutoff(self, day, state, quiet=False) -> None:
        """Nothing was decided in time: the record is closed and the owner is told, once per session. `quiet`:
        the dated summary that follows carries the news instead of a message that says "today"."""
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
        if self.rails.liquidating():
            self.rails.set_liquidating(False)
            self.notifier.alert("FLOOR_DONE", state_key="halt", pct=drawdown, equity=equity)

    def _floor_pending(self, day, drawdown, equity, held) -> None:
        self.notifier.alert(
            "FLOOR_PENDING", state_key=f"{day.isoformat()}:pending",
            pct=drawdown, equity=equity, product_pct=held,
        )

    def _floor_flow(self, now, session, window, reading) -> None:
        day = session.date
        try:
            snapshot = self._snapshot()
        except (SnapshotInvalid, BrokerError) as exc:
            # The sale cannot even be looked at. While it is still owed, the day's record says why, so that the
            # cutoff tells the owner instead of staying quiet.
            code = "SNAPSHOT_INVALID" if isinstance(exc, SnapshotInvalid) else "BROKER_DOWN"
            self._not_ready(day, code if self.rails.liquidating() else "FLOOR_HALT")
            return
        self._not_ready(day, "FLOOR_HALT")
        drawdown, equity, held = self._floor_numbers(reading, snapshot)
        if Decimal(snapshot.product_qty) <= 0:
            self._floor_done(drawdown, equity)
            return
        # The fund is held during a halt (also when it came back after an earlier complete sale): a sale is owed
        # again, and the owner hears about it like the first time.
        self.rails.set_liquidating(True)
        allowed, reason = floor_order_allowed(
            self.clock_ok is True, True, self.market_open, (session.close - now).total_seconds(),
            self._other_open(day, "flr"), bool(snapshot.foreign_product_orders),
        )
        if not allowed:
            log.warning("floor sale is waiting (%s)", reason)
            self._floor_pending(day, drawdown, equity, held)
            return
        record = self._record(day, "flr", create=True)
        if record.data.get("target") is None:
            self.executor.attach_floor(record)
        self.executor.run_chain(record, window)
        self._floor_report(now, session)

    def _floor_report(self, now, session) -> None:
        try:
            snapshot = self._snapshot()
        except (SnapshotInvalid, BrokerError):
            return
        reading = read_equity(self.broker, now)
        drawdown, equity, held = self._floor_numbers(reading, snapshot)
        if Decimal(snapshot.product_qty) <= 0:
            self._floor_done(drawdown, equity)
        else:
            self._floor_pending(session.date, drawdown, equity, held)

    def _settle_floor(self, reading) -> None:
        """After the close: a safety sale that finished while nobody was looking is reported as finished."""
        if self.rails.floor_state() != "HALT" or not self.rails.liquidating():
            return
        try:
            snapshot = self._snapshot()
        except (SnapshotInvalid, BrokerError):
            return
        if Decimal(snapshot.product_qty) <= 0:
            drawdown, equity, _ = self._floor_numbers(reading, snapshot)
            self._floor_done(drawdown, equity)

    # ------------------------------------------------------------------ after the close
    def _after_close(self, now, session, state, give_up=False) -> None:
        day = session.date
        marker = state.get("closed_session")
        if marker is not None and marker >= day.isoformat():
            return
        started = state.get("started_at")
        if started is not None and session.close <= datetime.fromisoformat(started):
            # This session ended before the engine ever ran: it is not ours to report.
            state["closed_session"] = day.isoformat()
            self._save_state(state)
            return
        window = window_for(session)
        late = now > window.reconcile_until + LATE_CLOSE
        if self.dry:
            # A dry-run record has no order at the broker: one that a dead process left open is simply closed
            # (housekeeping only settles records of real orders).
            for purpose in ("flr", "dec"):
                leftover = self._record(day, purpose)
                if _open(leftover):
                    self._after_chain(self.executor.reconcile(leftover, session), session)
        if any(_open(self._record(day, purpose)) for purpose in ("flr", "dec")) and now < window.reconcile_until:
            # Housekeeping is still asking the broker what became of the day's order: give it a few minutes
            # before a summary that would have to call the outcome unknown.
            self._digest_pending = True
            return
        self._cutoff(day, state, quiet=late)

        reading = read_equity(self.broker, now)
        if not reading.ok:
            self._digest_pending = True
            if give_up:
                log.error("no usable account reading after the close of %s; the digest is skipped", day)
                state["closed_session"] = day.isoformat()
                self._save_state(state)
                self._digest_pending = False
            return
        self._settle_floor(reading)

        # Which number is the closing value of this session? On time: the reading itself. Late, during the next
        # session: the broker's own `last_equity`. Late and before the next open: nothing is stored (the value
        # now may hold an overnight move); the row is taken from the broker once the next session is on.
        previous, following = self._previous_day(day), self._next_session(day)
        if not late:
            self._backfill_close(reading, session)
            close_equity = shown = reading.equity
        elif following is not None and following.open <= now < following.close and _positive(reading.last_equity):
            close_equity = shown = float(reading.last_equity)
        else:
            close_equity, shown = None, reading.equity
        base = None
        if close_equity is not None:
            rows = {str(row.get("session")): row.get("equity") for row in self.rails.state().get("equity_history", [])}
            if previous is not None and previous.isoformat() in rows:
                base = rows[previous.isoformat()]
            elif not late:
                base = reading.last_equity
            self.rails.record_close(day, close_equity)
        if self.rails.floor_state() != "HALT":
            drawdown = self.rails.check_alert(reading)
            if drawdown is not None:
                self.notifier.alert("DRAWDOWN_ALERT", state_key=day.isoformat(), pct=drawdown, equity=reading.equity)

        record, floor_record = self._record(day), self._record(day, "flr")
        decision = record.data.get("decision") if record is not None else None
        missing = bool(isinstance(decision, dict) and decision.get("overlay_data_missing"))
        state["overlay_missing_streak"] = int(state.get("overlay_missing_streak") or 0) + 1 if missing else 0

        self.notifier.digest(
            day,
            self._digest_context(
                now, day, reading, shown, base, record, floor_record,
                as_of_now=close_equity is None,
            ),
        )
        state["closed_session"] = day.isoformat()
        self._save_state(state)
        self._digest_pending = False
        log.info("session %s closed: equity %.2f%s", day, shown, " (reported late)" if late else "")

    def _digest_context(self, now, day, reading, equity, base, record, floor_record, as_of_now=False) -> dict:
        try:
            snapshot = self._snapshot()
            account = snapshot.account_equity or snapshot.equity
            product_weight = snapshot.product_weight * snapshot.equity / account
            owner_weight = snapshot.owner_value / account
            owner_label = ", ".join(_item_symbol(item) for item in snapshot.owner_positions)
            flat = Decimal(snapshot.product_qty) <= 0
        except (SnapshotInvalid, BrokerError, InvalidOperation):
            product_weight = min(1.0, max(0.0, reading.positions_value / reading.equity))
            owner_weight, owner_label, flat = 0.0, "", reading.positions_value <= 0

        bought = sold = 0.0
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
                if intent.get("side") == "buy":
                    bought += float(quantity) * float(price)
                elif intent.get("side") == "sell":
                    sold += float(quantity) * float(price)
        open_day = any(_open(item) for item in (record, floor_record))
        if open_day:
            action, action_usd = "unknown", 0.0         # an order went out and its outcome is not known yet
        elif sold > 0 and flat:
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
            outcome = "NOT_SENT" if record.data.get("status_detail") == "NOT_FOUND" else "REFUSED"
        elif floor_record is not None or self._hold_code(record) == "FLOOR_HALT":
            outcome = "FLOOR"
        decision = record.data.get("decision") if record is not None else None
        reason_keys = None
        if outcome is None and isinstance(decision, dict):
            reason_keys = list(decision.get("reason_keys") or [])
        if isinstance(reason_keys, list):
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

        if self.settings.mode == "paper":
            prefix = "paper"
        else:
            prefix = "dry_run" if self.dry else ""
        day_usd = equity - base if _positive(base) else None
        return {
            "date": day,
            "prefix": prefix,
            "equity": equity,
            "as_of_now": bool(as_of_now),
            "day_usd": day_usd,
            "day_pct": None if day_usd is None else day_usd / base,
            "product_weight": product_weight,
            "owner_weight": owner_weight,
            "owner_label": owner_label,
            "action": action,
            "action_usd": action_usd,
            "reason_keys": reason_keys,
            "outcome": outcome,
            "month_pct": None if not month_base else equity / month_base - 1.0,
            "total_pct": None if not total_base else equity / total_base - 1.0,
            "status_text": self._status_text(now, reading, record, floor_record, product_weight, open_day),
        }

    def _status_text(self, now, reading, record, floor_record, product_weight, open_day):
        """The sentence of the highest active state, or None when everything is normal."""
        document = self.rails.state()
        if document.get("floor_state") == "HALT":
            if document.get("liquidating"):
                return build_alert("RAIL_LIQUIDATING", product_pct=product_weight)
            peak = document.get("peak")
            drawdown = 0.0 if not peak else max(0.0, 1.0 - reading.equity / peak)
            return build_alert("FLOOR_DONE", pct=drawdown, equity=reading.equity)
        if open_day or self._stuck(now):
            return build_alert("UNRESOLVED")
        code = self._hold_code(record)
        if code == "FOREIGN_ASSET":
            return build_alert("FOREIGN_ASSET", symbol=_item_symbol(record.data["inputs"].get("detail") or ""))
        records = [item for item in (record, floor_record) if item is not None]
        not_found = next(
            (
                item for item in records
                if item.status == FAILED and item.data.get("status_detail") == "NOT_FOUND"
                and item.data.get("purpose") == "dec"
            ),
            None,
        )
        if not_found is not None:
            return build_alert("NOT_SENT", cause=self._not_sent_cause(not_found))
        if any(item.status == FAILED for item in records):
            return build_alert("ORDER_FAILED", product_pct=product_weight)
        for item in records:
            residual = item.data.get("residual") or 0.0
            if item.status == PARTIAL or (item.status == CAPPED and abs(residual) > 0.03):
                return build_alert("ORDER_PARTIAL", product_pct=product_weight)
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
