"""Steering the queue without moving work: adopt, hold, unhold, urgent (spec, sections 6.7, 6.9).

None of these changes a row's state. `adopt` gives work whose owner is gone to a live session whose post owns work;
the previous owner goes into the trail, and a live owner's work is never taken. `hold` keeps a handed branch out of
the reading queue on purpose, with a condition that can be asked (an open branch, or a session name the ledger or
the census knows) and a reason: a hold without them reads as forgotten work, and a misspelt name would read as
lifted. `urgent` is a person's request to ship a row out of turn; it changes only the order of the sender's brief.
"""

from __future__ import annotations

from flotilla.ledger.actor import Actor, require_may
from flotilla.ledger.core import Ledger
from flotilla.ledger.errors import MoveRefused
from flotilla.ledger.model import Row
from flotilla.posts import PostError, post_for_session

HOLDABLE = ("handed", "fixing")


def adopt(ledger: Ledger, actor: Actor, branch: str, *, to: str) -> Row:
    require_may(actor, "adopt", ledger.posts)
    heir = to.strip()
    live = ledger.live_names()
    try:
        post = post_for_session(ledger.posts, heir)
    except PostError as err:
        raise MoveRefused(str(err)) from err
    with ledger.session() as s:
        row = s.need_open_row(branch)
        state = s.next_state(row, "adopt")
        if row.owner in live:
            raise MoveRefused(f"{row.owner} is alive; adoption is for work whose owner is gone, so ask them")
        if heir not in live:
            raise MoveRefused(f"{heir} is not alive in the census; adopt to a live session")
        if post is None or ("claim" not in post.may and post.name != ledger.owner_post(row)):
            raise MoveRefused(f"{heir} holds no post that owns this work")
        trail = f"{row.owner} <- {row.adopted_from}" if row.adopted_from else row.owner
        return s.append(actor, row.id, "adopt", state, fields={"owner": heir, "adopted_from": trail},
                        evidence={"from": row.owner})


def _live_or_empty(ledger: Ledger) -> set[str]:
    try:
        return ledger.live_names()
    except MoveRefused:
        return set()


def hold(ledger: Ledger, actor: Actor, branch: str, *, until: str, why: str) -> Row:
    require_may(actor, "hold", ledger.posts)
    until, why = until.strip(), why.strip()
    if not until:
        raise MoveRefused("a hold names what lifts it (--until <branch> or <session name>); a hold without one "
                          "reads as forgotten work")
    if not why:
        raise MoveRefused("a hold says why (--why); otherwise the author cannot tell held from lost")
    live = _live_or_empty(ledger)
    with ledger.session() as s:
        row = s.need_open_row(branch)
        state = s.next_state(row, "hold")
        if row.state not in HOLDABLE:
            raise MoveRefused(f"a hold keeps work out of the reading queue; `{branch}` is {row.state}")
        if until == branch:
            raise MoveRefused("a row cannot wait for itself")
        if s.open_row(until) is None:
            known = {r.owner for r in s.rows.values()} | {r.reader for r in s.rows.values()} | live
            if until not in known - {""}:
                raise MoveRefused(f"`{until}` is neither an open branch nor a session the ledger or the census knows; "
                                  "check the spelling, because a name that exists nowhere would read as lifted")
        return s.append(actor, row.id, "hold", state,
                        fields={"held_by": actor.name, "held_until": until, "held_why": why})


def unhold(ledger: Ledger, actor: Actor, branch: str) -> Row:
    require_may(actor, "unhold", ledger.posts)
    with ledger.session() as s:
        row = s.need_open_row(branch)
        state = s.next_state(row, "unhold")
        if not (row.held_by or row.held_until):
            raise MoveRefused(f"`{branch}` is not held")
        return s.append(actor, row.id, "unhold", state, fields={"held_by": "", "held_until": "", "held_why": ""},
                        evidence={"was": f"held by {row.held_by} until {row.held_until}"})


def urgent(ledger: Ledger, actor: Actor, branch: str, *, why: str = "", cancel: bool = False) -> Row:
    require_may(actor, "urgent", ledger.posts)
    with ledger.session() as s:
        row = s.need_open_row(branch)
        state = s.next_state(row, "urgent")
        fields = ({"urgent_at": "", "urgent_why": ""} if cancel else
                  {"urgent_at": ledger.now(), "urgent_why": why.strip()})
        return s.append(actor, row.id, "urgent", state, fields=fields)


def walkable(ledger: Ledger, actor: Actor, branch: str, *, why: str) -> Row:
    """The orchestrator's word that a row which others build on reaches a person on its own."""
    require_may(actor, "walkable", ledger.posts)
    if not why.strip():
        raise MoveRefused("say why this part reaches a person on its own (--why)")
    with ledger.session() as s:
        row = s.need_open_row(branch)
        state = s.next_state(row, "walkable")
        return s.append(actor, row.id, "walkable", state, fields={"walkable": why.strip()})
