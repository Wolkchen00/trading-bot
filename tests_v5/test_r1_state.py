import json
import os
import subprocess
import sys
import time

import pytest

from v5 import state
from v5.state import (
    LockBusy,
    ProcessLock,
    StateCorrupt,
    StateDirError,
    atomic_write_json,
    ensure_state_dir,
    read_json,
    session_record_path,
)


def test_atomic_write_and_simulated_crash(tmp_path, monkeypatch):
    path = tmp_path / "nested" / "state.json"
    atomic_write_json(path, {"old": 1})
    monkeypatch.setattr(os, "replace", lambda source, target: (_ for _ in ()).throw(OSError("crash")))
    with pytest.raises(OSError):
        atomic_write_json(path, {"new": 2})
    assert json.loads(path.read_text(encoding="utf-8")) == {"old": 1}
    assert list(path.parent.glob("*.tmp")) == []


def test_read_json_valid_missing_and_corrupt(tmp_path):
    path = tmp_path / "state.json"
    assert read_json(path, {"default": True}) == {"default": True}
    with pytest.raises(FileNotFoundError):
        read_json(path)
    path.write_text('{"ok": 1}', encoding="utf-8")
    assert read_json(path) == {"ok": 1}
    path.write_text("broken", encoding="utf-8")
    with pytest.raises(StateCorrupt) as caught:
        read_json(path)
    assert str(path) in str(caught.value) and path.read_text(encoding="utf-8") == "broken"


def test_non_finite_numbers_are_refused_both_ways(tmp_path):
    path = tmp_path / "rails.json"
    atomic_write_json(path, {"peak": 491.08})
    for bad in ({"peak": float("nan")}, {"peak": float("inf")}, [1, {"deep": float("-inf")}]):
        with pytest.raises(ValueError):
            atomic_write_json(path, bad)
        assert read_json(path) == {"peak": 491.08}
    assert [item.name for item in tmp_path.iterdir()] == ["rails.json"]

    for text in ('{"peak": NaN}', '{"peak": Infinity}', "[-Infinity]", '{"peak": 1e999}', "[-1e999]"):
        path.write_text(text, encoding="utf-8")
        with pytest.raises(StateCorrupt):
            read_json(path, default=None)
        assert path.read_text(encoding="utf-8") == text
    path.write_text('{"count": 3, "low": -0.5, "peak": 1e308}', encoding="utf-8")
    assert read_json(path) == {"count": 3, "low": -0.5, "peak": 1e308}


def test_parent_directory_is_synced_only_after_a_finished_replace(tmp_path, monkeypatch):
    events = []
    real_replace = os.replace

    def recording_replace(source, target):
        real_replace(source, target)
        events.append("replace")

    path = tmp_path / "nested" / "state.json"
    monkeypatch.setattr(os, "replace", recording_replace)
    monkeypatch.setattr(state, "_fsync_directory", lambda directory: events.append(("sync", directory)))
    atomic_write_json(path, {"v": 1})
    assert events == ["replace", ("sync", tmp_path / "nested")]

    events.clear()
    monkeypatch.setattr(os, "replace", lambda source, target: (_ for _ in ()).throw(OSError("crash")))
    with pytest.raises(OSError):
        atomic_write_json(path, {"v": 2})
    assert events == [] and read_json(path) == {"v": 1}


def test_directory_sync_is_posix_only_and_best_effort(tmp_path, monkeypatch):
    events = []

    def refuse(*args, **kwargs):
        raise OSError("refused")

    with monkeypatch.context() as patch:
        patch.setattr(state, "_POSIX", True)
        patch.setattr(os, "open", lambda path, flags: events.append(("open", path)) or 77)
        patch.setattr(os, "fsync", lambda fd: events.append(("fsync", fd)))
        patch.setattr(os, "close", lambda fd: events.append(("close", fd)))
        state._fsync_directory(tmp_path)
        patch.setattr(os, "fsync", refuse)
        state._fsync_directory(tmp_path)  # a refused sync is ignored and the handle is still closed
        patch.setattr(os, "open", refuse)
        state._fsync_directory(tmp_path)  # a directory that cannot be opened is ignored
        patch.setattr(state, "_POSIX", False)
        state._fsync_directory(tmp_path)  # elsewhere nothing is attempted
    assert events == [("open", tmp_path), ("fsync", 77), ("close", 77), ("open", tmp_path), ("close", 77)]


def test_lock_context_manager_never_runs_its_body_unlocked(tmp_path):
    assert issubclass(LockBusy, RuntimeError)
    path = tmp_path / "process.lock"
    with ProcessLock(path) as held:
        assert held.acquired
        entered = False
        with pytest.raises(LockBusy) as caught:
            with ProcessLock(path):
                entered = True
        assert entered is False and str(path) in str(caught.value)
    assert not held.acquired
    with ProcessLock(path) as again:
        assert again.acquired


def test_ensure_state_dir_and_failure(tmp_path, monkeypatch):
    target = tmp_path / "state"
    ensure_state_dir(target)
    assert target.is_dir()
    monkeypatch.setattr("v5.state.tempfile.mkstemp", lambda **kwargs: (_ for _ in ()).throw(PermissionError("no")))
    with pytest.raises(StateDirError) as caught:
        ensure_state_dir(tmp_path / "unwritable")
    assert "unwritable" in str(caught.value)


def test_process_lock_excludes_process_and_releases_on_death(tmp_path):
    path = tmp_path / "process.lock"
    code = (
        "import sys,time; from v5.state import ProcessLock; "
        "lock=ProcessLock(sys.argv[1]); print(lock.acquire(), flush=True); time.sleep(30)"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", code, str(path)],
        cwd=os.getcwd(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        assert process.stdout.readline().strip() == "True"
        assert not ProcessLock(path).acquire()
    finally:
        process.terminate()
        process.wait(timeout=10)
    for _ in range(50):
        lock = ProcessLock(path)
        if lock.acquire():
            break
        time.sleep(0.02)
    else:
        pytest.fail("lock was not released when child process died")
    lock.release()


def test_session_record_paths(tmp_path):
    from datetime import date

    assert session_record_path(tmp_path, date(2026, 11, 16)) == tmp_path / "decisions" / "2026-11-16.json"
    assert session_record_path(tmp_path, date(2026, 11, 16), True) == tmp_path / "decisions_dryrun" / "2026-11-16.json"
