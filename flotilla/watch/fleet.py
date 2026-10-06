"""What the orchestrator is told about the fleet (spec, sections 6.9 layer 3 and 8).

Four kinds of item, none on a clock: a deviation (a move that cannot happen now), a dropped ball (the move is a
live session's, it recorded no wait, and the census says it is not working), a move named for a post no live
session holds, and a break (a session stopped twice while holding a move, and the row has not moved since). A
session in a seat's tree that holds no post is named as not a fleet session and counted nowhere (H7).
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from flotilla.core.storage import LocalLogStore
from flotilla.fleet import strangers
from flotilla.ledger import views
from flotilla.ledger.model import now_iso
from flotilla.watch.whose import POST_OF_MOVER, Item, holds_move, since_of

DEVIATION, DROPPED, NOBODY, BREAK, QUESTION, PERSON = ("deviation", "dropped", "nobody", "break", "question",
                                                     "person")
IDLE, DONE, HELPER, STRANGER, PILE = "idle", "done", "helper", "stranger", "pile"
IDLE_SEAT_MINUTES = 60
PILE_MOVES = 3   # this many moves of one seat nobody waits on, while a peer of its post holds none
SEATS = "seats"
SEATS_HINT = ("`flotilla spawn --fill` raises them again (dry run first, and the person's yes); `flotilla fleet` lists "
              "them, `flotilla fleet down` stands the fleet down")
THE_PERSON = "the person"


def not_working(session) -> bool:
    """What the census says, read without guessing: background sessions report `state`, interactive ones `status`."""
    if session.kind == "background":
        return session.state in ("blocked", "done")
    return session.status == "idle"


def _census_word(session) -> str:
    if session.kind == "background":
        if session.status == "waiting":
            return "waiting on a permission prompt nobody answers"
        if session.status == "idle":
            return "idle"
        if session.state == "blocked":
            # a dialog that waits on a person reads `waiting` above, and a question in flotilla's queue keeps the
            # ball from being called dropped at all; so with a status reported, blocked is idle (twosuns, 0.7.16).
            # With no status the census cannot tell the two apart (review of 0.7.17)
            if not session.status:
                return "blocked: idle, or waiting on a permission prompt"
            return "idle (no permission question of it waits in flotilla's queue)"
    return session.state or session.status or "unknown"


def movers(row, profile: dict, live: set[str], post_of, rows: dict | None = None) -> set[str]:
    mover = views.who_moves(row, profile, rows)
    if mover in POST_OF_MOVER:
        return {name for name in live if post_of(name) == POST_OF_MOVER[mover]}
    if mover and not mover.startswith("the "):
        return {mover}
    return set()


def fleet(rows: dict, profile: dict, sessions, *, post_of, former_of=None, breaks=(), asking=(), claimers=frozenset(),
          now: dt.datetime | None = None, census: set[str] | None = None) -> list[Item]:
    outside = strangers.in_seat_trees(sessions, rows, post_of)   # in a seat's tree, holding no post (H7)
    by_name = {session.name: session for session in sessions if session.name}
    for session, _ in outside:
        by_name.pop(session.name, None)   # counted nowhere a seat is counted
    live = set(by_name)
    everyone = census if census is not None else live   # waits are asked of the whole census, not the project
    found_all = views.deviations(rows, profile, live, post_of=post_of, former_of=former_of, census=everyone)
    items = [Item(DEVIATION, found["branch"], f"{found['kind']}: {found['why']}", since_of(rows, found["branch"]),
                  who=found["kind"])
             for found in found_all if found["kind"] != "seat_empty"]
    empty = sorted(found["on"] for found in found_all if found["kind"] == "seat_empty")
    if empty:   # one quiet line for every seat whose session is gone, not an alarm per seat (F27)
        items.append(Item(SEATS, "", f"{len(empty)} post seat(s) with no live session: {', '.join(empty)}; "
                                     + SEATS_HINT,
                          "", who=",".join(empty)))   # the ledger knows when a seat was reserved, not when it emptied
    for row in rows.values():
        if row.is_open and row.waiting_on.strip().lower() == THE_PERSON:
            items.append(Item(PERSON, row.branch, f"waits on the person: {row.note or 'no question recorded'}"
                                                  f"{views.since_then(rows, row)}", row.updated_at, who=row.note))
        if not row.is_open or row.state == "reserved" or _waits(row, rows, everyone) or row.held_until:
            continue   # a post row is the post held, not a move anyone owes
        mover = views.who_moves(row, profile, rows)
        named = movers(row, profile, live, post_of, rows) & live
        if mover in POST_OF_MOVER and not named:
            items.append(Item(NOBODY, row.branch, f"the move is {mover}'s, and no live session holds the "
                                                  f"`{POST_OF_MOVER[mover]}` post", row.updated_at, who=mover))
        for name in sorted(named):
            if name in asking:
                continue   # its question is in the queue: the move waits on the person, not on the session
            if not_working(by_name[name]):
                items.append(Item(DROPPED, row.branch, f"{name} holds the move ({row.state}) and is not working "
                                                       f"(census: {_census_word(by_name[name])}); message them",
                                  row.updated_at, who=name))
    for name in sorted(live):
        session = by_name[name]
        dropped = any(item.kind == DROPPED and item.who == name for item in items)
        if session.status == "waiting" and name not in asking and post_of(name) and not dropped \
                and session.kind != "interactive":   # the person is in front of it: waiting on them is normal (W17)
            # a question open in its own session that no row records (H42); one in the queue is a QUESTION item,
            # and a holder of a move already reads as DROPPED with its prompt named
            items.append(Item(PERSON, "", f"{name} waits on the person (census: waiting); answer it in its session",
                              "", who=name))
    items.extend(Item(STRANGER, "", f"{session.name}: {strangers.label(tree)}", "", who=session.name)
                 for session, tree in outside if not strangers.started_by(session))   # a hook's review: no alarm (W6)
    items.extend(breaks)
    items.extend(idle_seats(rows, profile, live, post_of, claimers, now))
    items.extend(piled(rows, profile, live, post_of, claimers, everyone))
    items.extend(drained(rows, live))
    items.extend(helpers(rows, live))
    return items


def idle_seats(rows: dict, profile: dict, live: set[str], post_of, claimers, now: dt.datetime | None) -> list[Item]:
    """A live implementer holding nothing but its seat, for longer than the threshold: memory and hands spent on
    nothing, and nobody is told (H45)."""
    if not claimers:
        return []
    minutes = (profile.get("watch") or {}).get("idle_seat_minutes", IDLE_SEAT_MINUTES)
    now = now or dt.datetime.now(dt.timezone.utc)
    items = []
    for name in sorted(live):
        if post_of(name) not in claimers:
            continue
        mine = [row for row in rows.values() if row.owner == name]
        busy = any(row.is_open and row.state != "reserved" for row in mine) or \
            any(row.is_open and row.state == "handed" and row.reader == name for row in rows.values())
        last = max((moment for moment in (_moment(row.updated_at) for row in mine) if moment), default=None)
        if busy or last is None or (now - last).total_seconds() < minutes * 60:
            continue
        hours = int((now - last).total_seconds() // 3600)
        age = f"{hours} h" if hours else f"{int((now - last).total_seconds() // 60)} min"
        items.append(Item(IDLE, "", f"{name} has held no work for {age}: give it a row, or retire it", "",
                          who=name))
    return items


def _waits(row, rows: dict, live: set[str] | None = None) -> bool:
    """A recorded wait that still has something to wait on; one whose object is gone silences nothing."""
    return bool(row.waiting_on) and not views.wait_over(row, rows, live)


def piled(rows: dict, profile: dict, live: set[str], post_of, claimers, census: set[str] | None = None) -> list[Item]:
    """Work piling up on one seat while a live seat of the same post has none: the fleet runs at that one seat's
    speed (twosuns, 2026-10-03: three rows in fixing on one main session, another with nothing to move). Only work
    in its author's hands counts - claimed, or returned for fixes - and nobody's work is taken from a live session
    (`adopt` refuses), so this only names the pile to the orchestrator."""
    own: dict[str, list] = {}
    for row in rows.values():
        if not row.is_open or row.state not in views.WORKING or row.held_until or _waits(row, rows, census):
            continue
        mover = views.who_moves(row, profile, rows)
        if mover == row.owner and mover in live and post_of(mover) in claimers:
            own.setdefault(mover, []).append(row)
    items = []
    for name in sorted(own):
        if len(own[name]) < PILE_MOVES:
            continue
        free = sorted(peer for peer in live if peer != name and post_of(peer) == post_of(name) and not own.get(peer))
        if not free:
            continue
        branches = ", ".join(row.branch for row in own[name])
        claimed = sum(row.state == "claimed" for row in own[name])
        states = ", ".join(part for part in (f"{claimed} claimed" if claimed else "",
                                             f"{len(own[name]) - claimed} returned for fixes"
                                             if len(own[name]) > claimed else "") if part)
        items.append(Item(PILE, "", f"{name} holds {len(own[name])} rows in work ({states}: {branches}) while "
                                    f"{', '.join(free)} hold{'s' if len(free) == 1 else ''} none: ask {name} to hand "
                                    f"over a claimed row it has not started, or give {free[0]} the next row", "",
                          who=name))
    return items


def drained(rows: dict, live: set[str]) -> list[Item]:
    """No open work left: the task is done unless the person says otherwise, and the fleet still holds memory (H12)."""
    seats = [row for row in rows.values() if row.is_open and row.state == "reserved" and row.owner in live]
    since = min((_moment(row.history[0].get("at", "")) if row.history else _moment(row.updated_at) for row in seats),
                default=None, key=lambda moment: moment or dt.datetime.max.replace(tzinfo=dt.timezone.utc))
    work = [row for row in rows.values()
            if row.state != "reserved" and not (row.history and row.history[0].get("move") == "reserve")]
    if any(row.is_open for row in work):
        return []
    done_here = [row for row in work if since is None or ((_moment(row.updated_at) or since) >= since)]
    if not live or not done_here:
        return []   # nothing done by this fleet yet is a fleet starting, not a task finished
    return [Item(DONE, "", f"the queue is empty: ask the person to check the result, then offer `flotilla fleet down` "
                           f"({len(live)} session(s) stay alive until then)", "", who="done")]


def question_items(questions) -> list[Item]:
    from flotilla.broker.present import summary
    from flotilla.core.text import visible
    return [Item(QUESTION, visible(asked.session), f"asks {summary(asked)}; answer with /flotilla:permit",
                 dt.datetime.fromtimestamp(asked.at, dt.timezone.utc).isoformat(timespec="seconds"), who=str(asked.at))
            for asked in questions]


def _store(state_dir) -> LocalLogStore:
    return LocalLogStore(Path(state_dir) / "watch" / "breaks")


def _moment(value: str) -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def record_break(state_dir, repo_key: str, name: str, branches, at: str | None = None) -> None:
    _store(state_dir).append(repo_key, {"at": at or now_iso(), "name": name, "branches": sorted(branches)})


def open_breaks(state_dir, repo_key: str, rows: dict, profile: dict, post_of) -> list[Item]:
    latest: dict[tuple[str, str], str] = {}
    for record in _store(state_dir).read(repo_key).records:
        for branch in record.get("branches") or []:
            latest[(record.get("name", ""), branch)] = record.get("at", "")
    items = []
    for (name, branch), at in sorted(latest.items()):
        row = next((r for r in reversed(list(rows.values())) if r.branch == branch and r.is_open), None)
        stopped, moved = _moment(at), _moment(row.updated_at) if row is not None else None
        if row is None or stopped is None or moved is None or moved > stopped:
            continue
        if row.waiting_on or row.held_until or not holds_move(row, profile, name, post_of(name), rows):
            continue
        items.append(Item(BREAK, branch, f"{name} stopped twice while holding this move ({row.state}), and "
                                         "nothing has moved since", at, who=f"{name} {at}"))
    return items


def helpers(rows: dict, live: set[str]) -> list[Item]:
    """A helper that finished but still runs is to be retired; a helper whose parent has no live owner is an orphan
    (field test twosuns, H8)."""
    items = []
    for name in sorted(live):
        seats = [row for row in rows.values() if row.owner == name and row.helper_of]
        if not seats:
            continue
        seat = seats[-1]
        parent = rows.get(seat.helper_of)
        helped = f"`{parent.branch}`" if parent is not None else seat.helper_of
        if not seat.is_open:
            items.append(Item(HELPER, seat.branch, f"{name} finished helping {helped}: merge {seat.branch} "
                                                   f"into it, then `flotilla retire \"{name}\"`",
                              seat.updated_at, who=f"{name} done"))
        elif parent is None or not parent.is_open or parent.owner not in live:
            items.append(Item(HELPER, seat.branch, f"{name} helps {helped}, which has no live owner: adopt the work "
                                                   f"or retire the helper (`flotilla retire \"{name}\"`)",
                              seat.updated_at, who=f"{name} orphan"))
    return items
