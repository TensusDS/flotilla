"""Whose move it is, said to the session that holds it (spec, sections 6.9 and 8).

Computed from the folded rows and the posts; nothing here asks the world or stores anything, so every answer is
tested with rows alone. A move named for a post ("the sender", "the judge") is held by the sessions of that post.
"""

from __future__ import annotations

from dataclasses import dataclass

from flotilla.ledger import views
from flotilla.ledger.model import Row
from flotilla.ledger.transitions import moves_from

BALL = "ball"          # the move is yours, and nothing you recorded says you wait
WORKING = "working"    # claimed work in your hands: handing it over is your move
WAITING = "waiting"    # the move is yours, and you recorded whom you wait on (or the row is held)
HOLD = "hold"          # a hold you placed whose condition is met, or cannot be asked
UNREAD = "unread"      # your handed work names no reader
HELD_BY_ME = (BALL, WORKING)
POST_OF_MOVER = views.POST_OF_MOVER
HIDDEN_MOVES = ("run",)   # recorded by the lane, never made by a person


@dataclass(frozen=True)
class Item:
    kind: str
    branch: str
    text: str
    since: str
    who: str = ""   # what makes it the same item across polls: the session, never a census word that flickers


def holds_move(row: Row, profile: dict, name: str, post: str = "", rows: dict | None = None) -> bool:
    mover = views.who_moves(row, profile, rows)
    if not mover:
        return False
    return mover == name or (bool(post) and POST_OF_MOVER.get(mover) == post)


def next_moves(row: Row, profile: dict, may=None, owner_post: str = "") -> list[str]:
    legal = moves_from(row.state, profile, owner_post)
    return sorted(move for move in legal if move not in HIDDEN_MOVES and (may is None or move in may))


def _what(row: Row, profile: dict) -> str:
    if row.state == "handed":
        return f"handed to you to read at {row.tip[:7] or 'an unrecorded tip'}"
    if row.state == "landed" and (profile.get("flow") or {}).get("mode") == "local":
        return "landed on the local trunk"
    return {"reserved": "reserved for you", "fixing": "returned to you for fixes",
            "accepted": "accepted by its reader", "queued": "queued", "landed": "landed",
            "shipped": "shipped", "walked": "walked on the live build"}.get(row.state, row.state)


def since_of(rows: dict[str, Row], branch: str) -> str:
    found = [row for row in rows.values() if row.branch == branch and row.is_open]
    return found[-1].updated_at if found else ""


def mine(rows: dict[str, Row], profile: dict, name: str, *, post: str = "", may=None,
         owner_post=lambda owner: "", live: set[str] | None = None) -> list[Item]:
    items = []
    for row in rows.values():
        if not row.is_open or row.state == "reserved":   # a post row is the post held, not a move
            continue
        if row.owner == name and row.state == "handed" and not row.reader and not row.held_until:
            items.append(Item(UNREAD, row.branch, "handed, and no reader is named; the orchestrator assigns one",
                              row.updated_at))
            continue
        if not holds_move(row, profile, name, post, rows):
            continue
        if row.waiting_on:
            items.append(Item(WAITING, row.branch, f"you wait on {row.waiting_on}: {row.note}", row.updated_at))
        elif row.held_until:
            items.append(Item(WAITING, row.branch, f"held by {row.held_by} until {row.held_until}: {row.held_why}",
                              row.updated_at))
        elif row.state == "claimed":
            items.append(Item(WORKING, row.branch, "claimed by you: hand it over, or record whom you wait on",
                              row.updated_at))
        else:
            moves = ", ".join(next_moves(row, profile, may, owner_post(row.owner))) or "none your post may make"
            items.append(Item(BALL, row.branch, f"{_what(row, profile)}; your moves: {moves}", row.updated_at))
    for found in views.deviations(rows, profile, live):
        if found["kind"] in ("hold_lifted", "hold_unknown") and found["on"] == name:
            items.append(Item(HOLD, found["branch"], found["why"], since_of(rows, found["branch"])))
    return items
