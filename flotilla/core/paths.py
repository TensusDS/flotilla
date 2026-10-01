"""Where durable state lives on this machine.

Never `${CLAUDE_PLUGIN_DATA}`: Claude Code deletes that directory when the plugin is uninstalled
(the CLI by default), and the fleet's history would go with it.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path


def state_dir(env: Mapping[str, str] = os.environ) -> Path:
    override = env.get("FLOTILLA_STATE_DIR")
    if override:
        return Path(override)
    base = env.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "flotilla"


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
