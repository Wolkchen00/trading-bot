"""Durable JSON state and single-process locking."""

from __future__ import annotations

import json
import math
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class StateCorrupt(RuntimeError):
    def __init__(self, path: str | os.PathLike[str]):
        self.path = Path(path)
        super().__init__(f"state file is unreadable or corrupt: {self.path}")


class StateDirError(RuntimeError):
    pass


class LockBusy(RuntimeError):
    """Raised when a `with ProcessLock(...)` block cannot take the lock."""


_MISSING = object()
_POSIX = os.name == "posix"


def _fsync_directory(directory: Path) -> None:
    """Best effort: make a finished rename survive a power loss (POSIX only)."""
    if not _POSIX:
        return
    try:
        fd = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


def _replace(source: str, target: Path) -> None:
    """os.replace; on Windows a brief refusal (a scanner holding the file) is retried for about half a second."""
    if os.name != "nt":
        os.replace(source, target)
        return
    for attempt in range(20):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if attempt == 19:
                raise
            time.sleep(0.025)


def atomic_write_json(path: str | os.PathLike[str], obj: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_name = handle.name
            json.dump(
                obj,
                handle,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        _replace(temp_name, target)
        temp_name = None
    finally:
        if temp_name is not None:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass
    _fsync_directory(target.parent)


def _reject_constant(name: str):
    raise ValueError(f"non-finite number in state file: {name}")


def _finite_float(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):
        raise ValueError("non-finite number in state file")
    return value


def read_json(path: str | os.PathLike[str], default: Any = _MISSING) -> Any:
    target = Path(path)
    try:
        with target.open(encoding="utf-8") as handle:
            return json.load(handle, parse_constant=_reject_constant, parse_float=_finite_float)
    except FileNotFoundError:
        if default is _MISSING:
            raise
        return default
    except (OSError, ValueError, RecursionError) as exc:
        # ValueError covers invalid JSON, invalid UTF-8 and the non-finite numbers refused above.
        raise StateCorrupt(target) from exc


def ensure_state_dir(path: str | os.PathLike[str]) -> None:
    target = Path(path)
    probe: Path | None = None
    try:
        target.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=".write-probe-", dir=target)
        probe = Path(name)
        with os.fdopen(fd, "wb") as handle:
            handle.write(b"ok")
            handle.flush()
            os.fsync(handle.fileno())
        probe.unlink()
        probe = None
    except Exception as exc:
        if probe is not None:
            try:
                probe.unlink()
            except OSError:
                pass
        raise StateDirError(f"state directory is not writable: {target}") from exc


class ProcessLock:
    """A non-blocking advisory lock whose file contents are diagnostic only."""

    def __init__(self, path: str | os.PathLike[str]):
        self.path = Path(path)
        self._file = None
        self.acquired = False

    def acquire(self) -> bool:
        if self.acquired:
            return True
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.path, "a+b")
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0, os.SEEK_END)
                if handle.tell() == 0:
                    handle.write(b"\0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError):
            handle.close()
            return False

        self._file = handle
        self.acquired = True
        diagnostic = (
            f"pid={os.getpid()}\n"
            f"started={datetime.now(timezone.utc).isoformat()}\n"
        ).encode("ascii")
        handle.seek(0)
        handle.truncate()
        handle.write(diagnostic)
        handle.flush()
        os.fsync(handle.fileno())
        return True

    def release(self) -> None:
        if not self.acquired or self._file is None:
            return
        handle, self._file = self._file, None
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            self.acquired = False
            handle.close()

    def __enter__(self) -> "ProcessLock":
        # The body of a `with` block must never run unlocked.
        if not self.acquire():
            raise LockBusy(f"process lock is held elsewhere: {self.path}")
        return self

    def __bool__(self) -> bool:
        return self.acquired

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.release()


def session_record_path(
    state_dir: str | os.PathLike[str], session_date, dry_run: bool = False
) -> Path:
    folder = "decisions_dryrun" if dry_run else "decisions"
    date_text = session_date.isoformat() if hasattr(session_date, "isoformat") else str(session_date)
    return Path(state_dir) / folder / f"{date_text}.json"
