"""What the fleet sees: whose move, the roster, stalled work, deviations, holds (spec, sections 6.7, 6.9).

Every view is computed from the folded rows plus facts passed in (the census, a receipt check); nothing here asks
the world or is stored, so a view is tested with rows alone. Liveness is passed, never assumed: without it no view
calls a session gone, because "the census could not be asked" read as "everyone is gone" would raise an alarm on
every row at once. Deviations need no threshold: each is a row from which the expected move cannot happen now.
"Has not moved for N hours" is the one timed view, and a person asks for it.
"""

from __future__ import annotations

import datetime as dt
import re

from flotilla.ledger.model import Row, blocked_by, delivered

SENDER, JUDGE = "the sender", "the judge"
POST_OF_MOVER = {SENDER: "sender", JUDGE: "judge"}   # a post-named mover and the post it stands for
READING = ("handed", "fixing")
WORKING = ("claimed", "fixing")
WAITING = ("handed", "accepted", "queued", "landed", "shipped", "walked")
BEFORE_QUEUE = ("claimed", "handed", "fixing", "accepted")


def pending_dependents(rows: dict[str, Row], row: Row, profile: dict) -> list[Row]:
    """Open rows that build on this one, directly or through other rows, and are not delivered: the path this row
    is part of is not whole yet. The link in between may have shipped while the row holding the entry point has
    not."""
    building, frontier = {}, [row.id]
    while frontier:
        below = frontier.pop()
        for other in rows.values():
            if below in (other.requires or []) and other.id not in building and other.id != row.id:
                building[other.id] = other
                frontier.append(other.id)
    return [other for other in building.values() if other.is_open and not delivered(other, profile)]


def who_moves(row: Row, profile: dict, rows: dict | None = None) -> str:
    """Who can move this row right now: a session name, a post in words, or '' (nobody named, or finished)."""
    if row.state in ("reserved", "claimed", "fixing", "walked"):
        return row.owner
    if row.state == "handed":
        return row.reader
    if row.state == "landed" and (profile.get("flow") or {}).get("mode") == "local":
        return row.owner   # without origin, landed is delivered: what remains is the owner's close
    if row.state in ("accepted", "queued", "landed"):
        if row.state == "queued" and (profile.get("pr") or {}).get("merged_by") == "human":
            return "the person who merges the PR"
        return SENDER
    if row.state == "shipped":
        if row.broken and rows is not None and not fix_arrived(rows, row, profile):
            return ""   # broken: the fix row's owner moves; the walk is the judge's once a fix is delivered
        if (profile.get("judge") or {}).get("required"):
            if rows is not None and not row.walkable and pending_dependents(rows, row, profile):
                return ""   # a part: the judge walks the path once the rows building on it ship
            return JUDGE
        return row.owner
    return ""


def waits_on(row: Row, rows: dict, profile: dict) -> str:
    """Why nobody is named for this row's move, in words a person can act on; "" when someone is named."""
    if who_moves(row, profile, rows):
        return ""
    if row.state == "shipped" and row.broken:
        fixes = [other for other in rows.values() if other.fixes == row.id and other.is_open
                 and fix_delivery(rows, other, profile) is None]   # an arrived fix is waited on by nobody
        if not fixes and fix_arrived(rows, row, profile):
            return ""
        if fixes:
            return ", ".join(f"the fix `{other.branch}` ({other.owner or 'no owner'})" for other in fixes)
        return "nobody: it broke and no fix row is open"
    return ""


def _moment(value: str) -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def moved_since(rows: dict[str, Row], row: Row) -> list[str]:
    """The rows a wait's note names that moved after the wait was recorded, as "r32 shipped" (H30).

    A note names a row by its id as a whole token (`r32`, never the `r3` inside `r33`) or by its branch as a whole
    word. A wait's note is free text, so this is a pointer for the person, never a move made for them."""
    note, since = row.note or "", _moment(row.updated_at)
    if not note or since is None:
        return []
    named = [f"r{number}" for number in re.findall(r"(?<![\w/-])r(\d+)(?![\w/-])", note)]
    named += [other.id for other in rows.values() if other.branch and
              re.search(rf"(?<![\w/-]){re.escape(other.branch)}(?![\w/-])", note)]
    found: list[str] = []
    for name in dict.fromkeys(named):
        other = rows.get(name)
        moment = _moment(other.updated_at) if other is not None else None
        if other is None or other.id == row.id or moment is None or moment <= since:
            continue
        found.append(f"{other.id} {other.state}")
    return found


