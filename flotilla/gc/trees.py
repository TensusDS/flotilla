"""The tree's verdict (gc design, section 5.1): a tree goes when it holds nothing that exists nowhere else. Its
branch is judged on its own (`branches.py`); a tree may go while its branch stays.

In order: alive (an open row holds it for a live session, a lock not flotilla's or naming a live session); in use
right now (an operation in progress, a session or any process working in it - none is stopped here); clean (status
with its options spelled out, nothing git is told not to look at, no stash on its branch, no open row on it); its
HEAD held by a ref that survives the pass (commits only its reflog reaches are parked, not a doubt); its ignored
files regenerable or with a twin that survives (`ignored.py`, `twins.py`).

What would break something is a reason (`reasons`): the tree stays and nobody is asked. What only the person can
settle is a doubt (`doubts`: edits, hidden files, a submodule, the sole holder of HEAD, the person's excluded files,
a nested repository); unique ignored files are bound for the archive (`archive`). A tree with no reason but doubts or
archive files is put to the person (gc design 3 and 9; `asks`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from flotilla.fleet import leftovers
from flotilla.gc import ignored, refs
from flotilla.gc.twins import main_checkout

#: Row states whose work is done with: the row no longer holds the branch.
DONE = {"shipped", "closed", "released"}
#: The lock reason spawn writes on a seat's tree.
LOCK_PREFIX = "flotilla: "
#: The lock the collector itself holds on a tree while it removes it: its own, never a seat being raised.
GC_LOCK = "flotilla: gc"
#: What git leaves in a tree's git directory while an operation is in progress; `git worktree remove` ignores
#: `index.lock` (git 2.53), so the collector looks itself.
MARKERS = ("index.lock", "MERGE_HEAD", "REBASE_HEAD", "rebase-merge", "rebase-apply", "CHERRY_PICK_HEAD",
           "BISECT_LOG")


@dataclass(frozen=True)
class Doubt:
    kind: str    # edits, hidden, submodule, sole, personal, nested
    text: str
    paths: tuple[str, ...] = ()


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
    doubts: list[Doubt] = field(default_factory=list)   # what only the person settles
    archive: list = field(default_factory=list)         # (path, size, sha256) of unique ignored files
    twins: list = field(default_factory=list)           # (path, Twin) of ignored files with a surviving copy

    @property
    def why(self) -> list[str]:
        """Every reason the tree stays, in words: what breaks, what the person settles, what the archive would take."""
        out = self.reasons + [doubt.text for doubt in self.doubts]
        if self.archive:
            out.append("unique ignored files bound for the archive: "
                       + ", ".join(path for path, _, _ in self.archive[:3])
                       + (f" and {len(self.archive) - 3} more" if len(self.archive) > 3 else ""))
        return out


def asks(verdict: TreeVerdict) -> bool:
    """Whether the tree is the person's to settle: nothing breaks, but a doubt or archive files stand in the way."""
    return not verdict.reasons and bool(verdict.doubts or verdict.archive)


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


def _raising(ledger, holder: str, gitdir: str) -> str:
    """Whether flotilla's lock names a seat spawn may still be raising: spawn adds and locks the tree before it
    reserves the row, and asks the census in between (final review of gc stage 1, I3)."""
    import time

    from flotilla.fleet.spawn import LAUNCH_WINDOW, _moment
    rows = [row for row in ledger.rows().values() if row.owner == holder]
    if not rows:
        try:
            age = time.time() - (Path(gitdir) / "locked").stat().st_mtime
        except OSError:
            return ""
        return f"a seat is being raised in it (its lock is {int(age)} s old and no row names it yet)" \
            if age < LAUNCH_WINDOW else ""
    now = _moment(ledger.now())
    for row in rows:
        reserved = _moment((row.history[0].get("at") if row.history else "") or row.updated_at)
        if now is None or reserved is None or (now - reserved).total_seconds() < LAUNCH_WINDOW:
            return f"{holder} was reserved moments ago and may still be launching"
    return ""


def _nested(ledger, here: Path) -> str:
    listing = _git(ledger.root, "worktree", "list", "--porcelain", run=ledger.run).stdout
    inside = [Path(line[len("worktree "):]) for line in listing.splitlines() if line.startswith("worktree ")]
    found = [path for path in inside if path.resolve() != here and here in path.resolve().parents]
    return f"another worktree lies inside it: {found[0]}" if found else ""


def _alive(ledger, here: Path, sessions, spare_rows, lock: str | None, gitdir: str = "") -> str:
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
        raising = "" if lock == GC_LOCK else _raising(ledger, holder, gitdir)
        if raising:
            return raising
    return ""


def _in_use(tree: Path, gitdir: str, proc_root, notes: list[str], *, run) -> str:
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


