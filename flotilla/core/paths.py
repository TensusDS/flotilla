"""Where durable state lives on this machine.

Never `${CLAUDE_PLUGIN_DATA}`: Claude Code deletes that directory when the plugin is uninstalled
(the CLI by default), and the fleet's history would go with it.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path


class RealStateInTest(RuntimeError):
    """A test asked for the state directory without naming one of its own."""


def state_dir(env: Mapping[str, str] = os.environ) -> Path:
    override = env.get("FLOTILLA_STATE_DIR")
    if override:
        return Path(override)
    base = env.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    real = Path(base) / "flotilla"
    if env.get("PYTEST_CURRENT_TEST") and "_pytest" in sys.modules:
        # pytest sets this for every test whether or not tests/conftest.py loaded; a test file outside tests/ never
        # gets the per-test directory, and one such run rewrote the person's machine.toml (alpha feedback item 21).
        # Only a test in this very process: a child inherits the marker, and a hook or git hook started from another
        # project's test run is not a test of flotilla (review of 0.29.3).
        raise RealStateInTest(f"running under a pytest test (PYTEST_CURRENT_TEST is set) with FLOTILLA_STATE_DIR "
                              f"unset, which would be the real state directory {real}; set FLOTILLA_STATE_DIR to a "
                              f"temporary directory")
    return real


def ensure_private(path: Path) -> None:
    """The state directory holds command text, tool inputs and the ledger: it is its user's alone (0700). Closing the
    root closes everything under it, whatever each file's own mode (security review F25, F16)."""
    import stat
    try:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        if stat.S_IMODE(path.stat().st_mode) & 0o077:
            path.chmod(0o700)
    except OSError:
        pass   # a state directory that cannot be made says so where it is first written
