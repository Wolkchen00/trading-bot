"""Operator commands for persistent v5 rail state."""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import sys
import tempfile
import time
from dataclasses import fields
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from .broker import BrokerError
from .clock import ET, SessionCalendar, parse_calendar, window_for
from .config import load_settings
from .data import DecisionInputs, InputsNotReady, build_inputs
from .executor import Executor, SnapshotInvalid, plan_order, take_snapshot
from .health import assess, read_heartbeat
from .notify import Notifier, Outbox, default_post
from .packages import get_package
from .rails import Rails, read_equity, reduce_only
from .state import StateCorrupt, read_json
from .strategy import READY, decide


def _utcnow():
    return datetime.now(timezone.utc)


def _append_log(state_dir, now, command, arguments, result):
    path = Path(state_dir) / "ops_log.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "time": now.isoformat(),
        "command": command,
        "arguments": arguments,
        "result": result,
    }
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _run_change(state_dir, now_fn, command, arguments, operation, message):
    try:
        operation()
    except Exception as exc:
        _append_log(state_dir, now_fn(), command, arguments, "REFUSED:" + type(exc).__name__)
        raise
    _append_log(state_dir, now_fn(), command, arguments, "OK")
    print(message)
    return 0


def cmd_init_peak(
    state_dir,
    settings,
    value,
    source,
    force=False,
    now_fn=_utcnow,
    sleep_fn=time.sleep,
):
    rails = Rails(settings, state_dir, now_fn, sleep_fn)
    arguments = {"value": value, "source": source, "force": bool(force)}
    return _run_change(
        state_dir,
        now_fn,
        "init-peak",
        arguments,
        lambda: rails.init_peak(value, source, force=force),
        f"Peak initialised at {float(value)} from {source}.",
    )


def cmd_pause(state_dir, settings, by, note, now_fn=_utcnow, sleep_fn=time.sleep):
    rails = Rails(settings, state_dir, now_fn, sleep_fn)
    arguments = {"by": by, "note": note}
    return _run_change(
        state_dir,
        now_fn,
        "pause",
        arguments,
        lambda: rails.pause(by, note),
        f"Paused by {by}.",
    )


def cmd_resume(state_dir, settings, by, note, now_fn=_utcnow, sleep_fn=time.sleep):
    rails = Rails(settings, state_dir, now_fn, sleep_fn)
    arguments = {"by": by, "note": note}
    return _run_change(
        state_dir,
        now_fn,
        "resume",
        arguments,
        lambda: rails.resume(by, note),
        f"Resumed by {by}.",
    )


def cmd_clear_halt(
    state_dir,
    settings,
    by,
    note,
    new_peak,
    now_fn=_utcnow,
    sleep_fn=time.sleep,
):
    rails = Rails(settings, state_dir, now_fn, sleep_fn)
    arguments = {"by": by, "note": note, "new_peak": new_peak}
    return _run_change(
        state_dir,
        now_fn,
        "clear-halt",
        arguments,
        lambda: rails.clear_halt(by, note, new_peak),
        f"Halt cleared by {by}; peak set to {float(new_peak)}.",
    )


