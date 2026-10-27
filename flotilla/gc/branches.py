"""Branch verdicts (gc design, sections 5.2 and 5.3): a branch is judged on its own, apart from any tree.

Only the fleet's branches are judged: those the ledger names, `fleet/*`, and the sender's (`batch/*` and the like)
when the ledger proves them flotilla's - their tip is the recorded `merge` of a shipped row. No name pattern is
used, so a person's branch that happens to match is never judged (decision 207, I5). A declared trunk is never
judged. A branch goes when it has landed (`refs.landing`), no open row holds it and no tree that stays has it
checked out - `git update-ref -d` would not refuse that and would leave the tree on an unborn branch. Its
reflog-only commits are parked first, and its tip too when it landed by a squash or on a trunk origin does not
hold. Branches on origin are never deleted: another clone may be working on them; landed ones are listed.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field

from flotilla.gc import refs
from flotilla.gc.trees import DONE


@dataclass
class BranchVerdict:
    name: str
    tip: str
    delete: bool
    reason: str = ""
    park: list[str] = field(default_factory=list)


def _git(ledger, *args) -> subprocess.CompletedProcess:
    return ledger.run(["git", "-C", str(ledger.root), *args], capture_output=True, text=True, check=False)


def _names(ledger, prefix: str) -> dict[str, str]:
    listed = _git(ledger, "for-each-ref", "--format=%(refname) %(objectname)", prefix).stdout
    return {line.split(" ", 1)[0][len(prefix):]: line.split(" ", 1)[1]
            for line in listed.splitlines() if " " in line}


def _declared(ledger) -> set[str]:
    return {ledger.trunk} | {str(item) for item in (ledger.profile.get("gc") or {}).get("trunks") or []}


def _sender_tips(ledger) -> set[str]:
    return {row.merge for row in ledger.rows().values()
            if row.merge and any(step.get("move") == "ship" for step in row.history)}


def judge_branches(ledger, trunks: list[refs.Trunk], *, staying_checkouts: set[str], ours: set[str]) \
        -> list[BranchVerdict]:
    declared = _declared(ledger)
    staying = set(staying_checkouts)
    main = _git(ledger, "symbolic-ref", "-q", "--short", "HEAD").stdout.strip()
    if main:
        staying.add(main)   # the main checkout stays, whatever it has checked out
    has_trunk = any(item.name == ledger.trunk for item in trunks)
    _, why = refs.origin_tip(ledger, ledger.trunk) if not has_trunk else ("", "")
    shipped, sender = refs.shipped_tips(ledger), _sender_tips(ledger)
    rows = list(ledger.rows().values())
    found = []
    for name, tip in sorted(_names(ledger, "refs/heads/").items()):
        if name in declared:
            continue
        verdict = BranchVerdict(name, tip, False)
        found.append(verdict)
        if name not in ours and not name.startswith("fleet/") and tip not in sender:
            verdict.reason = "flotilla did not make it"
            continue
        if not has_trunk:
            verdict.reason = why or f"the trunk `{ledger.trunk}` could not be read"
            continue
        if name in staying:
            verdict.reason = "checked out in a tree that stays"
            continue
        held = [row for row in rows if row.branch == name and row.is_open and row.state not in DONE]
        if held:
            verdict.reason = f"open row {held[0].id} ({held[0].state})"
            continue
        landed = refs.landing(ledger, tip, trunks, shipped)
        if landed is None:
            verdict.reason = "its commits are not on any trunk, and no shipped row carries it"
            continue
        holders = refs.holders(ledger, dropping={f"refs/heads/{name}"})
        logged = _git(ledger, "reflog", "show", "--format=%H", f"refs/heads/{name}").stdout.split()
        verdict.park = list(dict.fromkeys(([tip] if landed.park else []) + refs.unheld(ledger, logged, holders)))
        verdict.delete = True
        verdict.reason = f"landed on `{landed.trunk}`" if landed.how == "trunk" else "shipped as a squash"
    return found


def landed_remote_branches(ledger, trunks: list[refs.Trunk]) -> list[str]:
    """Origin's branches, other than the trunks, whose work has landed: for the report, never deleted."""
    declared = _declared(ledger)
    found = []
    for name, tip in sorted(_names(ledger, "refs/remotes/origin/").items()):
        if name == "HEAD" or name in declared:
            continue
        if refs.landing(ledger, tip, trunks, set()) is not None:
            found.append(f"origin/{name}")
    return found
