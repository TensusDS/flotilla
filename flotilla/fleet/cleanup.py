"""Removing a seat's tree and branch: the names `retire`, `fleet down` and `fleet clean` call, over `flotilla/gc`.

The person's rule (2026-10-02): if there is the slightest doubt the work may not be merged, keep it; if it surely is,
remove it. The gc design (2026-10-21) made the tree and its branch two decisions: a tree goes when it holds nothing
that exists nowhere else - its commits on a ref that stays, its files committed or made again - and its branch goes
when its work has landed. Commits only a reflog reaches are parked under `refs/flotilla/parked/`, not lost. What the
judge cannot settle stays, named, for the orchestrator to make sure or for the person to decide.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from flotilla.gc import branches, refs, remove as gc_remove, sweep as gc_sweep, trees
from flotilla.gc.ignored import REGENERABLE, REGENERABLE_SUFFIXES
from flotilla.gc.trees import DONE, LOCK_PREFIX  # noqa: F401 - names other modules read from here

__all__ = ["DONE", "LOCK_PREFIX", "REGENERABLE", "Verdict", "judge", "remove", "sweep", "sweep_branch"]


@dataclass
class Verdict:
    tree: str
    branch: str          # "" for a detached tree
    removable: bool
    reasons: list[str] = field(default_factory=list)
    tip: str = ""        # the commit judged
    judged: trees.TreeVerdict | None = None
    branch_verdict: branches.BranchVerdict | None = None
    dropping: set[str] = field(default_factory=set)
    sessions: list = field(default_factory=list)
    spare_rows: set = field(default_factory=set)
    also_held: list = field(default_factory=list)


def _regenerable(path: str) -> bool:
    """The ignored entry ITSELF is what a build or install makes (review of the cleanup, I1)."""
    name = path.rstrip("/").rsplit("/", 1)[-1]
    return name in REGENERABLE or name.endswith(REGENERABLE_SUFFIXES)


def _staying_checkouts(ledger, tree) -> set[str]:
    here = str(tree)
    return {branch for path, branch, _ in gc_sweep.worktrees(ledger) if branch and str(path) != here}


def judge(ledger, tree, *, spare_rows=(), sessions=()) -> Verdict:
    """Whether `tree` can go, its branch judged first; `spare_rows` do not hold it (the retiring seat's post row)."""
    found_trunks, _ = refs.trunks(ledger)
    ours = gc_sweep.made_by_flotilla(ledger)[1]
    head = ledger.run(["git", "-C", str(tree), "symbolic-ref", "-q", "--short", "HEAD"], capture_output=True,
                      text=True, check=False).stdout.strip()
    mine = None
    if head:
        mine = next((item for item in branches.judge_branches(
            ledger, found_trunks, staying_checkouts=_staying_checkouts(ledger, tree), ours=ours | {head},
            spare_rows=set(spare_rows))
            if item.name == head), None)
    dropping = {f"refs/heads/{head}"} if mine is not None and mine.delete else set()
    also = list(mine.park) if dropping else []   # what the branch parks holds what it reaches, as its ref will
    judged = trees.judge_tree(ledger, tree, holders=refs.holders(ledger, dropping=dropping) + also,
                              sessions=list(sessions), spare_rows=spare_rows)
    if not judged.removable and dropping:   # a tree that stays keeps its branch checked out
        mine, dropping, also = None, set(), []
        judged = trees.judge_tree(ledger, tree, holders=refs.holders(ledger), sessions=list(sessions),
                                  spare_rows=spare_rows)
    return Verdict(str(tree), judged.branch, judged.removable, judged.why, judged.head, judged, mine, dropping,
                   list(sessions), set(spare_rows), also)


def remove(ledger, verdict: Verdict) -> list[str]:
    """Remove a tree judged removable, then its branch when that was judged deletable."""
    if not verdict.removable or verdict.judged is None:
        return []
    lines = gc_remove.remove_tree(ledger, verdict.judged, dropping=verdict.dropping, sessions=verdict.sessions,
                                   spare_rows=verdict.spare_rows, also_held=verdict.also_held)
    if lines and lines[0].startswith("removed ") and verdict.branch:
        mine = verdict.branch_verdict
        lines.append(gc_remove.delete_branch(ledger, mine) if mine is not None and mine.delete
                     else f"kept branch `{verdict.branch}`: "
                          + (mine.reason if mine is not None else "its commits are not on any trunk"))
    return lines


def _delete_branch(ledger, branch: str, tip: str) -> str:
    return gc_remove.delete_branch(ledger, branches.BranchVerdict(branch, tip, True))


def sweep_branch(ledger, name: str) -> list[str]:
    """Judge one local branch no tree holds, and delete it when its work has landed; say so either way."""
    checked_out = {branch for _, branch, _ in gc_sweep.worktrees(ledger) if branch}
    exists = ledger.run(["git", "-C", str(ledger.root), "rev-parse", "--verify", "-q", f"refs/heads/{name}"],
                        capture_output=True, text=True, check=False).stdout.strip()
    if name in checked_out or not exists:
        return []
    found_trunks, _ = refs.trunks(ledger)
    mine = next((item for item in branches.judge_branches(ledger, found_trunks, staying_checkouts=checked_out,
                                                          ours={name}) if item.name == name), None)
    if mine is None:
        return []
    return [gc_remove.delete_branch(ledger, mine) if mine.delete else f"kept branch `{name}`: {mine.reason}"]


def sweep(ledger, *, sessions, act: bool, daily: bool = False, census=None, caller: str = "", usage=None,
          proc_root=None) -> list[str]:
    """One pass over every tree and local branch the fleet made: what goes, what is parked, what stays and why. With
    `daily`, the orchestrator's pass (gc design 4 and 8), run only when due: dead seats, the sweep, a low disk's
    shrinking, the outputs letters, the archive's lifetime, and what waits on the person - who is never asked here."""
    if not daily:
        return gc_sweep.lines(gc_sweep.run_pass(ledger, sessions=sessions, act=act))
    return _daily(ledger, sessions=sessions, census=census, caller=caller, usage=usage, proc_root=proc_root)


def _daily(ledger, *, sessions, census, caller: str, usage, proc_root) -> list[str]:
    import shutil
    import time
    from datetime import datetime, timezone

    from flotilla.gc import archive, dead, disk, due, expiry, questions, shrink, store
    from flotilla.ledger.letters import render
    now = time.time()
    found = disk.state(ledger.root, ledger.profile, usage=usage or shutil.disk_usage)
    reasons = due.due(ledger.state_dir, ledger.repo_key, now=now) + (["the disk is low"] if found.low else [])
    if not reasons:   # a low disk is an occasion of its own (gc design 4 j; final review, I1)
        return [f"the collector's next pass is due in {due.hours_left(ledger.state_dir, ledger.repo_key, now=now)} h; "
                "nothing to do"]
    lines = [f"the collector's daily pass ({'; '.join(reasons)})"]
    data = store.load(ledger.state_dir, ledger.repo_key)
    seats = dead.dead_seats(ledger, sessions, proc_root=proc_root, data=data)
    lines += dead.retire_dead(ledger, seats, census=census or (lambda: sessions), caller=caller or "daily pass")
    report = gc_sweep.run_pass(ledger, sessions=sessions, act=True, proc_root=proc_root)
    lines += gc_sweep.lines(report)
    lines += [f"note: {note}" for note in found.notes]
    if found.free_mb is None:
        lines.append(f"note: {found.text}; nothing is refused or shrunk for it")
    if found.low:
        lines.append(f"the disk is low: {found.text}" + (f"; {disk.refusal(found)}" if found.no_room else ""))
        lines += shrink.lines(shrink.shrink(ledger, sessions=sessions, act=True, proc_root=proc_root))
    today = datetime.now(timezone.utc)
    for letter in shrink.piling(ledger, sessions=sessions, data=data, today=today.date().isoformat()):
        lines += render(letter)
    ripe = expiry.ready(ledger, data, now=today, low=found.low)   # never carried out here: the person's apply does
    if ripe:
        lines.append(f"the archive: the person's yes is ready for {ripe} media file(s); they carry it out with "
                     "/flotilla:gc in their own session")
    waiting = expiry.due(archive.entries(ledger), now=today, low=found.low, data=data)
    if expiry.raised(data, waiting, now=today, low=found.low):
        lines.append("the archive asks the person: " + expiry.question(waiting, entries=archive.entries(ledger),
                                                                       now=today, low=found.low,
                                                                       data=data)["question"])
    items = questions.items(report.judged, rows=ledger.rows(), small=report.going)
    items += questions.released_items(ledger, report.released, rows=ledger.rows())
    items += dead.seat_items(dead.dead_seats(ledger, sessions, proc_root=proc_root, data=data))
    pending = [item for item in items
               if (data["answers"].get(item.key) or {}).get("seen") != questions.fingerprint(item)]
    if pending:
        lines.append(f"{len(pending)} piece(s) of work wait on the person: they answer with /flotilla:gc in their own "
                     "session")
    store.save_pass(ledger.state_dir, ledger.repo_key, data)
    due.passed(ledger.state_dir, ledger.repo_key, now=now)
    return lines
