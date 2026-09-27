"""When the prompt hook speaks: at once when what it would say changed, otherwise at most every 30 minutes.

Ages are printed but are not part of "changed", or a line would be new every minute. With nothing to say the stamp
is removed, so the next thing said is said at once. One stamp per session, in the state directory.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path

QUIET = dt.timedelta(minutes=30)


def digest(parts) -> str:
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def _stamp(state_dir, session_id: str) -> Path:
    name = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:32]
    return Path(state_dir) / "watch" / "said" / f"{name}.json"


def due(state_dir, session_id: str, said: str, now: dt.datetime) -> bool:
    path = _stamp(state_dir, session_id)
    if not said:
        path.unlink(missing_ok=True)
        return False
    try:
        last = json.loads(path.read_text(encoding="utf-8"))
        at, same = dt.datetime.fromisoformat(last["at"]), last["digest"] == said
    except (OSError, ValueError, KeyError, TypeError):
        at, same = None, False
    if same and at is not None and now - at < QUIET:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"at": now.isoformat(), "digest": said}), encoding="utf-8")
    os.replace(tmp, path)
    return True
