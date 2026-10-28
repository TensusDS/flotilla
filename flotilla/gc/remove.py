"""Removing a tree, and deleting a branch, under the checks of the gc design (section 5.5).

`git worktree remove` deletes ignored files without a word and does not check that HEAD stays reachable, so it is
the last check only for what it does check. The removal runs inside the ledger's lock (the one `tree.cut` cuts
trees under) and takes a `git worktree lock` as a mutex - it fails on a tree already locked, so a spawn's lock
wins. Under it the tree is judged again; a HEAD, a verdict or a reflog that moved aborts. Then its reflog-only
commits are parked, the lock lifted and the tree removed without `--force`. A removal git refuses still frees the
seat's branch and says why (decision 207, I6). A branch is deleted only while it points at the judged commit.
"""

from __future__ import annotations

from pathlib import Path

import os

from flotilla.gc import archive, refs
from flotilla.gc.branches import BranchVerdict
from flotilla.gc.park import ParkFailed, park
from flotilla.gc.trees import GC_LOCK, TreeVerdict, judge_tree
from flotilla.ledger import core


def _git(ledger, *args):
    return ledger.run(["git", "-C", str(ledger.root), *args], capture_output=True, text=True, check=False)


def size_of(tree) -> int:
    """The bytes under `tree`, links not followed: what removing it frees."""
    total = 0
    for here, dirs, files in os.walk(tree, followlinks=False):
        for name in files + [name for name in dirs if os.path.islink(os.path.join(here, name))]:
            try:
                total += os.lstat(os.path.join(here, name)).st_size
            except OSError:
                continue
    return total


def _keys(found) -> list[tuple[str, str]]:
    return sorted((path, digest) for path, _, digest in found)


def last_copies(tree: str, verdict: TreeVerdict) -> list[dict]:
    """Files removed on a twin a person curates (a store) or keeps (the main checkout): where the last copy is."""
    return [{"from": str(Path(tree) / path), "where": twin.where} for path, twin in verdict.twins
            if twin.kind in ("store", "checkout")]


def remove_tree(ledger, verdict: TreeVerdict, *, dropping: set[str], sessions, proc_root=None,
                spare_rows=(), also_held=(), twins=None, archive_entry: str = "", out: dict | None = None) -> list[str]:
    """Remove a tree judged removable; one line saying what happened. `also_held` are commits the same pass
    parks (a squash-shipped branch's tip): they hold what they reach, as their refs will. With `archive_entry`, a
    tree whose only obstacle is unique ignored files has them archived first (tier 2, small: told after), exactly
    the files judged, and files removed on a store's or the checkout's twin are recorded as last copies. `out`
    receives what was moved and freed: {"entry", "files", "moved", "freed", "last_copies"}."""
    tree = verdict.tree
    out = {} if out is None else out
    archiving = bool(archive_entry and verdict.archive and not verdict.reasons and not verdict.doubts)
    if not verdict.removable and not archiving:
        return []
    checking = twins.checking() if twins is not None else None
    with ledger.session():
        if verdict.lock:   # flotilla's lock of a seat no longer alive (a fleet stopped by force)
            _git(ledger, "worktree", "unlock", tree)
        if _git(ledger, "worktree", "lock", "--reason", GC_LOCK, tree).returncode != 0:
            return [f"kept {tree}: a session locked it while it was judged"]
        holders = refs.holders(ledger, dropping=dropping) + list(also_held)
        if checking is not None:
            checking.rehold(holders)

        def judge_again():
            return judge_tree(ledger, tree, holders=holders, sessions=sessions, spare_rows=spare_rows,
                              proc_root=proc_root, twins=checking)
        again = judge_again()
        why = "its HEAD moved since it was judged" if again.head != verdict.head else ""
        if not why and archiving:
            # a file the same pass archived from another tree is now an archive twin of its own: the same content
            # (rule 3, identical copies stored once; final review of gc stage 2, I7)
            # one already an archive twin when judged is no change (second review of the branch, I-3)
            before = {path for path, twin in verdict.twins if twin.kind == "archive"}
            stored = [(path, Path(twin.where).name) for path, twin in again.twins if twin.kind == "archive"]
            moved = [item for item in stored if item[0] not in before]
            if again.reasons or again.doubts or sorted(_keys(again.archive) + moved) != _keys(verdict.archive):
                why = "it changed while it was judged: " + ("; ".join(again.why) or "other files to archive")
            else:
                try:   # a file now an archive twin is put too: the object is kept once, the tree's record names it
                    entry = archive.put(ledger, entry=archive_entry, tree=tree,
                                        files=[path for path, _, _ in again.archive] + [path for path, _ in stored],
                                        tier="2",
                                        approved_by="told after: small", branch=again.branch, head=again.head,
                                        last_copies=last_copies(tree, again))
                    out.update(entry=entry.id, files=len(again.archive) + len(stored),
                               moved=sum(size for _, size, _ in again.archive))
                except archive.ArchiveFailed as err:
                    why = str(err)
                if not why:
                    again = judge_again()
        if not why:
            why = ("; ".join(again.why) if not again.removable
                   else "its reflog moved since it was judged" if set(again.park) != set(verdict.park) else "")
        stored = [path for path, twin in again.twins if twin.kind == "archive"]   # restorable by this tree's name
        if not why and archive_entry and not archiving and (last_copies(tree, again) or stored):
            try:
                archive.put(ledger, entry=archive_entry, tree=tree, files=stored, tier="1",
                            approved_by="tier 1: a twin survives", branch=again.branch, head=again.head,
                            last_copies=last_copies(tree, again))
            except archive.ArchiveFailed as err:
                why = str(err)
        out["last_copies"] = last_copies(tree, again)
        parked = []
        if not why:
            try:
                parked = park(ledger, Path(tree).name, again.park)
            except ParkFailed as err:
                why = str(err)
        _git(ledger, "worktree", "unlock", tree)
        if why:
            if verdict.lock:
                _git(ledger, "worktree", "lock", "--reason", verdict.lock, tree)
            return [f"kept {tree}: {why}"]
        out["freed"] = size_of(tree)
        done = _git(ledger, "worktree", "remove", tree)
    if done.returncode != 0:
        out["freed"] = 0
        refused = (done.stderr or done.stdout).strip()[:160]
        freed = core.free_seat_tree(ledger, "", tree) if verdict.branch else ""
        return [f"kept {tree}: git refused to remove it ({refused})" + (f"; its branch: {freed}" if freed else "")]
    return [f"removed {tree}" + (f": parked {len(parked)} commit(s)" if parked else "")]


def delete_branch(ledger, verdict: BranchVerdict) -> str:
    """Delete a branch judged deletable, parking what it alone holds first."""
    now = _git(ledger, "rev-parse", "--verify", "-q", f"refs/heads/{verdict.name}").stdout.strip()
    if now != verdict.tip:
        return f"kept branch `{verdict.name}`: it moved since it was judged"
    try:
        park(ledger, verdict.name, verdict.park)
    except ParkFailed as err:
        return f"kept branch `{verdict.name}`: {err}"
    done = _git(ledger, "update-ref", "-d", f"refs/heads/{verdict.name}", verdict.tip)
    return (f"deleted branch `{verdict.name}`" if done.returncode == 0
            else f"kept branch `{verdict.name}`: it moved since it was judged")
