"""Test environment compatibility helpers.

Everything here is for Windows only: on any other system nothing is patched and
both hooks return at once.
"""

import os
import shutil
from pathlib import Path

import pytest


# The replacement basetemp this session switched to, if any (see pytest_configure).
_fallback_in_use = None


if os.name == "nt":
    # Python 3.12 gives mode 0o700 special ACL semantics on Windows. In a
    # managed workspace pytest's basetemp can then exclude its own restricted
    # process token. Keep the directory's normal inherited Windows ACL.
    _path_mkdir = Path.mkdir

    def _windows_inherited_acl_mkdir(self, mode=0o777, parents=False, exist_ok=False):
        if mode == 0o700:
            mode = 0o777
        return _path_mkdir(self, mode=mode, parents=parents, exist_ok=exist_ok)

    Path.mkdir = _windows_inherited_acl_mkdir


def _fallback_basetemp():
    # Only needed when the requested basetemp (normally .pytest_tmp) cannot be opened, so it
    # cannot live inside that directory: it sits next to it at the repository root, never
    # inside tests_v5/, and is removed again when the session ends.
    return Path(__file__).resolve().parent.parent / ".pytest_tmp_managed"


@pytest.hookimpl(trylast=True)
def pytest_configure(config):
    """Windows only: recover when an older 0o700 basetemp is already inaccessible."""

    global _fallback_in_use
    if os.name != "nt":
        return
    # Private pytest attributes are only reached through getattr: with another pytest
    # version this hook does nothing instead of breaking the session.
    factory = getattr(config, "_tmp_path_factory", None)
    given = getattr(factory, "_given_basetemp", None)
    if not isinstance(given, Path):
        return
    try:
        if not given.exists():
            return
        next(given.iterdir(), None)
    except PermissionError:
        fallback = _fallback_basetemp()
        try:
            setattr(factory, "_given_basetemp", fallback)
        except AttributeError:
            return
        _fallback_in_use = fallback
    except OSError:
        return


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session, exitstatus):
    """Windows only: remove the replacement basetemp if this session switched to it."""

    if os.name != "nt" or _fallback_in_use is None:
        return
    shutil.rmtree(_fallback_in_use, ignore_errors=True)
