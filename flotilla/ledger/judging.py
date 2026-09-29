"""The person's path and the end of a row: walked, broke, close (spec, sections 6.2-6.3, 7.4).

The judge walks shipped work on the deployed build, never on a branch: the build must contain the shipped commit,
and where the profile says how to ask the deployment for its revision, the build walked must be that revision.
`broke` names the place in the product that failed and files the fix row at once, owned by the author; the broken
row is neither walked nor closed until a fix is delivered, and the next walk must be over a build containing it.
`close` is the author's: it checks the ticket the profile asks for, or records why the work closes without one.
"""

from __future__ import annotations

import re
import subprocess

from flotilla.ledger import core, gitq, views
from flotilla.ledger.actor import Actor, require_may
from flotilla.ledger.core import Ledger
from flotilla.ledger.errors import MoveRefused
from flotilla.ledger.model import Row, delivered, next_row_id


def _deployed(ledger: Ledger) -> str:
    command = (ledger.profile.get("deploy") or {}).get("revision_command") or ""
    if not command:
        return ""
    try:
        done = ledger.run(command, shell=True, cwd=str(ledger.root), capture_output=True, text=True, check=False,
                          timeout=60)
    except subprocess.TimeoutExpired as err:
        raise MoveRefused("deploy.revision_command did not answer within 60 s; the deployed revision is "
                          "unknown") from err
    words = (done.stdout or "").split()
    if done.returncode != 0 or not words:
        raise MoveRefused(f"deploy.revision_command failed (exit {done.returncode}); the deployed revision is "
                          "unknown")
    return words[0]


def fixes_of(rows: dict[str, Row], row: Row) -> list[Row]:
    return [other for other in rows.values() if other.fixes == row.id]


def _fixed(ledger: Ledger, rows: dict[str, Row], row: Row) -> list[Row]:
    """The delivered rows carrying this row's fixes; a fix settled by another row counts as that row (G10)."""
    found = [views.fix_delivery(rows, other, ledger.profile) for other in fixes_of(rows, row)]
    return [other for other in found if other is not None]


def _whole_or_walkable(ledger: Ledger, rows: dict[str, Row], row: Row) -> None:
    waiting = [] if row.walkable else views.pending_dependents(rows, row, ledger.profile)
    if waiting:
        names = ", ".join(f"`{other.branch or other.id}` ({other.state})" for other in waiting)
        raise MoveRefused(f"`{row.branch}` is a part: {names} build on it and are not shipped yet. Walk the path "
                          "once they ship; the orchestrator may mark this row walkable on its own "
                          f"(`flotilla work walkable {row.branch} --why \"<why>\"`)")


def walked(ledger: Ledger, actor: Actor, branch: str, *, build: str, steps: str, saw: str) -> Row:
    require_may(actor, "walked", ledger.posts)
    if not steps.strip() or not saw.strip():
        raise MoveRefused("a walk records the steps taken (--steps) and what was seen (--saw)")
    built = gitq.resolve(ledger.root, build, run=ledger.run)
    if built is None:
        raise MoveRefused(f"git could not resolve --build {build}; a walk is over a revision someone can name")
    deployed = _deployed(ledger)
    if deployed:
        same = gitq.same_revision(ledger.root, deployed, built, run=ledger.run)
        if same is None:
            raise MoveRefused(f"the deployed revision {deployed} is unknown to git here; fetch it first")
        if not same:
            raise MoveRefused(f"the deployed build is {deployed[:7]}, but --build is {built[:7]}; walk what is "
                              "deployed")
    with ledger.session() as s:
        row = s.need_open_row(branch)
        _whole_or_walkable(ledger, s.rows, row)
        state = s.next_state(row, "walked")
        if not row.merge:
            raise MoveRefused(f"`{branch}` has no shipped commit recorded")
        if gitq.is_ancestor(ledger.root, row.merge, built, run=ledger.run) is not True:
            raise MoveRefused(f"build {built[:7]} does not contain the shipped commit {row.merge[:7]}; that walk "
                              "says nothing about this work")
        if row.broken:
            fixed = _fixed(ledger, s.rows, row)
            if not fixed:
                raise MoveRefused(f"`{branch}` broke at {row.broken}; walk it again once its fix row is delivered")
            missing = [other.branch for other in fixed if not other.merge or
                       gitq.is_ancestor(ledger.root, other.merge, built, run=ledger.run) is not True]
            if missing:
                raise MoveRefused(f"build {built[:7]} does not contain the fix ({', '.join(missing)})")
        evidence = {"build": built, "steps": steps.strip(), "saw": saw.strip(),
                    "deployed": deployed or "not asked: the profile has no deploy.revision_command"}
        return s.append(actor, row.id, "walked", state, fields={"broken": ""}, evidence=evidence)


