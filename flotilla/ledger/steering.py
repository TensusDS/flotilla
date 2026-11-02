"""Steering the queue without moving work: adopt, hold, unhold, urgent (spec, sections 6.7, 6.9).

None of these changes a row's state. `adopt` gives work whose owner is gone to a live session whose post owns work;
the previous owner goes into the trail, and a live owner's work is never taken. `hold` keeps a handed branch out of
the reading queue on purpose, with a condition that can be asked (an open branch, or a session name the ledger or
the census knows) and a reason: a hold without them reads as forgotten work, and a misspelt name would read as
lifted. `urgent` is a person's request to ship a row out of turn; it changes only the order of the sender's brief.
`requires` amends what a row builds on, on the record: a link dropped must be one the tip no longer carries.
"""

from __future__ import annotations

from flotilla.ledger.actor import Actor, require_may
from flotilla.ledger import core, gitq
from flotilla.ledger.core import Ledger
from flotilla.ledger.errors import MoveRefused
from flotilla.ledger.model import Row
from flotilla.posts import PostError, post_for_session

HOLDABLE = ("handed", "fixing", "accepted", "queued")   # the reading queue, and what waits to ship (0.7.17)
THE_PERSON = "the person"   # a hold only the person lifts: a release put off, not work waiting on other work


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
        evidence = {"from": row.owner}
        if row.tree:   # the gone owner's clean tree lets go of the branch, so the heir can switch to it (twosuns A7)
            held = ledger.run(["git", "-C", row.tree, "symbolic-ref", "--quiet", "--short", "HEAD"],
                              capture_output=True, text=True, check=False).stdout.strip()
            # only while it still holds this branch: by now it may be on another, perhaps a later seat's (review, M2)
            evidence["tree"] = (core.free_seat_tree(ledger, row.owner, row.tree) if held == row.branch else
                                f"not freed: {row.tree} holds `{held or 'no branch'}` now, not `{row.branch}`")
        return s.append(actor, row.id, "adopt", state, fields={"owner": heir, "adopted_from": trail},
                        evidence=evidence)


def _live_or_empty(ledger: Ledger) -> set[str]:
    try:
        return ledger.live_names()
    except MoveRefused:
        return set()


def adopt_from(ledger: Ledger, actor: Actor, gone: str, *, to: str) -> tuple[list[Row], list[tuple[str, str]]]:
    """Every open row a gone session owns, to one live heir, in one move each (twosuns, 0.7.16: five sessions
    stopped in one night and their rows were handed over one command at a time). Post seats stay: a seat is the
    post held, raised again by `flotilla spawn --fill`, not work to adopt."""
    gone = gone.strip()
    branches = [row.branch for row in ledger.rows().values()
                if row.is_open and row.owner == gone and row.state != "reserved" and row.branch]
    if not branches:
        raise MoveRefused(f"{gone} owns no open work to adopt (rows it reads are reassigned with "
                          "`flotilla work assign <branch> --reader`)")
    moved, refused = [], []
    for branch in dict.fromkeys(branches):   # each row answered for itself: a refusal never hides what moved
        try:
            moved.append(adopt(ledger, actor, branch, to=to))
        except MoveRefused as err:
            refused.append((branch, str(err)))
    return moved, refused


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
        if row.state == "queued" and ledger.mode == "pr":
            raise MoveRefused(f"`{branch}` is queued as a pull request, which GitHub can merge whatever the ledger "
                              "says; hold an accepted branch before it is queued, or close its PR")
        if row.state not in HOLDABLE:
            raise MoveRefused(f"a hold keeps work out of the reading queue, or out of the batch to ship; `{branch}` "
                              f"is {row.state}")
        if until == branch:
            raise MoveRefused("a row cannot wait for itself")
        if until != THE_PERSON and s.open_row(until) is None:
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


def walkable(ledger: Ledger, actor: Actor, branch: str, *, why: str = "", clear: bool = False) -> Row:
    """The orchestrator's word that a row which others build on reaches a person on its own; `clear` takes the word
    back, and the row waits for the rows building on it again."""
    require_may(actor, "walkable", ledger.posts)
    if not clear and not why.strip():
        raise MoveRefused("say why this part reaches a person on its own (--why), or take the mark back (--clear)")
    with ledger.session() as s:
        row = s.need_open_row(branch)
        if clear and not row.walkable:
            raise MoveRefused(f"`{branch}` is not marked walkable; there is nothing to take back")
        state = s.next_state(row, "walkable")
        return s.append(actor, row.id, "walkable", state, fields={"walkable": "" if clear else why.strip()})


