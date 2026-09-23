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
