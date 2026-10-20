"""What git alone shows (spec, sections 3 and 6): branches and what they hold, commits on the local trunk origin lacks,
onboarding's own branch beside its profile commit, and tool caches.

A branch is "work" when it carries commits trunk does not; "no commits of its own" when its tip sits on trunk's own
line and no merged pull request names it; "merged" when a merged pull request names it, its tip was merged into
trunk, or every commit of it is on trunk under another id (squash and rebase merges). A branch checked out in any
worktree is never offered for deletion, whatever it holds: someone works in it.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from flotilla.ledger import gitq

CACHES = (".serena/",)
PROFILE_PATHS = (".flotilla/", ".claude/settings.json")


def _git(root: Path, *args: str, run) -> str | None:
    done = run(["git", "-C", str(root), *args], capture_output=True, text=True, check=False)
    return done.stdout if done.returncode == 0 else None


def _lines(text: str | None) -> list[str]:
    return [line for line in (text or "").splitlines() if line.strip()]


def branch_tips(root: Path, run) -> dict[str, tuple[str, bool]]:
    """{name: (tip, local)} for every local branch and every branch only origin has."""
    found: dict[str, tuple[str, bool]] = {}
    for line in _lines(_git(root, "for-each-ref", "--format=%(refname)%00%(objectname)", "refs/heads",
                            "refs/remotes/origin", run=run)):
        ref, _, sha = line.partition("\0")
        if ref.startswith("refs/heads/"):
            found[ref[len("refs/heads/"):]] = (sha, True)
        elif ref != "refs/remotes/origin/HEAD":
            name = ref[len("refs/remotes/origin/"):]
            found.setdefault(name, (sha, False))
    return found


def _merged_here(root: Path, tip: str, heads: set[str], run) -> bool:
    """Whether a merged pull request merged this tip: its head, or a head this tip is part of. A name alone proves
    nothing - a branch may be reused after its pull request merged (security review of 5196c72)."""
    return any(head == tip or gitq.is_ancestor(root, tip, head, run=run) is True for head in heads if head)


def branches(root: Path, trunk: str, *, skip: set[str], checked_out: set[str], merged_pr_heads: dict[str, set[str]],
             run=subprocess.run) -> dict[str, list[str]]:
    origin = f"refs/remotes/origin/{trunk}"
    target = origin if gitq.resolve(root, origin, run=run) else f"refs/heads/{trunk}"
    found: dict[str, list[str]] = {"work": [], "no_own": [], "merged": []}
    for name, (tip, _local) in sorted(branch_tips(root, run).items()):
        if name in skip or name == trunk:
            continue
        inside = gitq.is_ancestor(root, tip, target, run=run) is True
        if not inside:
            cherry = _lines(_git(root, "cherry", target, tip, run=run))
            squashed = bool(cherry) and all(line.startswith("-") for line in cherry)
            if squashed or _merged_here(root, tip, merged_pr_heads.get(name, set()), run):
                if name not in checked_out:
                    found["merged"].append(name)
            else:
                found["work"].append(name)
            continue
        if name in checked_out:
            continue
        if _merged_here(root, tip, merged_pr_heads.get(name, set()), run) or \
                gitq.on_first_parent(root, tip, target, run=run) is not True:
            found["merged"].append(name)
        else:
            found["no_own"].append(name)
    return found


def trunk_commits(root: Path, trunk: str, run=subprocess.run) -> list[str]:
    """Commits on the local trunk that origin's trunk lacks, oldest first."""
    if not gitq.resolve(root, f"refs/remotes/origin/{trunk}", run=run):
        return []
    return _lines(_git(root, "rev-list", "--reverse", f"refs/remotes/origin/{trunk}..refs/heads/{trunk}", run=run))


def profile_only(root: Path, sha: str, run=subprocess.run) -> bool:
    """A commit that touches nothing but the profile, its posts and the settings onboarding writes."""
    changed = _lines(_git(root, "diff-tree", "--no-commit-id", "--name-only", "-r", "--root", sha, run=run))
    return bool(changed) and all(path.startswith(PROFILE_PATHS) for path in changed)


def _adds_profile(root: Path, sha: str, run) -> bool:
    added = _git(root, "diff-tree", "--no-commit-id", "--name-only", "--diff-filter=A", "-r", sha, run=run)
    return ".flotilla/project.toml" in _lines(added)


def onboard_branch(root: Path, trunk: str, run=subprocess.run) -> dict | None:
    """The local branch above origin's trunk that holds the commit adding the profile: {"branch", "profile",
    "extra": commits above it, oldest first, "tip", "direct": the profile commit sits on origin's trunk}."""
    origin = f"refs/remotes/origin/{trunk}"
    origin_sha = gitq.resolve(root, origin, run=run)
    if not origin_sha:
        return None
    for name, (tip, local) in sorted(branch_tips(root, run).items()):
        if not local or name == trunk:
            continue
        commits = _lines(_git(root, "rev-list", "--reverse", f"{origin}..{tip}", run=run))
        profile = next((sha for sha in commits if _adds_profile(root, sha, run)), None)
        if profile is None:
            continue
        parent = gitq.resolve(root, f"{profile}^1", run=run)
        return {"branch": name, "profile": profile, "extra": commits[commits.index(profile) + 1:], "tip": tip,
                "direct": parent == origin_sha and commits[0] == profile}
    return None


def caches(tree: Path, run=subprocess.run) -> list[str]:
    """Known tools' cache directories, untracked and not ignored, in this tree."""
    listed = _lines(_git(tree, "status", "--porcelain=v1", "--untracked-files=normal", run=run))
    untracked = {line[3:] for line in listed if line.startswith("?? ")}
    return [cache for cache in CACHES if cache in untracked]


def dirty(tree: Path, run=subprocess.run) -> list[str] | None:
    """The paths with uncommitted edits in a tree - staged, unstaged, untracked - less onboarding's own files and
    tool caches; None when git cannot read the tree."""
    said = run(["git", "-C", str(tree), "status", "--porcelain=v1", "-z", "--untracked-files=all"],
               capture_output=True, text=True, check=False)
    if said.returncode != 0:
        return None
    entries = [entry for entry in said.stdout.split("\0") if entry]
    paths, skip_next, renamed_from = [], False, False
    for entry in entries:
        if skip_next:
            skip_next = False
            continue
        if renamed_from:   # the old path of the rename before it (final review, finding 4)
            renamed_from = False
            if not entry.startswith(PROFILE_PATHS) and not any(entry.startswith(cache) for cache in CACHES):
                paths.append(entry)
            continue
        status, path = entry[:2], entry[3:]
        if status[0] == "C":
            skip_next = True   # a copy's second entry is its source, which is not changed
        elif status[0] == "R":
            renamed_from = True   # a rename's second entry is its old path: the deletion half of the edit
        if path.startswith(PROFILE_PATHS) or any(path.startswith(cache) for cache in CACHES):
            continue
        paths.append(path)
    return sorted(paths)
