"""A Bash tool call in an onboarded project, noted by the guard hook so a listing it runs records nothing as shown.

`caller.person_refusal` passes a model in the person's own interactive session. What tells the two apart is the
door: a Bash tool call fires the PreToolUse hook, a command the person types with `!` does not (measured on Claude
Code 2.1.287, as for `approve` in `guards/person.py`). The guard leaves a note keyed by the session for every Bash
call, whatever its words: a note left only when the word `inbox` showed let `in''box` or a variable run the listing
unnoted (security review of eadba0b). The listing that runs under that session within the window uses the note up
and records nothing. The person's own `!` run within the window of the model's last Bash call is not recorded
either: an over-reported "never shown", the safe way.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

WINDOW = 30.0   # seconds between the hook and the command it let run


def _path(state_dir, session_id: str) -> Path:
    name = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:32]
    return Path(state_dir) / "watch" / "toolcall" / name


def note(state_dir, session_id: str, at: float) -> None:
    if not session_id:
        return
    path = _path(state_dir, session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(repr(at), encoding="utf-8")


def by_tool(state_dir, session_ids, now: float, window: float = WINDOW) -> bool:
    """Whether a fresh note stands for one of these sessions; a note found is used up."""
    found = False
    for session_id in session_ids:
        if not session_id:
            continue
        path = _path(state_dir, session_id)
        try:
            at = float(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        path.unlink(missing_ok=True)
        found = found or 0 <= now - at <= window
    return found
