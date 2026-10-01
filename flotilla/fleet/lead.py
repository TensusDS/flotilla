"""The person's own session leading the fleet (decision 191, worldcore field test W9).

Only a person renames an interactive session, and `/rename` does not wake it, so asking for one read as a bug. A
UserPromptSubmit hook may set the session's title, and the census then lists the session under it (measured on
Claude Code 2.1.287). So `flotilla spawn --lead` records "this session leads, as <name>", and the hook gives the
session that name on the person's next message, once. Until then the session still carries its old name, and
`--fill` counts the recorded lead as the orchestrator's seat.
"""

from __future__ import annotations

KEY = "leads"


def record(store, session_id: str, name: str, *, now: str) -> None:
    store.append(KEY, {"session": session_id, "name": name, "at": now})


def pending(store) -> dict[str, str]:
    """Session id -> the name it is to carry, for every lead not yet given."""
    waiting: dict[str, str] = {}
    for item in store.read(KEY).records:
        session = item.get("session")
        if not session:
            continue
        if "applied" in item:
            waiting.pop(session, None)
        elif item.get("name"):
            waiting[session] = item["name"]
    return waiting


def take(store, session_id: str) -> str:
    """The name this session is to carry now, or ""; taken once."""
    if not session_id or session_id not in pending(store):   # the common path reads, and writes nothing
        return ""
    with store.transaction(KEY) as tx:
        name = pending(store).get(session_id, "")
        if name:
            tx.append({"session": session_id, "applied": name})
    return name