def _off_trunk(ledger: Ledger, tip: str, trunk_head: str) -> set[str] | None:
    done = ledger.run(["git", "-C", str(ledger.root), "rev-list", tip, f"^{trunk_head}"], capture_output=True,
                      text=True, check=False)
    return set(done.stdout.split()) if done.returncode == 0 else None


def _carried(ledger: Ledger, row: Row, dep: Row | None) -> tuple[int, str]:
    """How many commits of `dep` that are not on trunk the tip of `row` carries, and what could not be asked."""
    trunk_head = gitq.resolve(ledger.root, gitq.trunk_ref(ledger.root, ledger.trunk, run=ledger.run), run=ledger.run)
    if trunk_head is None:
        raise MoveRefused(f"git could not resolve trunk `{ledger.trunk}`, so whether the tip still carries the row "
                          "cannot be asked; fetch and try again")
    tip = gitq.branch_tip(ledger.root, row.branch, run=ledger.run) or gitq.resolve(ledger.root, row.tip,
                                                                                  run=ledger.run)
    if tip is None:
        return 0, ""   # no branch and no recorded tip: it carries nothing yet
    dep_tip = None if dep is None else (gitq.branch_tip(ledger.root, dep.branch, run=ledger.run) if dep.branch
                                        else None) or gitq.resolve(ledger.root, dep.tip, run=ledger.run)
    if dep_tip is None:
        return 0, "its commits could not be named (no branch and no recorded tip)"
    mine, theirs = _off_trunk(ledger, tip, trunk_head), _off_trunk(ledger, dep_tip, trunk_head)
    if mine is None or theirs is None:
        raise MoveRefused("git could not list the commits off trunk; nothing is dropped unchecked")
    return len(mine & theirs), ""


def requires(ledger: Ledger, actor: Actor, branch: str, *, add=(), drop=(), why: str) -> Row:
    """Add or drop what a row builds on, on the record (twosuns item 11): the owner, or a post that may. A drop is
    refused while the row's tip carries commits of the dropped row that trunk does not, so a link is only lifted
    when the work no longer needs it."""
    if not why.strip():
        raise MoveRefused("say why the links change (--why): the next reader of the row sees it in its history")
    if not add and not drop:
        raise MoveRefused("name what changes with --add or --drop (a row id or a branch), or both")
    with ledger.session() as s:
        row = s.need_open_row(branch)
        if row.owner != actor.name:
            require_may(actor, "requires", ledger.posts)
        state = s.next_state(row, "requires")
        current = list(row.requires or [])
        dropped, unchecked = [], {}
        for name in dict.fromkeys(drop):
            named = [item for item in s.rows.values() if name in (item.id, item.branch)]
            if not named and name not in current:
                raise MoveRefused(f"--drop `{name}`: no such row or branch in the ledger")
            target = next((rid for rid in current if rid == name or any(item.id == rid for item in named)), None)
            if target is None:
                links = ", ".join(f"`{s.rows[rid].branch if rid in s.rows else rid}`" for rid in current) or "nothing"
                raise MoveRefused(f"`{branch}` does not require `{name}`; it requires {links}")
            carried, note = _carried(ledger, row, s.rows.get(target))
            if carried:
                raise MoveRefused(f"`{branch}`'s tip carries {carried} commit(s) of `{name}` that are not on trunk, "
                                  f"so it still builds on it; the link stays until `{name}` reaches trunk or "
                                  f"`{branch}` is rebuilt without it")
            if note:
                unchecked[name] = note
            current.remove(target)
            dropped.append(name)
        added = []
        for target, name in zip(core.link_ids(s.rows, list(add), "--add"), add):
            if target not in current:
                current.append(target)
                added.append(name)
        evidence = {"why": why.strip(), "added": added, "dropped": dropped}
        if unchecked:
            evidence["unchecked"] = unchecked
        return s.append(actor, row.id, "requires", state, fields={"requires": current}, evidence=evidence)