def _free_branch(rows: dict[str, Row], wanted: str) -> str:
    taken = {row.branch for row in rows.values()}
    name, number = wanted, 1
    while name in taken:
        number += 1
        name = f"{wanted}-{number}"
    return name


def broke(ledger: Ledger, actor: Actor, branch: str, *, where: str, saw: str,
          fix_branch: str = "") -> tuple[Row, Row]:
    require_may(actor, "broke", ledger.posts)
    where, saw = where.strip(), saw.strip()
    if not where:
        raise MoveRefused("name the place in the product where it broke (--where)")
    if not saw:
        raise MoveRefused("say what you saw (--saw)")
    with ledger.session() as s:
        row = s.need_open_row(branch)
        _whole_or_walkable(ledger, s.rows, row)
        state = s.next_state(row, "broke")
        name = fix_branch.strip() or _free_branch(s.rows, f"fix/{branch}")
        core.check_claim(s.rows, name)
        if gitq.branch_tip(ledger.root, name, run=ledger.run):
            raise MoveRefused(f"branch `{name}` already exists; name the fix branch with --fix-branch")
        fields = {"branch": name, "owner": row.owner, "fixes": row.id, "ref": row.ref, "tree": "", "base": ""}
        broken, fix = s.append_all([(actor, row.id, "broke", state, {"broken": where}, {"saw": saw}),
                                    (actor, next_row_id(s.rows), "claim", "claimed", fields, {"broke": where})])
    return broken, fix


def close(ledger: Ledger, actor: Actor, branch: str, *, ref: str = "", why: str = "") -> Row:
    require_may(actor, "close", ledger.posts)
    rule = (ledger.profile.get("evidence") or {}).get("close") or {}
    if rule.get("field", "ref") != "ref":
        raise MoveRefused(f"the profile's evidence.close names field `{rule.get('field')}`; flotilla checks `ref`")
    with ledger.session() as s:
        row = s.need_open_row(branch)
        state = s.next_state(row, "close")
        if row.owner != actor.name:
            raise MoveRefused(f"`{branch}` belongs to {row.owner}; the author closes their own work")
        if row.broken and not _fixed(ledger, s.rows, row):
            raise MoveRefused(f"`{branch}` broke at {row.broken}; it closes after its fix row is delivered")
        value, pattern = (ref.strip() or row.ref), rule.get("pattern") or ""
        evidence: dict = {}
        if value:
            if pattern and re.search(pattern, value) is None:
                raise MoveRefused(f"`{value}` does not match the profile's ticket pattern {pattern}")
            evidence["ref"] = value
        elif rule.get("required"):
            if not why.strip():
                raise MoveRefused(f"this project closes work against a ticket ({pattern or 'ref'}); name it with "
                                  "--ref, or close without one with --why \"<reason>\"")
            evidence["without_ref"] = why.strip()
        elif why.strip():
            evidence["why"] = why.strip()
        fields = {"ref": value} if value and value != row.ref else {}
        return s.append(actor, row.id, "close", state, fields=fields, evidence=evidence)