def _unclean(ledger, tree: Path, branch: str, spare_rows) -> tuple[list[str], list[Doubt]] | None:
    run = ledger.run
    # options spelled out: a person's status.showUntrackedFiles=no hid untracked work (review of the cleanup, C1)
    # -z: without it porcelain quotes a name with a space or a non-ASCII letter, and a quoted name is no path
    # (final review of gc stage 2, C1); a rename names both its paths
    status = _git(tree, "-c", "status.showUntrackedFiles=all", "-c", "diff.ignoreSubmodules=none", "status",
                  "--porcelain", "-z", "--untracked-files=all", "--ignore-submodules=none", run=run)
    if status.returncode != 0:
        return None
    reasons: list[str] = []
    doubts: list[Doubt] = []
    hidden = [entry[2:] for entry in _git(tree, "ls-files", "-v", "-z", run=run).stdout.split("\0")
              if entry and (entry[:1] == "S" or entry[:1].islower())]
    if hidden:   # skip-worktree or assume-unchanged: an edit there shows in no status (review of the cleanup, I2)
        doubts.append(Doubt("hidden", f"files git is told not to look at: {', '.join(hidden[:3])}",
                            tuple(hidden)))
    # a populated submodule is a nested repository: its edits can hide from status (`ignore = all`) and its commits
    # live in this tree's own git directory, so it is the person's to decide (security review of gc stage 1)
    nested = [entry.split("\t", 1)[1] for entry in _git(tree, "ls-files", "--stage", "-z", run=run).stdout.split("\0")
              if entry.startswith("160000 ") and "\t" in entry]
    populated = [path for path in nested if (tree / path / ".git").exists()]
    if populated:
        doubts.append(Doubt("submodule", f"a populated submodule: {', '.join(populated[:3])}", tuple(populated)))
    changed, entries = [], status.stdout.split("\0")
    at = 0
    while at < len(entries):
        entry = entries[at]
        at += 1
        if len(entry) < 4:
            continue
        changed.append(entry[3:])
        if "R" in entry[:2] or "C" in entry[:2]:   # the original path follows a rename or a copy
            changed.append(entries[at])
            at += 1
    repositories = [path for path in changed if path.endswith("/") and (tree / path / ".git").exists()]
    if repositories:   # an untracked nested repository: not plain files to move (final review of gc stage 2, I5)
        doubts.append(Doubt("nested", f"a nested repository: {', '.join(repositories[:3])}", tuple(repositories)))
        changed = [path for path in changed if path not in repositories]
    if changed:
        doubts.append(Doubt("edits", f"{len(changed)} uncommitted or untracked file(s): {', '.join(changed[:3])}",
                            tuple(changed)))
    if branch:
        stashes = _git(ledger.root, "stash", "list", "--format=%gs", run=run).stdout.splitlines()
        if any(line.startswith((f"WIP on {branch}:", f"On {branch}:")) for line in stashes):
            reasons.append(f"a stash was made on `{branch}`")
        held = [row for row in ledger.rows().values()
                if row.branch == branch and row.is_open and row.state not in DONE and row.id not in spare_rows]
        if held:
            reasons.append(f"open row {held[0].id} ({held[0].state}) on `{branch}`")
    return reasons, doubts


def judge_tree(ledger, tree, *, holders: list[str], sessions, spare_rows=(), proc_root=None,
               twins=None) -> TreeVerdict:
    """Whether `tree` can go, judged against `holders` - the refs that survive this pass. `twins` (a
    `twins.Twins`) lets an ignored file with a surviving copy go; without it none is found."""
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
    gitdir = _git(tree, "rev-parse", "--absolute-git-dir", run=run).stdout.strip()
    alive = _alive(ledger, here, list(sessions), set(spare_rows), lock, gitdir) or _nested(ledger, here)
    if alive:
        verdict.reasons.append(alive)
        return verdict
    verdict.lock = lock or ""
    busy = _in_use(tree, gitdir, proc_root, verdict.notes, run=run)
    if busy:
        verdict.reasons.append(busy)
        return verdict
    unclean = _unclean(ledger, tree, branch, set(spare_rows))
    if unclean is None:
        verdict.reasons.append(f"git could not read the state of {tree}")
        return verdict
    verdict.reasons += unclean[0]
    verdict.doubts += unclean[1]
    if refs.held(ledger, tip, holders):
        logged = _git(tree, "reflog", "show", "--format=%H", "HEAD", run=run).stdout.split()
        verdict.park = refs.unheld(ledger, logged, holders)
    else:
        verdict.doubts.append(Doubt("sole", f"it is the only holder of {tip[:7]}: no ref that stays reaches it",
                                    (tip,)))
    found = ignored.classify(tree, main=main_checkout(ledger), profile=ledger.profile, twins=twins, run=run)
    if found is None:
        verdict.reasons.append(f"git could not list the ignored files of {tree}")
    else:
        verdict.twins, verdict.archive = found.twins, found.unique
        if found.personal:
            verdict.doubts.append(Doubt("personal", "files only the person's own excludes ignore: "
                                        + ", ".join(found.personal[:3]), tuple(found.personal)))
        if found.nested:
            verdict.doubts.append(Doubt("nested", f"a nested repository: {', '.join(found.nested[:3])}",
                                        tuple(found.nested)))
    verdict.removable = not verdict.reasons and not verdict.doubts and not verdict.archive
    return verdict
