"""Entry point for Claude Code hooks.

Installed is not active: until the project holds `.flotilla/project.toml`, every hook exits 0 with
no output. That path imports only the standard library and `flotilla.core.config`, because a
project that never onboarded flotilla must not pay for it. A hook never blocks a session start;
what it cannot check it says, in text the session will read.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def run_hook(event: str, stdin, out=sys.stdout) -> int:
    try:
        payload = json.load(stdin)
    except ValueError:
        payload = {}
    cwd = Path(payload.get("cwd") or ".") if isinstance(payload, dict) else Path(".")

    from flotilla.core.config import find_project
    root = find_project(cwd)
    if root is None:
        return 0

    if event == "session-start":
        try:
            from flotilla import doctor
            lines = doctor.render(doctor.collect(cwd=root), quiet=True)
        except Exception as err:  # noqa: BLE001 - a hook must say what broke, never crash the session
            print(f"flotilla: could not check this project: {err}", file=out)
            return 0
        if lines:
            print("flotilla: " + "; ".join(lines), file=out)
    return 0
