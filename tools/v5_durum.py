"""Plain Turkish, state-only owner status for v5."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from v5.config import load_settings
from v5.health import assess, read_heartbeat
from v5.notify import Outbox


def _heartbeat_age(heartbeat, now):
    if not isinstance(heartbeat, dict):
        return None
    try:
        then = datetime.fromisoformat(str(heartbeat["time"]).replace("Z", "+00:00"))
        if then.tzinfo is None:
            then = then.replace(tzinfo=timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        return (now - then).total_seconds()
    except (KeyError, TypeError, ValueError, OverflowError):
        return None


def render(state_dir, now) -> str:
    heartbeat = read_heartbeat(state_dir)
    age = _heartbeat_age(heartbeat, now)
    fresh = age is not None and -60 <= age <= 300
    level, _ = assess(heartbeat, now)
    normal = level == 0
    lines = ["Bot çalışıyor." if fresh else "Bot çalışmıyor görünüyor."]
    if age is None:
        lines.append("Kalp atışı kaydı yok.")
    elif age < -60:
        lines.append("Kalp atışı kaydının saati ileride görünüyor.")
    else:
        lines.append(f"Son kalp atışı: {max(0, int(age // 60))} dakika önce.")
    if normal:
        lines.append("Durum: Her şey normal.")
    else:
        codes = []
        if isinstance(heartbeat, dict):
            states = heartbeat.get("states", [])
            if isinstance(states, list):
                codes.extend(str(state) for state in states)
            readiness = heartbeat.get("readiness", {})
            problems = readiness.get("problems", []) if isinstance(readiness, dict) else []
            if isinstance(problems, list):
                codes.extend(str(problem) for problem in problems)
            if heartbeat.get("ntfy_configured") is False:
                codes.append("NOTIFY_MISSING")
            oldest = heartbeat.get("outbox_oldest_minutes")
            if isinstance(oldest, (int, float)) and not isinstance(oldest, bool) and oldest > 60:
                codes.append("OWNER_MESSAGE_UNDELIVERED")
        if age is None:
            codes.append("HEARTBEAT_MISSING")
        elif age < -60:
            codes.append("HEARTBEAT_FUTURE")
        elif age > 300:
            codes.append("HEARTBEAT_STALE")
        lines.append("Durum: " + ", ".join(dict.fromkeys(codes or ["UNKNOWN"])))
    digest = Outbox(state_dir).last("DIGEST")
    if digest is None:
        lines.append("Henüz günlük özet yok.")
    else:
        lines.append("Son günlük özet:")
        lines.extend(digest["text"].splitlines())
    return "\n".join(lines)


def main():
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if callable(reconfigure):
        reconfigure(encoding="utf-8")
    settings = load_settings()
    print(render(settings.state_dir, datetime.now(timezone.utc)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