def cmd_status(state_dir, settings, now_fn=_utcnow, sleep_fn=time.sleep):
    rails = Rails(settings, state_dir, now_fn, sleep_fn)
    heartbeat = read_heartbeat(state_dir)
    level, lines = assess(heartbeat, now_fn())
    print(
        json.dumps(
            {
                "rails": rails.state(),
                "health": {"level": level, "lines": lines, "heartbeat": heartbeat},
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


def _decision_dict(decision) -> dict:
    """Every field of a Decision in the form JSON gives back (tuples become lists)."""
    raw = {field.name: getattr(decision, field.name) for field in fields(decision)}
    return json.loads(json.dumps(raw))


def _calendar_for(settings, broker, today):
    """The cached calendar when it covers today, otherwise a fresh one. Nothing is written."""
    try:
        cached = read_json(Path(settings.state_dir) / "calendar.json", default=None)
        if isinstance(cached, dict):
            calendar = SessionCalendar.from_json(cached["sessions"])
            if calendar.covers(today):
                return calendar
    except (StateCorrupt, KeyError, TypeError, ValueError):
        pass
    rows = broker.get_calendar(today - timedelta(days=400), today + timedelta(days=16))
    return SessionCalendar(parse_calendar(rows))


def _plan_dict(snapshot, decision, params):
    """The order the executor would plan for this decision, or None when the decision trades nothing."""
    if decision.status != READY or not decision.trade:
        return None
    planned = plan_order(snapshot, decision.total_new, params)
    return {"kind": planned.kind, "side": planned.side, "qty": planned.qty,
            "notional": planned.notional, "capped": planned.capped}


def _same(left, right) -> bool:
    """Strict equality of two JSON values: true is not 1, and 1 is not 1.0."""
    return json.dumps(left, sort_keys=True) == json.dumps(right, sort_keys=True)


def _blockers(settings, broker, now, session, snapshot, executor, reading):
    """Everything that would stop the running engine from placing a new strategy order at this moment, in the
    engine's own order, and whether today's breaker leaves it sells only. Read-only."""
    found, breaker = [], False
    try:
        document = Rails(settings, settings.state_dir, lambda: now, lambda seconds: None).state()
    except StateCorrupt:
        document = None
        found.append("STATE_CORRUPT")
    if document is not None and document.get("peak") is None:
        found.append("PEAK_MISSING")
    if not reading.ok:
        known = ("BROKER_DOWN", "ACCOUNT_STATUS", "BLOCKED")
        found.append(reading.problem if reading.problem in known else "EQUITY_DOUBT")
    if document is not None:
        peak, floor = document.get("peak"), document.get("floor_state")
        if floor == "HALT":
            found.append("FLOOR_HALT")
        elif floor == "CONFIRMING" or (
            reading.ok and peak and reading.equity <= (1.0 - settings.rails.floor_dd) * peak
        ):
            found.append("RAIL_CONFIRMING")
        stored = document.get("breaker")
        breaker = bool(stored and stored.get("session") == session.date.isoformat())
    baseline = reading.last_equity
    if (
        reading.ok and isinstance(baseline, (int, float)) and not isinstance(baseline, bool)
        and math.isfinite(baseline) and baseline > 0
    ):
        breaker = breaker or reading.equity <= (1.0 - settings.rails.breaker_day) * baseline
    try:
        clock = broker.get_clock()
        if abs((clock.timestamp - now).total_seconds()) > 60.0:
            found.append("CLOCK_DOUBT")
        elif not clock.is_open:
            found.append("MARKET_CLOSED")
    except BrokerError:
        found.append("CLOCK_DOUBT")
    if not settings.ntfy_topic:
        found.append("NOTIFY_MISSING")
    if executor.open_records():
        found.append("UNRESOLVED")
    if snapshot.foreign:
        found.append("FOREIGN_ASSET")
    if document is not None and document.get("paused"):
        found.append("PAUSED")
    return found, breaker


def cmd_decide_now(settings, broker, now, out_path=None):
    """What the engine would do at this moment. Read-only: no order, nothing written to the state.

    Exit code 0 only when the rule is READY and nothing would stop the engine (`blockers` is empty)."""
    params = get_package(settings.package)
    document = {"time": now.isoformat(), "mode": settings.mode, "package": settings.package}
    code = 1
    try:
        calendar = _calendar_for(settings, broker, now.astimezone(ET).date())
        session = calendar.get(now.astimezone(ET).date())
        if session is None or not session.open <= now < session.close:
            document["error"] = "NO_SESSION_NOW"
        else:
            window = window_for(session)
            executor = Executor(broker, settings, settings.state_dir, params, lambda: now, lambda seconds: None)
            snapshot = take_snapshot(
                broker, params, executor.own_client_ids(), tuple(getattr(settings, "owner_symbols", ()) or ())
            )
            fraction = executor.load_overlay_fraction()
            reading = read_equity(broker, now)
            blockers, breaker = _blockers(settings, broker, now, session, snapshot, executor, reading)
            document["snapshot"] = {
                "equity": snapshot.equity, "account_equity": snapshot.account_equity,
                "owner_value": snapshot.owner_value, "cash": snapshot.cash, "spendable": snapshot.spendable,
                "product_qty": snapshot.product_qty, "product_weight": snapshot.product_weight,
                "foreign": list(snapshot.foreign),
            }
            document["overlay_fraction"] = fraction
            document["blockers"] = blockers
            document["breaker"] = breaker
            document["in_window"] = window.decide_from <= now <= window.decide_until
            inputs = build_inputs(broker, calendar, session, now, params)
            if isinstance(inputs, InputsNotReady):
                document["inputs"] = {"code": inputs.code, "detail": inputs.detail}
                document["decision"], document["plan"] = None, None
            else:
                decision = decide(
                    inputs.closes, inputs.px, inputs.session_high, inputs.session_low,
                    snapshot.product_weight, fraction, params,
                )
                if breaker:
                    decision = reduce_only(decision)
                document["inputs"] = inputs.to_bundle()
                document["decision"] = _decision_dict(decision)
                document["plan"] = _plan_dict(snapshot, decision, params)
                if decision.status == READY and not blockers:
                    code = 0
    except (BrokerError, SnapshotInvalid, StateCorrupt, ValueError) as exc:
        document["error"] = type(exc).__name__
    text = json.dumps(document, ensure_ascii=False, sort_keys=True, indent=1, allow_nan=False)
    if out_path is not None:
        Path(out_path).write_text(text + "\n", encoding="utf-8")
    print(text)
    return code, json.loads(text)


def cmd_replay(document):
    """Recompute a stored decision and its order plan from the document's own inputs: 0 when both come out
    identical, 1 otherwise."""
    try:
        params = get_package(document["package"])
        inputs = DecisionInputs.from_bundle(document["inputs"])
        stored = document["snapshot"]
        decision = decide(
            inputs.closes, inputs.px, inputs.session_high, inputs.session_low,
            stored["product_weight"], document["overlay_fraction"], params,
        )
        if document.get("breaker") is True:
            decision = reduce_only(decision)
        view = SimpleNamespace(
            equity=stored["equity"], cash=stored["cash"], spendable=stored.get("spendable"),
            product_weight=stored["product_weight"], product_qty=stored["product_qty"],
        )
        plan = _plan_dict(view, decision, params)
    except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
        print(f"replay impossible: {type(exc).__name__}")
        return 1, None
    recomputed = _decision_dict(decision)
    same = _same(recomputed, document.get("decision")) and _same(plan, document.get("plan"))
    print(json.dumps(
        {"identical": same, "decision": recomputed, "plan": plan}, ensure_ascii=False, sort_keys=True, indent=1,
    ))
    return (0 if same else 1), recomputed


def cmd_notify_test(settings, post_fn=default_post, now_fn=_utcnow):
    """One test message to this process's own topic, from a throw-away outbox (the bot's outbox is not touched).
    0 when the message was delivered."""
    folder = tempfile.mkdtemp(prefix="v5-notify-test-")
    try:
        outbox = Outbox(folder)
        notifier = Notifier(settings, outbox, post_fn, now_fn)
        if not notifier.configured:
            print("no notification topic is configured")
            return 1
        notifier.alert("TEST")
        delivered = not outbox.pending(now_fn() + timedelta(hours=3))
        print("delivered" if delivered else "the post was refused: nothing reached the phone")
        return 0 if delivered else 1
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def _add_decide_now(parser):
    parser.add_argument("--out")


def _run_decide_now(namespace, settings):
    from .broker import AlpacaBroker

    return cmd_decide_now(settings, AlpacaBroker(settings), _utcnow(), namespace.out)[0]


def _add_replay(parser):
    parser.add_argument("file")


def _run_replay(namespace, settings):
    with open(namespace.file, encoding="utf-8") as handle:
        return cmd_replay(json.load(handle))[0]


def _add_init_peak(parser):
    parser.add_argument("value", type=float)
    parser.add_argument("--source", required=True)
    parser.add_argument("--force", action="store_true")


def _add_actor(parser):
    parser.add_argument("--by", required=True)
    parser.add_argument("--note", required=True)


def _add_clear_halt(parser):
    _add_actor(parser)
    parser.add_argument("--new-peak", required=True, type=float)


def _add_status(parser):
    return None


def _run_notify_test(namespace, settings):
    return cmd_notify_test(settings)


def _run_init_peak(namespace, settings):
    return cmd_init_peak(
        settings.state_dir, settings, namespace.value, namespace.source, namespace.force
    )


def _run_pause(namespace, settings):
    return cmd_pause(settings.state_dir, settings, namespace.by, namespace.note)


def _run_resume(namespace, settings):
    return cmd_resume(settings.state_dir, settings, namespace.by, namespace.note)


def _run_clear_halt(namespace, settings):
    return cmd_clear_halt(
        settings.state_dir, settings, namespace.by, namespace.note, namespace.new_peak
    )


def _run_status(namespace, settings):
    return cmd_status(settings.state_dir, settings)


COMMANDS = {
    "init-peak": (_add_init_peak, _run_init_peak),
    "pause": (_add_actor, _run_pause),
    "resume": (_add_actor, _run_resume),
    "clear-halt": (_add_clear_halt, _run_clear_halt),
    "status": (_add_status, _run_status),
    "decide-now": (_add_decide_now, _run_decide_now),
    "replay": (_add_replay, _run_replay),
    "notify-test": (_add_status, _run_notify_test),
}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Operate v5 trading state.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name, (add_arguments, _) in COMMANDS.items():
        command_parser = subparsers.add_parser(name)
        add_arguments(command_parser)
    namespace = parser.parse_args(argv)
    try:
        settings = load_settings()
        return COMMANDS[namespace.command][1](namespace, settings)
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
