"""The tree's verdict (gc design, section 5.1): a tree goes when it holds nothing that exists nowhere else. Its
branch is judged on its own (`branches.py`); a tree may go while its branch stays.

In order: alive (an open row holds it for a live session, a lock not flotilla's or naming a live session); in use
right now (an operation in progress, a session or any process working in it - none is stopped here); clean (status
with its options spelled out, nothing git is told not to look at, no stash on its branch, no open row on it); its
HEAD held by a ref that survives the pass (commits only its reflog reaches are parked, not a doubt); its ignored
files regenerable (`ignored.py`). Anything else is a doubt, named, and the tree stays.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from flotilla.fleet import leftovers
from flotilla.gc import ignored, refs

#: Row states whose work is done with: the row no longer holds the branch.
DONE = {"shipped", "closed", "released"}
#: The lock reason spawn writes on a seat's tree.
LOCK_PREFIX = "flotilla: "
#: What git leaves in a tree's git directory while an operation is in progress; `git worktree remove` ignores
#: `index.lock` (git 2.53), so the collector looks itself.
MARKERS = ("index.lock", "MERGE_HEAD", "REBASE_HEAD", "rebase-merge", "rebase-apply", "CHERRY_PICK_HEAD",
           "BISECT_LOG")


@dataclass
class TreeVerdict:
    tree: str
    branch: str          # "" for a detached tree
    head: str
    removable: bool
    reasons: list[str] = field(default_factory=list)
    park: list[str] = field(default_factory=list)    # reflog-only commits, parked before the tree goes
    notes: list[str] = field(default_factory=list)   # said in the report, hold nothing
    lock: str = ""                                    # the lock reason when flotilla's lock of a gone seat is on it


def _git(where, *args, run):
    return run(["git", "-C", str(where), *args], capture_output=True, text=True, check=False)


def locks(ledger) -> dict[Path, str]:
    """Tree -> its lock reason, for every locked worktree of the project."""
    listing = _git(ledger.root, "worktree", "list", "--porcelain", run=ledger.run).stdout
    found, current = {}, None
    for line in listing.splitlines():
        if line.startswith("worktree "):
            current = Path(line[len("worktree "):]).resolve()
        elif line.startswith("locked") and current is not None:
            found[current] = line[len("locked"):].strip()
    return found


def _alive(ledger, here: Path, sessions, spare_rows, lock: str | None) -> str:
    live = {item.name for item in sessions if item.name}
    # a seat works in its tree from the main checkout (decision 208): an open row for a live session is its tree
    for row in ledger.rows().values():
        if row.tree and row.is_open and row.owner in live and row.id not in spare_rows \
                and Path(row.tree).resolve() == here:
            return f"{row.owner} is alive and works in it"
    for item in sessions:
        if item.name and item.cwd and (Path(item.cwd).resolve() == here or here in Path(item.cwd).resolve().parents):
            return f"{item.name} is alive and works in it"
    if lock is not None:
        holder = lock[len(LOCK_PREFIX):] if lock.startswith(LOCK_PREFIX) else None
        if holder is None:
            return f"locked by someone else ({lock or 'no reason given'})"
        if holder in live:
            return f"{holder} is alive and holds its lock"
    return ""


def _in_use(tree: Path, proc_root, notes: list[str], *, run) -> str:
    gitdir = _git(tree, "rev-parse", "--absolute-git-dir", run=run).stdout.strip()
    if gitdir:
        for marker in MARKERS:
            if (Path(gitdir) / marker).exists():
                return f"an operation is in progress in it ({marker})"
    working = leftovers.working_in(tree, proc_root=proc_root)
    if working is None:
        notes.append("processes working in trees were not looked for: no /proc on this machine")
    elif working:
        pid, command = working[0]
        more = f" and {len(working) - 1} more" if len(working) > 1 else ""
        return f"a process works in it: {pid} {command[:60]}{more}"
    return ""


def _unclean(ledger, tree: Path, branch: str, spare_rows) -> list[str] | None:
    run = ledger.run
    # options spelled out: a person's status.showUntrackedFiles=no hid untracked work (review of the cleanup, C1)
    status = _git(tree, "-c", "status.showUntrackedFiles=all", "-c", "diff.ignoreSubmodules=none", "status",
                  "--porcelain", "--untracked-files=all", "--ignore-submodules=none", run=run)
    if status.returncode != 0:
        return None
    reasons = []
    hidden = [line[2:] for line in _git(tree, "ls-files", "-v", run=run).stdout.splitlines()
              if line[:1] == "S" or line[:1].islower()]
    if hidden:   # skip-worktree or assume-unchanged: an edit there shows in no status (review of the cleanup, I2)
        reasons.append(f"files git is told not to look at: {', '.join(hidden[:3])}")
    # a populated submodule is a nested repository: its edits can hide from status (`ignore = all`) and its commits
    # live in this tree's own git directory, so it is the person's to decide (security review of gc stage 1)
    nested = [line.split("\t", 1)[1] for line in _git(tree, "ls-files", "--stage", run=run).stdout.splitlines()
              if line.startswith("160000 ") and "\t" in line]
    populated = [path for path in nested if (tree / path / ".git").exists()]
    if populated:
        reasons.append(f"a populated submodule: {', '.join(populated[:3])}")
    changed = [line[3:] for line in status.stdout.splitlines() if line]
    if changed:
        reasons.append(f"{len(changed)} uncommitted or untracked file(s): {', '.join(changed[:3])}")
    if branch:
        stashes = _git(ledger.root, "stash", "list", "--format=%gs", run=run).stdout.splitlines()
        if any(line.startswith((f"WIP on {branch}:", f"On {branch}:")) for line in stashes):
            reasons.append(f"a stash was made on `{branch}`")
        held = [row for row in ledger.rows().values()
                if row.branch == branch and row.is_open and row.state not in DONE and row.id not in spare_rows]
        if held:
            reasons.append(f"open row {held[0].id} ({held[0].state}) on `{branch}`")
    return reasons


def judge_tree(ledger, tree, *, holders: list[str], sessions, spare_rows=(), proc_root=None) -> TreeVerdict:
    """Whether `tree` can go, judged against `holders` - the refs that survive this pass."""
    tree = Path(tree)
    run = ledger.run
    head = _git(tree, "symbolic-ref", "-q", "--short", "HEAD", run=run)
    branch = head.stdout.strip() if head.returncode == 0 else ""
    tip = _git(tree, "rev-parse", "--verify", "-q", "HEAD", run=run).stdout.strip()
    verdict = TreeVerdict(str(tree), branch, tip, False)
    if not tip:
        verdict.reasons.append(f"git could not read {tree}")
        return verdict
    here = tree.resolve()
    lock = locks(ledger).get(here)
    alive = _alive(ledger, here, list(sessions), set(spare_rows), lock)
    if alive:
        verdict.reasons.append(alive)
        return verdict
    verdict.lock = lock or ""
    busy = _in_use(tree, proc_root, verdict.notes, run=run)
    if busy:
        verdict.reasons.append(busy)
        return verdict
    unclean = _unclean(ledger, tree, branch, set(spare_rows))
    if unclean is None:
        verdict.reasons.append(f"git could not read the state of {tree}")
        return verdict
    verdict.reasons += unclean
    if refs.held(ledger, tip, holders):
        logged = _git(tree, "reflog", "show", "--format=%H", "HEAD", run=run).stdout.split()
        verdict.park = refs.unheld(ledger, logged, holders)
    else:
        verdict.reasons.append(f"it is the only holder of {tip[:7]}: no ref that stays reaches it")
    kept = ignored.holding(tree, main=ledger.root, profile=ledger.profile, run=run)
    if kept is None:
        verdict.reasons.append(f"git could not list the ignored files of {tree}")
    elif kept:
        verdict.reasons.append(f"ignored files that nothing makes again: {', '.join(kept[:3])}")
    verdict.removable = not verdict.reasons
    return verdict
