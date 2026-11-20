"""Plain Turkish, state-only owner status for v5."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from v5.config import load_settings
from v5.health import read_heartbeat
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
    lines = ["Bot çalışıyor." if fresh else "Bot çalışmıyor görünüyor."]
    if age is None:
        lines.append("Kalp atışı kaydı yok.")
    elif age < -60:
        lines.append("Kalp atışı kaydının saati ileride görünüyor.")
    else:
        lines.append(f"Son kalp atışı: {max(0, int(age // 60))} dakika önce.")
    if age is None:
        lines.append("Durum: bilinmiyor.")
    else:
        states = heartbeat.get("states", [])
        if states:
            lines.append("Durum: " + ", ".join(str(state) for state in states))
        else:
            lines.append("Durum: Her şey normal.")
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
