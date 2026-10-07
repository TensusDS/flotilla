"""The person's files are refused to Claude's edit tools (rig design, section 4).

`machine.toml` carries the person's switch and ceilings, the rig's state directory the journal, the machine key and
the reaper's launcher, and the provider key the person's money. A shell command that writes them is not seen here -
the limit the README's security model names; this closes the edit tools, the honest path.
"""

from __future__ import annotations

import os
from pathlib import Path

PATH_FIELDS = ("file_path", "notebook_path", "path")


def _real(path) -> str:
    return os.path.realpath(os.path.expanduser(str(path)))


def _keys(env) -> str:
    return _real(Path(env.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")) / "flotilla" / "rig")


def protected(path: str, *, state: Path, env) -> bool:
    target = _real(path)
    inside = [_real(state / "rig"), _keys(env)]
    return target == _real(state / "machine.toml") or any(target == top or target.startswith(top + os.sep)
                                                          for top in inside)


def check(tool_input: dict, *, state: Path, env) -> str:
    for field in PATH_FIELDS:
        value = tool_input.get(field)
        if isinstance(value, str) and value and protected(value, state=state, env=env):
            return (f"flotilla: {value} holds the person's word on rented machines (the switch, ceilings, key or "
                    "journal); a Claude tool call does not edit it. Show the person what to change; `flotilla rig "
                    "enable`/`disable` are their commands.")
    return ""
