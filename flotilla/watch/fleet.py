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

DEVIATION, DROPPED, NOBODY, BREAK = "deviation", "dropped", "nobody", "break"


def not_working(session) -> bool:
    """What the census says, read without guessing: background sessions report `state`, interactive ones `status`."""
    if session.kind == "background":
        return session.state in ("blocked", "done")
    return session.status == "idle"


def _census_word(session) -> str:
    if session.kind == "background" and session.state == "blocked":
        return "blocked: idle, or waiting on a permission prompt"
    return session.state or session.status or "unknown"


def movers(row, profile: dict, live: set[str], post_of) -> set[str]:
    mover = views.who_moves(row, profile)
    if mover in POST_OF_MOVER:
        return {name for name in live if post_of(name) == POST_OF_MOVER[mover]}
    if mover and not mover.startswith("the "):
        return {mover}
    return set()


def fleet(rows: dict, profile: dict, sessions, *, post_of, breaks=()) -> list[Item]:
    by_name = {session.name: session for session in sessions if session.name}
    live = set(by_name)
    items = [Item(DEVIATION, found["branch"], f"{found['kind']}: {found['why']}", since_of(rows, found["branch"]))
             for found in views.deviations(rows, profile, live)]
    for row in rows.values():
        if not row.is_open or row.waiting_on or row.held_until:
            continue
        mover = views.who_moves(row, profile)
        named = movers(row, profile, live, post_of) & live
        if mover in POST_OF_MOVER and not named:
            items.append(Item(NOBODY, row.branch, f"the move is {mover}'s, and no live session holds the "
                                                  f"`{POST_OF_MOVER[mover]}` post", row.updated_at))
        for name in sorted(named):
            if not_working(by_name[name]):
                items.append(Item(DROPPED, row.branch, f"{name} holds the move ({row.state}) and is not working "
                                                       f"(census: {_census_word(by_name[name])}); message them",
                                  row.updated_at))
    items.extend(breaks)
    return items


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
        if row.waiting_on or row.held_until or not holds_move(row, profile, name, post_of(name)):
            continue
        items.append(Item(BREAK, branch, f"{name} stopped twice while holding this move ({row.state}), and "
                                         "nothing has moved since", at))
    return items
