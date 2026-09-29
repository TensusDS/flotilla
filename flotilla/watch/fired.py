"""When each hook last ran for a session (field test F11).

The guards and the Stop guard print nothing when they let a command through, so nothing in a session shows that
they run. One small file per session in the state directory records the last time each hook fired; a failure to
write it is ignored, because a hook must never fail for bookkeeping.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

SAFE = re.compile(r"[^A-Za-z0-9_-]")


def _path(state_dir: Path, session_id: str) -> Path:
    return Path(state_dir) / "hooks" / f"{SAFE.sub('_', session_id) or 'unknown'}.json"


def read(state_dir: Path, session_id: str) -> dict[str, str]:
    try:
        data = json.loads(_path(state_dir, session_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    events = data.get("events") if isinstance(data, dict) else None
    return {k: v for k, v in (events or {}).items() if isinstance(k, str) and isinstance(v, str)}


def record(state_dir: Path, session_id: str, event: str, *, root: Path, at: str) -> None:
    try:
        path = _path(state_dir, session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        events = read(state_dir, session_id)
        events[event] = at
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"root": str(root), "events": events}), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        return
