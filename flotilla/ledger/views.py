"""What the fleet sees: whose move, the roster, stalled work, deviations, holds (spec, sections 6.7, 6.9).

Every view is computed from the folded rows plus facts passed in (the census, a receipt check); nothing here asks
the world or is stored, so a view is tested with rows alone. Liveness is passed, never assumed: without it no view
calls a session gone, because "the census could not be asked" read as "everyone is gone" would raise an alarm on
every row at once. Deviations need no threshold: each is a row from which the expected move cannot happen now.
"Has not moved for N hours" is the one timed view, and a person asks for it.
"""

from __future__ import annotations

import datetime as dt

from flotilla.ledger.model import Row, blocked_by, delivered

SENDER, JUDGE = "the sender", "the judge"
READING = ("handed", "fixing")
WORKING = ("claimed", "fixing")
WAITING = ("handed", "accepted", "queued", "landed", "shipped", "walked")
BEFORE_QUEUE = ("claimed", "handed", "fixing", "accepted")


def who_moves(row: Row, profile: dict) -> str:
    """Who can move this row right now: a session name, a post in words, or '' (nobody named, or finished)."""
    if row.state in ("reserved", "claimed", "fixing", "walked"):
        return row.owner
    if row.state == "handed":
        return row.reader
    if row.state in ("accepted", "queued", "landed"):
        if row.state == "queued" and (profile.get("pr") or {}).get("merged_by") == "human":
            return "the person who merges the PR"
        return SENDER
    if row.state == "shipped":
        return JUDGE if (profile.get("judge") or {}).get("required") else row.owner
    return ""


def roster(rows: dict[str, Row], profile: dict, live: set[str] | None = None) -> list[dict]:
    open_rows = [row for row in rows.values() if row.is_open]
    names = {row.owner for row in open_rows} | {row.reader for row in open_rows if row.state in READING}
    entries = []
    for name in sorted(names - {""}):
        mine = [row for row in open_rows if row.owner == name]
        reading = [row for row in open_rows if row.reader == name and row.state == "handed"]
        blocked = [other for row in mine if row.state in BEFORE_QUEUE for other in blocked_by(rows, row, profile)]
        if live is not None and name not in live:
            state = "orphaned"
        elif blocked:
            state = "blocked"
        elif any(row.state in WORKING for row in mine):
            state = "working"
        elif reading:
            state = "reading"
        elif any(row.state in WAITING for row in mine):
            state = "waiting"
        else:
            state = "idle"
        entries.append({"who": name, "state": state, "rows": mine, "reading": reading,
                        "blocked_on": [other.branch or other.id for other in blocked]})
    return entries


def _moment(value: str) -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def stalled(rows: dict[str, Row], hours: float, now: dt.datetime) -> list[Row]:
    late = []
    for row in rows.values():
        moved = _moment(row.updated_at)
        if not row.is_open or row.state == "reserved" or moved is None:
            continue
        if (now - moved).total_seconds() > hours * 3600:
            late.append(row)
    return sorted(late, key=lambda row: row.updated_at)


def hold_lifted(row: Row, rows: dict[str, Row], live: set[str] | None = None) -> bool | None:
    """Whether a hold's condition is met, asked of the ledger; None when it cannot be asked."""
    until = row.held_until
    if not until:
        return None
    target = next((other for other in reversed(list(rows.values())) if other.branch == until and other.is_open), None)
    if target is not None:
        return target.state not in READING
    if any(other.branch == until for other in rows.values()):
        return True
    if live is not None and until not in live:
        return None
    return not any(other.reader == until and other.state in READING and other.is_open for other in rows.values())


def deviations(rows: dict[str, Row], profile: dict, live: set[str] | None = None, finished=None) -> list[dict]:
    found = []
    for row in rows.values():
        if not row.is_open:
            continue

        def add(kind: str, on: str, why: str) -> None:
            found.append({"kind": kind, "branch": row.branch, "state": row.state, "on": on, "why": why})

        if row.state == "handed" and not row.reader and not row.held_until:
            add("nobody_named", "", "handed, and no reader is named: it can wait forever")
        if row.reader and row.reader == row.owner:
            add("reader_is_owner", row.reader, "the author cannot read their own work")
        if row.held_until:
            lifted = hold_lifted(row, rows, live)
            if lifted is True:
                add("hold_lifted", row.held_by, f"the hold's condition ({row.held_until}) is met; unhold it")
            elif lifted is None:
                add("hold_unknown", row.held_by, f"the hold waits on `{row.held_until}`, which cannot be asked: "
                                                 "gone, or unknown to the ledger")
        if row.broken and not any(other.fixes == row.id and (other.is_open or delivered(other, profile))
                                  for other in rows.values()):
            add("broken_unfixed", row.owner, f"broke at {row.broken}, and no fix row is open")
        if finished is not None and row.state == "claimed" and finished(row):
            add("finished_not_handed", row.owner, "handover tiers are green over the branch tip, and it is not "
                                                  "handed: the author's move")
        mover = who_moves(row, profile)
        if live is not None and mover and not mover.startswith("the ") and mover not in live:
            add("mover_gone", mover, f"the move is {mover}'s, and that session is not alive")
    return found
