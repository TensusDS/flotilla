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

from flotilla.gc import refs
from flotilla.gc.branches import BranchVerdict
from flotilla.gc.park import ParkFailed, park
from flotilla.gc.trees import GC_LOCK, TreeVerdict, judge_tree
from flotilla.ledger import core


def _git(ledger, *args):
    return ledger.run(["git", "-C", str(ledger.root), *args], capture_output=True, text=True, check=False)


def remove_tree(ledger, verdict: TreeVerdict, *, dropping: set[str], sessions, proc_root=None,
                spare_rows=(), also_held=()) -> list[str]:
    """Remove a tree judged removable; one line saying what happened. `also_held` are commits the same pass
    parks (a squash-shipped branch's tip): they hold what they reach, as their refs will."""
    tree = verdict.tree
    if not verdict.removable:
        return []
    with ledger.session():
        if verdict.lock:   # flotilla's lock of a seat no longer alive (a fleet stopped by force)
            _git(ledger, "worktree", "unlock", tree)
        if _git(ledger, "worktree", "lock", "--reason", GC_LOCK, tree).returncode != 0:
            return [f"kept {tree}: a session locked it while it was judged"]
        again = judge_tree(ledger, tree, holders=refs.holders(ledger, dropping=dropping) + list(also_held),
                           sessions=sessions,
                           spare_rows=spare_rows, proc_root=proc_root)
        why = ("its HEAD moved since it was judged" if again.head != verdict.head
               else "; ".join(again.reasons) if not again.removable
               else "its reflog moved since it was judged" if set(again.park) != set(verdict.park) else "")
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
        done = _git(ledger, "worktree", "remove", tree)
    if done.returncode != 0:
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
