"""One pass of the collector over the project's trees and local branches (gc design, sections 5 and 9).

Only what the fleet made is judged: trees and branches the ledger names, a seat's tree by its name, `fleet/*`, and
the sender's branches by the ledger's merge proof (`branches.py`). Branches are decided first, so trees are judged
against the refs that survive; a tree that stays keeps its branch checked out, so the two are settled together
until neither moves. With `act`, trees go first (under the lock, judged again), then the branches whose tree is
gone or never was. The report counts what was done and groups what stays by reason - one line per reason, not one
per tree - and names the warnings: commits only on this disk, an origin on this machine, trees whose directory is
gone (named, not pruned).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from flotilla.gc import branches, refs, remove, trees

SHOWN = 5   # names per kept line before "and N more"
WAITS = "waits on the person (flotilla gc next)"   # one line for every tree only the person can settle


@dataclass
class Report:
    removed: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    parked: int = 0
    kept: dict[str, list[str]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    remote_landed: list[str] = field(default_factory=list)
    acted: bool = False


def _git(ledger, *args):
    return ledger.run(["git", "-C", str(ledger.root), *args], capture_output=True, text=True, check=False)


def _held_by_operation(tree: Path) -> str:
    """The branch a rebase or a bisect in `tree` has detached from: `git worktree list` shows such a tree as
    detached, and deleting its branch breaks the operation (final review of gc stage 1, I4)."""
    try:
        pointer = (tree / ".git").read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    gitdir = Path(pointer[len("gitdir: "):]) if pointer.startswith("gitdir: ") else None
    if gitdir is None:
        return ""
    for name in ("rebase-merge/head-name", "rebase-apply/head-name", "BISECT_START"):
        try:
            found = (gitdir / name).read_text(encoding="utf-8").strip()
        except OSError:
            continue
        return found[len("refs/heads/"):] if found.startswith("refs/heads/") else found
    return ""


def worktrees(ledger) -> list[tuple[Path, str, bool]]:
    """(tree, branch, missing) for every worktree of the project but the main checkout; a detached tree in the
    middle of a rebase or bisect counts as holding the branch it left."""
    listing = _git(ledger, "worktree", "list", "--porcelain").stdout
    found, current, branch, gone, first = [], None, "", False, True
    for line in listing.splitlines() + [""]:
        if line.startswith("worktree "):
            current, branch, gone = Path(line[len("worktree "):]), "", False
        elif line.startswith("branch refs/heads/"):
            branch = line[len("branch refs/heads/"):]
        elif line.startswith("prunable"):
            gone = True
        elif not line and current is not None:
            if not first:   # git lists the main checkout first
                missing = gone or not current.is_dir()
                found.append((current, branch or ("" if missing else _held_by_operation(current)), missing))
            first, current = False, None
    return found


def made_by_flotilla(ledger) -> tuple[set[Path], set[str]]:
    """The trees and branches the fleet made: those the ledger names, a seat's tree by its name
    (`<main>-<post>-<n>`). A person's own tree or branch is not the sweep's to judge (review of the cleanup, I5)."""
    rows = list(ledger.rows().values())
    found_trees = {Path(row.tree).resolve() for row in rows if row.tree}
    found_branches = {row.branch for row in rows if row.branch}
    main = Path(ledger.root).resolve()
    seat = re.compile(rf"^{re.escape(main.name)}-(?:{'|'.join(map(re.escape, ledger.posts))})-\d+$") \
        if ledger.posts else None
    for tree, _, _ in worktrees(ledger):
        if seat is not None and tree.parent.resolve() == main.parent and seat.match(tree.name):
            found_trees.add(tree.resolve())
    return found_trees, found_branches


def _planned(verdicts) -> list[str]:
    """The commits the branches deleted in this pass park: they hold what they reach."""
    return sorted({sha for item in verdicts.values() if item.delete for sha in item.park})


def _group(reason: str) -> str:
    head = reason.split(":", 1)[0]
    return re.sub(r"^\d+ ", "", re.sub(r" [0-9a-f]{7,40}\b", "", head))


def _keep(report: Report, kind: str, reason: str, name: str) -> None:
    report.kept.setdefault(f"{kind}\0{_group(reason)}", []).append(name)


def run_pass(ledger, *, sessions, act: bool, proc_root=None) -> Report:
    report = Report(acted=act)
    found_trunks, report.warnings = refs.trunks(ledger)
    ours_trees, ours_branches = made_by_flotilla(ledger)
    listed = worktrees(ledger)
    staying: set[str] = set()
    judged: dict[Path, trees.TreeVerdict] = {}
    holds = {tree: branch for tree, branch, _ in listed}   # a rebase's or bisect's branch too, not only HEAD's
    for tree, branch, gone in listed:
        if gone:
            report.missing.append(str(tree))
            staying.add(branch)
        elif tree.resolve() not in ours_trees:
            _keep(report, "tree", "flotilla did not make it", tree.name)
            staying.add(branch)
        else:
            judged[tree] = None
    verdicts: dict[str, branches.BranchVerdict] = {}
    for _ in range(len(judged) + 2):   # trees that stay keep their branches; fewer branches go, fewer refs drop
        deleting = {name for name, item in verdicts.items() if item.delete}
        dropping = {f"refs/heads/{name}" for name in deleting}
        planned = _planned(verdicts)
        holders = refs.holders(ledger, dropping=dropping) + planned
        for tree in judged:
            judged[tree] = trees.judge_tree(ledger, tree, holders=holders, sessions=sessions, proc_root=proc_root)
        now_staying = staying | {holds[tree] for tree, item in judged.items() if holds[tree] and not item.removable}
        verdicts = {item.name: item for item in branches.judge_branches(
            ledger, found_trunks, staying_checkouts=now_staying, ours=ours_branches)}
        if {name for name, item in verdicts.items() if item.delete} == deleting:
            break
    dropping = {f"refs/heads/{name}" for name, item in verdicts.items() if item.delete}
    planned = _planned(verdicts)
    gone_trees: set[str] = set()
    for tree, verdict in judged.items():
        report.warnings += [note for note in verdict.notes if note not in report.warnings]
        if not verdict.removable:
            _keep(report, "tree", verdict.reasons[0] if verdict.reasons else WAITS, tree.name)
            continue
        if not act:
            report.removed.append(tree.name)
            report.parked += len(verdict.park)
            gone_trees.add(holds[tree])
            continue
        line = remove.remove_tree(ledger, verdict, dropping=dropping, sessions=sessions, proc_root=proc_root,
                                  also_held=planned)[0]
        if line.startswith("removed "):
            report.removed.append(tree.name)
            report.parked += len(verdict.park)
            gone_trees.add(holds[tree])
        else:
            _keep(report, "tree", line.split(": ", 1)[1] if ": " in line else line, tree.name)
            staying.add(holds[tree])
    checked_out = {holds[tree] for tree in judged if holds[tree]} - gone_trees
    for name, verdict in sorted(verdicts.items()):
        if not verdict.delete or name in checked_out or name in staying:
            reason = verdict.reason if not verdict.delete else "checked out in a tree that stays"
            if reason != "flotilla did not make it":
                _keep(report, "branch", reason, name)
            continue
        if not act:
            report.deleted.append(name)
            report.parked += len(verdict.park)
            continue
        line = remove.delete_branch(ledger, verdict)
        if line.startswith("deleted "):
            report.deleted.append(name)
            report.parked += len(verdict.park)
        else:
            _keep(report, "branch", line.split(": ", 1)[1], name)
    report.remote_landed = branches.landed_remote_branches(ledger, found_trunks)
    return report


def _count(count: int, word: str) -> str:
    return f"{count} {word}" + ("" if count == 1 else "es" if word.endswith("ch") else "s")


def _list(names: list[str]) -> str:
    more = f" and {len(names) - SHOWN} more" if len(names) > SHOWN else ""
    return ", ".join(names[:SHOWN]) + more


def lines(report: Report) -> list[str]:
    """Counts first, then what went by name, then one line per reason something stays, then the warnings."""
    if not (report.removed or report.deleted or report.kept or report.missing):
        return ["nothing to clean: no tree or branch beside the main checkout"] + report.warnings
    verb = ("removed", "deleted", "parked") if report.acted else ("would remove", "would delete", "would park")
    out = [f"{verb[0]} {_count(len(report.removed), 'tree')}, {verb[1]} {_count(len(report.deleted), 'branch')}, "
           f"{verb[2]} {_count(report.parked, 'commit')}"]
    if report.removed:
        out.append(f"{verb[0]}: {_list(report.removed)}")
    if report.deleted:
        out.append(f"{verb[1]}: {_list(report.deleted)}")
    for key, names in report.kept.items():
        kind, reason = key.split("\0", 1)
        out.append(f"kept {_count(len(names), kind)}: {reason} - {_list(names)}")
    if report.missing:
        out.append(f"missing: {_count(len(report.missing), 'tree')} whose directory is gone, not pruned: "
                   f"{_list(report.missing)}")
    out += report.warnings
    if report.remote_landed:
        out.append(f"{_count(len(report.remote_landed), 'branch')} on origin have landed and are never deleted "
                   f"here: {_list(report.remote_landed)}")
    return out
