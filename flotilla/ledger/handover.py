"""Handing work over (spec, section 6.3).

A handover names the branch tip, needs the handover tiers green over that tip, and records a stack on work that is
not yet accepted. A tip that moves after handover is its own move; once a reader has taken the branch, it needs the
reader's agreement, because otherwise they finish reading a revision that is gone.
"""

from __future__ import annotations

from flotilla.ledger import gitq, receipts
from flotilla.ledger.actor import Actor, require_may
from flotilla.ledger.core import Ledger
from flotilla.ledger.errors import MoveRefused
from flotilla.ledger.model import Row

UNACCEPTED = ("claimed", "handed", "fixing")


def _current_tip(ledger: Ledger, branch: str, named: str | None = None) -> str:
    current = gitq.branch_tip(ledger.root, branch, run=ledger.run)
    if current is None:
        raise MoveRefused(f"branch `{branch}` has no tip git can resolve")
    if named is not None:
        same = gitq.same_revision(ledger.root, named, current, run=ledger.run)
        if same is None:
            raise MoveRefused(f"--tip {named}: git could not resolve it")
        if not same:
            raise MoveRefused(f"--tip {named} is not the branch tip {current[:7]}; hand over what is there")
    return current


def _receipt(ledger: Ledger, tip: str) -> str:
    ok, why = receipts.check_receipt(state=ledger.state_dir, repo_key=ledger.repo_key, sha=tip, purpose="handover",
                                     profile=ledger.profile)
    if not ok:
        raise MoveRefused(why)
    return why


def _stacked_on(ledger: Ledger, rows: dict, row: Row, tip: str) -> list[str]:
    trunk_head = gitq.resolve(ledger.root, gitq.trunk_ref(ledger.root, ledger.trunk, run=ledger.run), run=ledger.run)
    found = []
    for other in rows.values():
        if other.id == row.id or not other.is_open or other.state not in UNACCEPTED:
            continue
        other_tip = gitq.branch_tip(ledger.root, other.branch, run=ledger.run)
        if not other_tip or other_tip == tip:
            continue
        in_trunk = bool(trunk_head) and gitq.is_ancestor(ledger.root, other_tip, trunk_head, run=ledger.run) is True
        if gitq.is_ancestor(ledger.root, other_tip, tip, run=ledger.run) is True and not in_trunk:
            found.append(other.branch)
    return sorted(found)


def hand(ledger: Ledger, actor: Actor, branch: str, *, tip: str | None = None) -> Row:
    require_may(actor, "hand", ledger.posts)
    current = _current_tip(ledger, branch, tip)
    receipt = _receipt(ledger, current)
    with ledger.session() as s:
        row = s.need_open_row(branch)
        if row.owner != actor.name:
            raise MoveRefused(f"`{branch}` belongs to {row.owner}; only the owner hands it over")
        state = s.next_state(row, "hand")
        stacked = _stacked_on(ledger, s.rows, row, current)
        return s.append(actor, row.id, "hand", state, fields={"tip": current, "verdict": "", "taken": False},
                        evidence={"receipt": receipt, "stacked_on": stacked})


def moved(ledger: Ledger, actor: Actor, branch: str, *, tip: str, agreed_by: str = "") -> Row:
    require_may(actor, "moved", ledger.posts)
    current = _current_tip(ledger, branch, tip)
    receipt = _receipt(ledger, current)
    with ledger.session() as s:
        row = s.need_open_row(branch)
        if row.owner != actor.name:
            raise MoveRefused(f"`{branch}` belongs to {row.owner}; only the owner records a moved tip")
        state = s.next_state(row, "moved")
        if row.reader and row.taken and agreed_by != row.reader:
            raise MoveRefused(f"{row.reader} is reading `{branch}` over {row.tip[:7]}; moving the tip needs their "
                              f"agreement (--agreed-by \"{row.reader}\")")
        return s.append(actor, row.id, "moved", state, fields={"tip": current},
                        evidence={"from": row.tip, "agreed_by": agreed_by, "receipt": receipt})


def wait(ledger: Ledger, actor: Actor, branch: str, *, on: str = "", why: str = "", clear: bool = False) -> Row:
    require_may(actor, "wait", ledger.posts)
    with ledger.session() as s:
        row = s.need_open_row(branch)
        if actor.name not in (row.owner, row.reader):
            raise MoveRefused(f"only the owner ({row.owner}) or the reader of `{branch}` records a wait on it")
        state = s.next_state(row, "wait")
        if clear:
            fields = {"waiting_on": "", "note": ""}
        else:
            if not on.strip() or not why.strip():
                raise MoveRefused("a wait names whom it waits on (--on) and why (--why)")
            fields = {"waiting_on": on.strip(), "note": why.strip()}
        return s.append(actor, row.id, "wait", state, fields=fields)
