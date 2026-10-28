"""One pass of the collector over the project's trees and local branches (gc design, sections 5 and 9).

Only what the fleet made is judged: trees and branches the ledger names, a seat's tree by its name, `fleet/*`, and
the sender's branches by the ledger's merge proof (`branches.py`). Branches are decided first, so trees are judged
against the refs that survive; a tree that stays keeps its branch checked out, so the two are settled together
until neither moves. With `act`, trees go first (under the lock, judged again), then the branches whose tree is
gone or never was. The report counts what was done and groups what stays by reason - one line per reason, not one
per tree - and names the warnings: commits only on this disk, an origin on this machine, trees whose directory is
gone (named, not pruned).

Ignored files are judged by content (stage 2): one `Twins` per pass, its hashes cached between passes. A tree whose
only obstacle is unique ignored files under the thresholds (`[gc] archive_tree_mb`, 100; `[gc] archive_pass_mb`,
500; content counted once per pass) has them archived under the removal lock and goes - the person is told after,
with the restore command. Over either threshold, or with anything else only the person settles, it waits on them.
The report keeps space freed apart from space moved: the archive sits on the same disk.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from flotilla.core.text import visible
from flotilla.gc import archive, branches, refs, remove, trees, twins

MB = 1 << 20

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
    archived: list[str] = field(default_factory=list)      # one line per tree whose files went to the archive
    moved: int = 0                                         # bytes moved to the archive
    freed: int = 0                                         # bytes removing the trees freed
    last_copies: list[str] = field(default_factory=list)   # where the last copy of a file removed on a twin is
    judged: dict = field(default_factory=dict, repr=False)  # the trees' verdicts, for the questions (not printed)
    going: set = field(default_factory=set, repr=False)     # the trees small enough to archive and remove
    finder: object = field(default=None, repr=False)       # the pass's Twins, for the apply step


def size_text(count: int) -> str:
    if count < 1024:
        return f"{count} B"
    for unit in ("KB", "MB", "GB"):
        count /= 1024
        if count < 1024 or unit == "GB":
            return f"{count:.1f} {unit}"
    return ""


def piece(rows: dict, tree: Path, branch: str, head: str) -> str:
    """The piece of work a tree belongs to: the newest row whose tree, branch or tip names it; else its branch;
    else its name. A reviewer's seat standing on the work is asked about with the builder's tree."""
    owning = [row for row in rows.values()
              if (row.tree and Path(row.tree).resolve() == Path(tree).resolve()) or (branch and row.branch == branch)
              or (head and row.tip == head)]
    if owning:
        return f"work:{owning[-1].id}"
    return f"work:{branch}" if branch else f"work:{Path(tree).name}"


def _limits(profile: dict) -> tuple[float, float]:
    config = profile.get("gc") or {}
    return (float(config.get("archive_tree_mb", 100)) * MB, float(config.get("archive_pass_mb", 500)) * MB)


def small(verdicts: dict, profile: dict) -> set[Path]:
    """The trees whose only obstacle is unique ignored files under both thresholds, in judging order; content is
    counted once per sha256 across the pass (rule 3)."""
    per_tree, per_pass = _limits(profile)
    seen: set[str] = set()
    total, found = 0, set()
    for tree, verdict in verdicts.items():
        if verdict is None or verdict.reasons or verdict.doubts or not verdict.archive:
            continue
        fresh = {digest or path: size for path, size, digest in verdict.archive if (digest or path) not in seen}
        size = sum(fresh.values())
        if size <= per_tree and total + size <= per_pass:
            found.add(tree)
            seen |= set(fresh)
            total += size
    return found


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
    untouched: list[Path] = []
    holds = {tree: branch for tree, branch, _ in listed}   # a rebase's or bisect's branch too, not only HEAD's
    for tree, branch, gone in listed:
        if gone:
            report.missing.append(str(tree))
            staying.add(branch)
        elif tree.resolve() not in ours_trees:
            _keep(report, "tree", "flotilla did not make it", tree.name)
            staying.add(branch)
            untouched.append(tree)
        else:
            judged[tree] = None
    cache = twins.load_cache(ledger.state_dir, ledger.repo_key)
    finder = twins.Twins(ledger, holders=[], cache=cache,
                         sources=twins.sources(ledger, untouched=untouched, archive_root=archive.root_of(ledger)))
    verdicts: dict[str, branches.BranchVerdict] = {}
    going: set[Path] = set()
    for _ in range(len(judged) + 2):   # trees that stay keep their branches; fewer branches go, fewer refs drop
        deleting = {name for name, item in verdicts.items() if item.delete}
        dropping = {f"refs/heads/{name}" for name in deleting}
        planned = _planned(verdicts)
        holders = refs.holders(ledger, dropping=dropping) + planned
        finder.rehold(holders)
        for tree in judged:
            judged[tree] = trees.judge_tree(ledger, tree, holders=holders, sessions=sessions, proc_root=proc_root,
                                            twins=finder)
        going = small(judged, ledger.profile)
        now_staying = staying | {holds[tree] for tree, item in judged.items()
                                 if holds[tree] and not item.removable and tree not in going}
        verdicts = {item.name: item for item in branches.judge_branches(
            ledger, found_trunks, staying_checkouts=now_staying, ours=ours_branches)}
        if {name for name, item in verdicts.items() if item.delete} == deleting:
            break
    dropping = {f"refs/heads/{name}" for name, item in verdicts.items() if item.delete}
    planned = _planned(verdicts)
    gone_trees: set[str] = set()
    rows = ledger.rows()
    day = datetime.now(timezone.utc).date().isoformat()
    for tree, verdict in judged.items():
        report.warnings += [note for note in verdict.notes if note not in report.warnings]
        if not verdict.removable and tree not in going:
            _keep(report, "tree", verdict.reasons[0] if verdict.reasons else WAITS, tree.name)
            continue
        files, size = len(verdict.archive), sum(item[1] for item in verdict.archive)
        if not act:
            report.removed.append(tree.name)
            report.parked += len(verdict.park)
            gone_trees.add(holds[tree])
            if files:
                report.archived.append(f"would archive {_count(files, 'file')} ({size_text(size)}) from "
                                       f"{tree.name}")
            continue
        done: dict = {}
        line = remove.remove_tree(ledger, verdict, dropping=dropping, sessions=sessions, proc_root=proc_root,
                                  also_held=planned, twins=finder, out=done,
                                  archive_entry=archive.entry_id(piece(rows, tree, holds[tree], verdict.head),
                                                                 day))[0]
        if done.get("entry"):
            report.moved += done["moved"]
            report.archived.append(f"archived {_count(done['files'], 'file')} ({size_text(done['moved'])}) from "
                                   f"{tree.name}: restore with flotilla gc restore {done['entry']}")
        if line.startswith("removed "):
            report.removed.append(tree.name)
            report.parked += len(verdict.park)
            report.freed += done.get("freed", 0)
            report.last_copies += [f"the last copy of {Path(item['from']).relative_to(tree)} is now in "
                                   f"{item['where']}" for item in done.get("last_copies", [])]
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
    twins.save_cache(ledger.state_dir, ledger.repo_key, cache)
    report.judged, report.going, report.finder = judged, going, finder
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
    if report.freed or report.moved:
        out.append(f"freed {size_text(report.freed)}, moved {size_text(report.moved)} to the archive (moving frees "
                   "nothing until the archive is emptied)")
    if report.removed:
        out.append(f"{verb[0]}: {_list(report.removed)}")
    out += report.archived
    out += report.last_copies[:SHOWN]
    if len(report.last_copies) > SHOWN:
        out.append(f"and the last copies of {len(report.last_copies) - SHOWN} more files moved the same way")
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
    # names of trees, branches and files reach the person's terminal (security review of 52a9a2f)
    return [visible(line) for line in out]
