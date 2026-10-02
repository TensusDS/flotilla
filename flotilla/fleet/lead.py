"""The person's own session leading the fleet (decision 191, worldcore field test W9).

Only a person renames an interactive session, and `/rename` does not wake it, so asking for one read as a bug. A
UserPromptSubmit hook may set the session's title, and the census then lists the session under it (measured on
Claude Code 2.1.287). So `flotilla spawn --lead` records "this session leads, as <name>", and the hook gives the
session that name on the person's next message - again on each one until the census lists it under that name. Until
then the session still carries its old name, and `--fill` counts the recorded lead as the orchestrator's seat.
"""

from __future__ import annotations

import datetime as dt

KEY = "leads"
#: A lead not shown within a day is forgotten: the session was closed before the person's next message, and a resume
#: days later must not rename it.
KEEP = dt.timedelta(days=1)
#: After this many messages that offered the title without the census showing it, the session asks the person for
#: `/rename`: the hook's title is measured on one Claude Code version only, and a version that ignored it would leave
#: the session unnamed with nobody told.
ASK_AFTER = 3


def record(store, session_id: str, name: str, *, now: str) -> None:
    store.append(KEY, {"session": session_id, "name": name, "at": now})


def _fresh(item: dict, now) -> bool:
    if now is None:
        return True
    try:
        at = dt.datetime.fromisoformat(str(item.get("at")))
    except ValueError:
        return True   # a record without a readable time is kept; forgetting needs a time to measure from
    if at.tzinfo is None:
        at = at.replace(tzinfo=dt.timezone.utc)
    return now - at <= KEEP


def pending(store, now=None) -> dict[str, str]:
    """Session id -> the name it is to carry, for every lead the census has not shown yet."""
    waiting: dict[str, str] = {}
    for item in store.read(KEY).records:
        session = item.get("session")
        if not session:
            continue
        if "applied" in item:
            waiting.pop(session, None)
        elif item.get("name") and _fresh(item, now):
            waiting[session] = item["name"]
    return waiting


def offered(store, session_id: str) -> int:
    """How many messages have offered this session its title since its newest lead was recorded."""
    count = 0
    for item in store.read(KEY).records:
        if item.get("session") != session_id:
            continue
        if "offered" in item:
            count += 1
        elif "applied" in item or item.get("name"):
            count = 0
    return count


def due(store, session_id: str, *, current: str, now=None) -> str:
    """The name to give this session now, or "". Given again on every prompt until the census lists the session
    under it - Claude Code says nothing back about a title it was handed (review of 0.6.0, I4) - and then closed."""
    name = pending(store, now).get(session_id, "") if session_id else ""
    if not name:
        return ""
    if name != current:
        store.append(KEY, {"session": session_id, "offered": name})
        return name
    with store.transaction(KEY) as tx:
        if pending(store, now).get(session_id) == name:
            tx.append({"session": session_id, "applied": name})
    return ""
