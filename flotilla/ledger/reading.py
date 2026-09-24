"""Reading handed work (spec, sections 6.3 and 6.9).

`take` is the reader saying "I am reading this": a fact in the ledger, where the ai-os tool inferred it from
transcripts. An author never reads, returns or accepts their own work — enforced by the row, not only by the posts.
A verdict stands over the handed tip only: the revision read must be that tip, and the branch must still be there.
"""

from __future__ import annotations

from flotilla.ledger import gitq
from flotilla.ledger.actor import Actor, require_may
from flotilla.ledger.core import Ledger
from flotilla.ledger.errors import MoveRefused
from flotilla.ledger.model import Row
from flotilla.posts import PostError, post_for_session


def _not_the_author(row: Row, actor: Actor, doing: str) -> None:
    if actor.name == row.owner:
        raise MoveRefused(f"`{row.branch}` is {actor.name}'s own work; an author never {doing} it")


def _not_assigned_elsewhere(row: Row, actor: Actor) -> None:
    if row.reader and row.reader != actor.name:
        raise MoveRefused(f"`{row.branch}` is assigned to {row.reader}")


def letter(row: Row) -> str:
    base = row.base[:7] if row.base else "unknown"
    return (f"`{row.branch}` is handed to you to read (row {row.id}, owner {row.owner}, tip {row.tip[:7]}, "
            f"base {base}).\n"
            f"Start with `flotilla work take {row.branch}`. Your verdict is "
            f"`flotilla work accept {row.branch} --reviewed <sha>` or "
            f"`flotilla work fix {row.branch} --why \"<what must change>\"`.")


def take(ledger: Ledger, actor: Actor, branch: str) -> Row:
    require_may(actor, "take", ledger.posts)
    with ledger.session() as s:
        row = s.need_open_row(branch)
        _not_the_author(row, actor, "reads")
        _not_assigned_elsewhere(row, actor)
        state = s.next_state(row, "take")
        return s.append(actor, row.id, "take", state, fields={"reader": actor.name, "taken": True})


def recuse(ledger: Ledger, actor: Actor, branch: str) -> Row:
    require_may(actor, "recuse", ledger.posts)
    with ledger.session() as s:
        row = s.need_open_row(branch)
        if row.reader != actor.name:
            raise MoveRefused(f"`{branch}` is not assigned to {actor.name}")
        state = s.next_state(row, "recuse")
        return s.append(actor, row.id, "recuse", state, fields={"reader": "", "taken": False})


def fix(ledger: Ledger, actor: Actor, branch: str, *, why: str) -> Row:
    require_may(actor, "fix", ledger.posts)
    if not why.strip():
        raise MoveRefused("a return names what must change (--why)")
    with ledger.session() as s:
        row = s.need_open_row(branch)
        _not_the_author(row, actor, "returns")
        _not_assigned_elsewhere(row, actor)
        state = s.next_state(row, "fix")
        return s.append(actor, row.id, "fix", state, fields={"reader": actor.name, "taken": True, "verdict": ""},
                        evidence={"why": why.strip()})


def assign(ledger: Ledger, actor: Actor, branch: str, *, reader: str) -> tuple[Row, str]:
    require_may(actor, "assign", ledger.posts)
    live = ledger.live_names()
    try:
        reader_post = post_for_session(ledger.posts, reader)
    except PostError as err:
        raise MoveRefused(str(err)) from err
    with ledger.session() as s:
        row = s.need_open_row(branch)
        state = s.next_state(row, "assign")
        if reader == row.owner:
            raise MoveRefused(f"{reader} is the author of `{branch}`; an author never reads their own work")
        if reader not in live:
            raise MoveRefused(f"{reader} is not alive in the census; assign a live reader")
        if reader_post is None or "accept" not in reader_post.may:
            raise MoveRefused(f"{reader} holds no post that may accept")
        new = s.append(actor, row.id, "assign", state, fields={"reader": reader, "taken": False})
    return new, letter(new)


def accept(ledger: Ledger, actor: Actor, branch: str, *, reviewed: str) -> Row:
    require_may(actor, "accept", ledger.posts)
    with ledger.session() as s:
        row = s.need_open_row(branch)
        _not_the_author(row, actor, "accepts")
        _not_assigned_elsewhere(row, actor)
        state = s.next_state(row, "accept")
        same = gitq.same_revision(ledger.root, reviewed, row.tip, run=ledger.run)
        if same is None:
            raise MoveRefused(f"git could not resolve `{reviewed}` or the handed tip; nothing is accepted over a "
                              "revision nobody can name")
        if not same:
            raise MoveRefused(f"you read {reviewed}, but the handed tip is {row.tip[:7]}; accept the revision that "
                              "was handed over")
        current = gitq.branch_tip(ledger.root, branch, run=ledger.run)
        if current is None:
            raise MoveRefused(f"git could not resolve the tip of `{branch}`; nothing is accepted over a branch nobody "
                              "can name")
        if current != row.tip:
            raise MoveRefused(f"`{branch}` moved since handover ({row.tip[:7]} -> {current[:7]}); the owner records "
                              "it with `moved` first")
        return s.append(actor, row.id, "accept", state,
                        fields={"verdict": row.tip, "reader": actor.name, "taken": True},
                        evidence={"reviewed": reviewed})
