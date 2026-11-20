import json
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from tools.v5_durum import render
from v5 import health
from v5.health import assess, read_heartbeat, write_heartbeat
from v5.notify import Outbox
from v5.ops import cmd_clear_halt, cmd_init_peak, cmd_pause, cmd_resume, cmd_status
from v5.rails import EquityReading, Rails


NOW = datetime(2026, 11, 16, 21, tzinfo=timezone.utc)


def settings(state_dir):
    return SimpleNamespace(
        state_dir=state_dir,
        rails=SimpleNamespace(alert_dd=0.37, breaker_day=0.15, floor_dd=0.60),
    )


def heartbeat(**changes):
    result = {
        "time": NOW.isoformat(), "mode": "paper", "phase": "idle", "session": "2026-11-16",
        "readiness": {"ok": True, "problems": []}, "states": [],
        "last_decision": {"session": "2026-11-16", "status": "READY"},
        "outbox_oldest_minutes": None, "ntfy_configured": True,
        "orders_enabled": True, "package": "high",
    }
    result.update(changes)
    return result


def test_assess_all_levels_and_boundaries():
    assert assess(None, NOW)[0] == 2
    assert assess({}, NOW)[0] == 2
    assert assess(heartbeat(time="bad"), NOW)[0] == 2
    assert assess(heartbeat(time=(NOW - timedelta(seconds=300)).isoformat()), NOW)[0] == 0
    assert assess(heartbeat(time=(NOW - timedelta(seconds=301)).isoformat()), NOW)[0] == 2
    for state in ("FLOOR_HALT", "RAIL_LIQUIDATING", "UNRESOLVED", "NOT_STARTED"):
        assert assess(heartbeat(states=[state]), NOW)[0] == 2
    assert assess(heartbeat(states=["PAUSED"]), NOW)[0] == 1
    assert assess(heartbeat(readiness={"ok": False, "problems": ["PRICE_BAD"]}), NOW)[0] == 1
    assert assess(heartbeat(readiness={"ok": False, "problems": []}), NOW)[0] == 1
    assert assess(heartbeat(ntfy_configured=False), NOW)[0] == 1
    assert assess(heartbeat(outbox_oldest_minutes=61), NOW)[0] == 1
    assert assess(heartbeat(outbox_oldest_minutes=60), NOW)[0] == 0


def test_heartbeat_round_trip_and_corrupt_is_missing(tmp_path):
    payload = heartbeat()
    write_heartbeat(tmp_path, payload)
    assert read_heartbeat(tmp_path) == payload
    (tmp_path / "heartbeat.json").write_text("bad", encoding="utf-8")
    assert read_heartbeat(tmp_path) is None


def test_health_json_output(monkeypatch, tmp_path, capsys):
    write_heartbeat(tmp_path, heartbeat(time=datetime.now(timezone.utc).isoformat()))
    monkeypatch.setattr(health, "load_settings", lambda: settings(tmp_path))
    assert health.main(["--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert set(result) == {"level", "heartbeat_age_seconds", "lines", "heartbeat", "owner_unreachable"}


def rail_reading(equity, when=NOW):
    return EquityReading(when, equity, 100, equity, 0, True, None)


def test_all_ops_commands_and_log(tmp_path, capsys):
    cfg = settings(tmp_path)
    now = lambda: NOW
    assert cmd_init_peak(tmp_path, cfg, 100, "v4", now_fn=now, sleep_fn=lambda _: None) == 0
    assert cmd_pause(tmp_path, cfg, "owner", "wait", now_fn=now, sleep_fn=lambda _: None) == 0
    assert cmd_resume(tmp_path, cfg, "owner", "go", now_fn=now, sleep_fn=lambda _: None) == 0
    rails = Rails(cfg, tmp_path, now, lambda _: None)
    rails.check_floor(rail_reading(40))
    rails.check_floor(rail_reading(40, NOW + timedelta(seconds=60)))
    assert cmd_clear_halt(tmp_path, cfg, "owner", "safe", 40, now_fn=now, sleep_fn=lambda _: None) == 0
    assert rails.floor_state() == "OK" and rails.peak() == 40
    output = capsys.readouterr().out
    assert "Peak initialised" in output and "Halt cleared" in output
    rows = [json.loads(line) for line in (tmp_path / "ops_log.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [row["command"] for row in rows] == ["init-peak", "pause", "resume", "clear-halt"]
    assert all(row["result"] == "OK" for row in rows)
    assert cmd_status(tmp_path, cfg, now_fn=now, sleep_fn=lambda _: None) == 0
    status = json.loads(capsys.readouterr().out)
    assert set(status) == {"rails", "health"}
    with pytest.raises(ValueError):
        cmd_init_peak(tmp_path, cfg, 50, "twice", now_fn=now, sleep_fn=lambda _: None)


def test_owner_status_fresh_stale_missing_and_digest(tmp_path):
    missing = "\n".join([
        "Bot çalışmıyor görünüyor.",
        "Kalp atışı kaydı yok.",
        "Durum: HEARTBEAT_MISSING",
        "Henüz günlük özet yok.",
    ])
    assert render(tmp_path, NOW) == missing
    write_heartbeat(tmp_path, heartbeat(states=["PAUSED"]))
    fresh = "\n".join([
        "Bot çalışıyor.",
        "Son kalp atışı: 0 dakika önce.",
        "Durum: PAUSED",
        "Henüz günlük özet yok.",
    ])
    assert render(tmp_path, NOW) == fresh
    Outbox(tmp_path).add("DIGEST", "özet satır 1\nözet satır 2", NOW, dedupe_key="digest")
    stale = render(tmp_path, NOW + timedelta(minutes=6))
    assert stale == "\n".join([
        "Bot çalışmıyor görünüyor.",
        "Son kalp atışı: 6 dakika önce.",
        "Durum: PAUSED, HEARTBEAT_STALE",
        "Son günlük özet:",
        "özet satır 1",
        "özet satır 2",
    ])
