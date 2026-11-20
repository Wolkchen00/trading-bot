"""Heartbeat persistence and operator health assessment."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from .config import load_settings
from .state import StateCorrupt, atomic_write_json, read_json


CRITICAL_STATES = {"FLOOR_HALT", "RAIL_LIQUIDATING", "UNRESOLVED", "NOT_STARTED"}


def write_heartbeat(state_dir, payload):
    atomic_write_json(Path(state_dir) / "heartbeat.json", payload)


def read_heartbeat(state_dir) -> dict | None:
    try:
        heartbeat = read_json(Path(state_dir) / "heartbeat.json", default=None)
    except StateCorrupt:
        return None
    return heartbeat if isinstance(heartbeat, dict) else None


def _parse_time(value):
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _age(heartbeat, now) -> float | None:
    if not isinstance(heartbeat, dict) or "time" not in heartbeat:
        return None
    try:
        then = _parse_time(heartbeat["time"])
        current = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
        return (current - then).total_seconds()
    except (TypeError, ValueError, OverflowError):
        return None


def assess(heartbeat, now):
    if not isinstance(heartbeat, dict):
        return 2, ["Heartbeat is missing or unreadable."]
    age = _age(heartbeat, now)
    if age is None:
        return 2, ["Heartbeat time is missing or unreadable."]

    lines = []
    level = 0
    if age > 300:
        level = 2
        lines.append(f"Heartbeat is stale ({int(age)} seconds old).")

    raw_states = heartbeat.get("states", [])
    states = raw_states if isinstance(raw_states, list) else ["UNKNOWN_STATE"]
    for state in states:
        if state in CRITICAL_STATES:
            level = 2
            lines.append(f"Blocked state is active: {state}.")
        else:
            level = max(level, 1)
            lines.append(f"Degraded state is active: {state}.")

    readiness = heartbeat.get("readiness", {})
    problems = readiness.get("problems", []) if isinstance(readiness, dict) else ["unreadable"]
    readiness_bad = not isinstance(readiness, dict) or readiness.get("ok") is False or bool(problems)
    if readiness_bad:
        level = max(level, 1)
        detail = ", ".join(str(item) for item in problems) or "not ready"
        lines.append("Readiness has problems: " + detail + ".")
    if heartbeat.get("ntfy_configured") is False:
        level = max(level, 1)
        lines.append("Owner notifications are not configured.")
    oldest = heartbeat.get("outbox_oldest_minutes")
    if isinstance(oldest, (int, float)) and not isinstance(oldest, bool) and oldest > 60:
        level = max(level, 1)
        lines.append(f"An owner message has been undelivered for {int(oldest)} minutes.")
    if not lines:
        lines.append("Healthy.")
    return level, lines


def main(argv=None):
    parser = argparse.ArgumentParser(description="Assess the v5 trading heartbeat.")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)
    try:
        settings = load_settings()
    except Exception as exc:
        print(type(exc).__name__)
        return 2
    heartbeat = read_heartbeat(settings.state_dir)
    now = datetime.now(timezone.utc)
    level, lines = assess(heartbeat, now)
    if args.as_json:
        print(
            json.dumps(
                {
                    "level": level,
                    "heartbeat_age_seconds": _age(heartbeat, now),
                    "lines": lines,
                    "heartbeat": heartbeat,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
    else:
        print(("healthy", "degraded", "blocked")[level])
        for line in lines:
            print(line)
    return level


if __name__ == "__main__":
    raise SystemExit(main())
