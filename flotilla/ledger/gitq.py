"""Questions to git. Every answer is asked of git, never rebuilt from a record.

A question git could not answer returns None: the third outcome between yes and no. Callers treat it as unknown
and refuse, because "git could not say" read as "different" or "same" is exactly the silent error a ledger exists
to prevent.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def _git(root: Path, *args: str, run=subprocess.run) -> subprocess.CompletedProcess:
    return run(["git", "-C", str(root), *args], capture_output=True, text=True, check=False)


def resolve(root: Path, rev: str, run=subprocess.run) -> str | None:
    if not rev:
        return None
    done = _git(root, "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}", run=run)
    value = done.stdout.strip()
    return value if done.returncode == 0 and value else None


def files_of(root: Path, rev: str, run=subprocess.run) -> str | None:
    """The id of the tree of files a commit carries: two commits with the same files have the same one."""
    done = _git(root, "rev-parse", "--verify", "--quiet", f"{rev}^{{tree}}", run=run)
    value = done.stdout.strip()
    return value if done.returncode == 0 and value else None


def same_revision(root: Path, a: str, b: str, run=subprocess.run) -> bool | None:
    first, second = resolve(root, a, run=run), resolve(root, b, run=run)
    if first is None or second is None:
        return None
    return first == second


def differs_only_in(root: Path, a: str, b: str, prefix: str, run=subprocess.run) -> bool | None:
    """Whether two revisions differ, and only in files under `prefix` (a directory, ending in "/"); None when git
    cannot say."""
    # --no-renames: a rename out of the product into `prefix` must list the name it left, not only where it went
    done = run(["git", "-C", str(root), "diff", "--no-renames", "--no-ext-diff", "--name-only", "-z", a, b],
               capture_output=True, text=True, check=False)
    if done.returncode != 0:
        return None
    names = [name for name in done.stdout.split("\0") if name]
    return bool(names) and all(name.startswith(prefix) for name in names)


def branch_tip(root: Path, branch: str, run=subprocess.run) -> str | None:
    return resolve(root, f"refs/heads/{branch}", run=run)


def trunk_ref(root: Path, trunk: str, run=subprocess.run) -> str:
    return f"origin/{trunk}" if resolve(root, f"refs/remotes/origin/{trunk}", run=run) else trunk



def merges_cleanly(root: Path, a: str, b: str, run=subprocess.run) -> bool | None:
    """Whether git merges `a` and `b` without a conflict; None when git cannot say (an old git, an unknown ref)."""
    done = _git(root, "merge-tree", "--write-tree", a, b, run=run)
    return {0: True, 1: False}.get(done.returncode)

def fork_point(root: Path, branch: str, trunk: str, run=subprocess.run) -> str | None:
    done = _git(root, "merge-base", trunk_ref(root, trunk, run=run), f"refs/heads/{branch}", run=run)
    return (done.stdout.strip() or None) if done.returncode == 0 else None


def is_ancestor(root: Path, a: str, b: str, run=subprocess.run) -> bool | None:
    done = _git(root, "merge-base", "--is-ancestor", a, b, run=run)
    return {0: True, 1: False}.get(done.returncode)


def on_first_parent(root: Path, sha: str, ref: str, *, run=subprocess.run, depth: int = 500) -> bool | None:
    """Whether `sha` is one of the last `depth` commits on `ref`'s first-parent line (trunk itself, not a merged-in
    side). A branch pointing there was moved to trunk, not merged into it."""
    done = _git(root, "rev-list", "--first-parent", f"--max-count={depth}", ref, run=run)
    if done.returncode != 0:
        return None
    return sha in done.stdout.split()


def patch_fingerprint(root: Path, base: str, tip: str, run=subprocess.run) -> str | None:
    """What a branch brings, by content: survives a rebase onto a new base, changes with the change."""
    diff = _git(root, "diff", "--no-color", base, tip, run=run)
    if diff.returncode != 0:
        return None
    if not diff.stdout:
        return "empty"
    # --verbatim, not --stable: --stable drops whitespace, and in Python indentation is meaning (F15)
    done = run(["git", "-C", str(root), "patch-id", "--verbatim"], input=diff.stdout, capture_output=True,
               text=True, check=False)
    words = done.stdout.split()
    return words[0] if done.returncode == 0 and words else None


def is_clean(tree: Path, run=subprocess.run) -> bool | None:
    done = _git(tree, "status", "--porcelain", run=run)
    return done.stdout.strip() == "" if done.returncode == 0 else None