def since_then(rows: dict[str, Row], row: Row) -> str:
    """" (since then: r32 shipped)" for a wait whose named rows moved on, else ""."""
    moved = moved_since(rows, row)
    return f" (since then: {', '.join(moved)})" if moved else ""


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
        return target.state not in ("reserved", "claimed", "handed", "fixing")   # lifted once that work is read
    if any(other.branch == until for other in rows.values()):
        return True
    if live is not None and until not in live:
        return None
    return not any(other.reader == until and other.state in READING and other.is_open for other in rows.values())


def fix_delivery(rows: dict[str, Row], fix: Row, profile: dict) -> Row | None:
    """The delivered row that carries a fix: the fix row itself, or the delivered row it was settled by (G9)."""
    if delivered(fix, profile):
        return fix
    if fix.state != "released" or not fix.history:
        return None
    wanted = (fix.history[-1].get("evidence") or {}).get("settled_by") or ""
    other = rows.get(wanted)
    return other if other is not None and delivered(other, profile) else None


def fix_arrived(rows: dict[str, Row], row: Row, profile: dict) -> bool:
    """Whether a fix for this broken row has been delivered, directly or by settlement."""
    return any(other.fixes == row.id and fix_delivery(rows, other, profile) for other in rows.values())


def handover_hint(row: Row, mover: str, rows: dict[str, Row], live: set[str], post_of, former_of=None) -> str:
    """Who can take a gone session's move, and the move that hands it over (twosuns field test of 0.6.7 and the
    person's proposal): the live sessions of the same post, least loaded first, and the adopt or assign line - the
    orchestrator chooses; flotilla only spares it the working out."""
    if not post_of:
        return ""
    post = (former_of or post_of)(mover)   # a former name (before the fleet's name) says only whose post it held
    if not post:
        return ""
    import shlex   # the orchestrator copies the line into a shell, and git allows `$(...)` in a branch name
    if mover == row.owner:
        def move(name: str) -> str:
            return "flotilla work adopt " + shlex.quote(row.branch) + " --to " + shlex.quote(name)
    elif mover == row.reader:
        def move(name: str) -> str:
            return "flotilla work assign " + shlex.quote(row.branch) + " --reader " + shlex.quote(name)
    else:
        return ""
    reading = mover == row.reader
    load: dict[str, int] = {}
    for other in rows.values():   # a reader's load is what it reads; an owner's, what it owns
        who = other.reader if reading else other.owner
        if other.is_open and who and not (other.state == "reserved"):
            load[who] = load.get(who, 0) + 1
    peers = sorted((name for name in live if name not in (mover, row.owner if reading else "")
                    and post_of(name) == post),   # strict: only this project's seats are offered (0.6.9, I4)
                   key=lambda name: (load.get(name, 0), name))
    if not peers:
        return f"; no live {post} session: raise one (`flotilla spawn --post {post}=1`), then `{move('<name>')}`"
    listed = ", ".join(f"{name} ({load.get(name, 0)} open)" for name in peers)
    return f"; live {post} sessions: {listed} - `{move(peers[0])}`"


def deviations(rows: dict[str, Row], profile: dict, live: set[str] | None = None, finished=None,
               post_of=None, former_of=None) -> list[dict]:
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
        if row.broken and not any(other.fixes == row.id and (other.is_open or fix_delivery(rows, other, profile))
                                  for other in rows.values()):
            add("broken_unfixed", row.owner, f"broke at {row.broken}, and no fix row is open")
        if finished is not None and row.state == "claimed" and finished(row):
            add("finished_not_handed", row.owner, "handover tiers are green over the branch tip, and it is not "
                                                  "handed: the author's move")
        mover = who_moves(row, profile, rows)
        if live is not None and mover and not mover.startswith("the ") and mover not in live:
            if row.state == "reserved":   # a post seat, not a move anyone owes (F8, F27)
                add("seat_empty", mover, f"the post seat is held for {mover}, and that session is not alive")
            else:
                add("mover_gone", mover, f"the move is {mover}'s, and that session is not alive"
                                         + handover_hint(row, mover, rows, live, post_of, former_of))
    return found


def approve_command(ledger, branch: str) -> str:
    """The person's approve move as they type it, shell-quoted, through the stable link to the running plugin when
    there is one: the orchestrator relays this line and never builds it from a branch name (scan of 0.6.10, F2)."""
    import shlex
    from flotilla.guards.githooks import link_path
    link = link_path(ledger.state_dir)
    cli = str(link) if link.is_symlink() or link.exists() else "flotilla"
    return f"{shlex.quote(cli)} work approve {shlex.quote(branch)}"
