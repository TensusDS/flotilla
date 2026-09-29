"""When each hook last ran for a session (field test F11).

The guards and the Stop guard print nothing when they let a command through, so nothing in a session shows that
they run. A directory per session in the state directory holds one file per hook with the last time it fired; a
failure to write it is ignored, because a hook must never fail for bookkeeping.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

SAFE = re.compile(r"[^A-Za-z0-9_-]")


def _dir(state_dir: Path, session_id: str) -> Path:
    return Path(state_dir) / "hooks" / (SAFE.sub("_", session_id) or "unknown")


def read(state_dir: Path, session_id: str) -> dict[str, str]:
    folder = _dir(state_dir, session_id)
    try:
        names = [path for path in folder.iterdir() if path.suffix == ".at"]
    except OSError:
        return {}
    events = {}
    for path in names:
        try:
            events[path.stem] = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
    return events


def record(state_dir: Path, session_id: str, event: str, *, root: Path, at: str) -> None:
    """One file per session and event, replaced whole: two hooks of one session firing at once never read and
    rewrite each other's record."""
    try:
        folder = _dir(state_dir, session_id)
        folder.mkdir(parents=True, exist_ok=True)
        name = SAFE.sub("_", event) or "unknown"
        tmp = folder / f".{name}.{os.getpid()}.tmp"
        tmp.write_text(at + "\n", encoding="utf-8")
        tmp.replace(folder / f"{name}.at")
    except OSError:
        return
