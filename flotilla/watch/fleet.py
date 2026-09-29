"""What the orchestrator is told about the fleet (spec, sections 6.9 layer 3 and 8).

Four kinds of item, none on a clock: a deviation (a move that cannot happen now), a dropped ball (the move is a
live session's, it recorded no wait, and the census says it is not working), a move named for a post no live
session holds, and a break (a session stopped twice while holding a move, and the row has not moved since).
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from flotilla.core.storage import LocalLogStore
from flotilla.ledger import views
from flotilla.ledger.model import now_iso
from flotilla.watch.whose import POST_OF_MOVER, Item, holds_move, since_of

DEVIATION, DROPPED, NOBODY, BREAK, QUESTION, PERSON = ("deviation", "dropped", "nobody", "break", "question",
                                                     "person")
SEATS = "seats"
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
            return "blocked: idle, or waiting on a permission prompt"
    return session.state or session.status or "unknown"


def movers(row, profile: dict, live: set[str], post_of, rows: dict | None = None) -> set[str]:
    mover = views.who_moves(row, profile, rows)
    if mover in POST_OF_MOVER:
        return {name for name in live if post_of(name) == POST_OF_MOVER[mover]}
    if mover and not mover.startswith("the "):
        return {mover}
    return set()


def fleet(rows: dict, profile: dict, sessions, *, post_of, breaks=(), asking=()) -> list[Item]:
    by_name = {session.name: session for session in sessions if session.name}
    live = set(by_name)
    found_all = views.deviations(rows, profile, live)
    items = [Item(DEVIATION, found["branch"], f"{found['kind']}: {found['why']}", since_of(rows, found["branch"]),
                  who=found["kind"])
             for found in found_all if found["kind"] != "seat_empty"]
    empty = sorted(found["on"] for found in found_all if found["kind"] == "seat_empty")
    if empty:   # one quiet line for every seat whose session is gone, not an alarm per seat (F27)
        items.append(Item(SEATS, "", f"{len(empty)} post seat(s) with no live session: {', '.join(empty)}; "
                                     "`flotilla fleet` lists them, `flotilla fleet down` stands the fleet down",
                          min((row.updated_at for row in rows.values() if row.owner in empty
                               and row.state == "reserved"), default=""), who=",".join(empty)))
    for row in rows.values():
        if row.is_open and row.waiting_on.strip().lower() == THE_PERSON:
            items.append(Item(PERSON, row.branch, f"waits on the person: {row.note or 'no question recorded'}",
                              row.updated_at, who=row.note))
        if not row.is_open or row.state == "reserved" or row.waiting_on or row.held_until:
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
    items.extend(breaks)
    return items


def question_items(questions) -> list[Item]:
    from flotilla.broker.present import summary
    return [Item(QUESTION, asked.session, f"asks {summary(asked)}; answer with /flotilla:permit",
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
