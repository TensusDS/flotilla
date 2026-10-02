"""Removing a seat's tree and branch once their work is surely on trunk - and only then (decision 206).

The person's rule (2026-10-02): if there is the slightest doubt the work may not be merged, keep it; if it surely is,
remove it. `retire` and `fleet down` remove a stopped seat's tree when it is sure; `flotilla fleet clean` sweeps the
project's trees and local branches the same way, for what forced stops and older fleets left. Sure means every one
of these, each asked of git or the ledger at the moment of removal:

- nothing uncommitted or untracked in the tree, and nothing ignored but what a build or an install makes again
  (`node_modules`, `.venv`, caches): `git worktree remove` deletes ignored files without a word, and a `.env` or local
  data is exactly that;
- no file git is told not to look at (skip-worktree, assume-unchanged), whose edits no status shows;
- no stash made on its branch (a sign of work in progress; the stash itself is shared and survives), no open ledger
  row on it, no other tree holding it;
- every commit it carries is on origin's trunk as origin answers now - or, for a squash merge, the ledger SHIPPED
  exactly its tip (a ship move is checked against origin when it is made; a close alone is not);
- no commit that only the tree's or the branch's reflog reaches, since both go with it.

Status is asked with its options spelled out, so a person's `status.showUntrackedFiles=no` hides nothing. The sweep
judges only what the fleet made - trees and branches the ledger names, seat trees by their name, `fleet/*` - and
deletes a branch only while it still points at the commit that was judged.

Anything else is a doubt, named, and the tree stays. The removal is `git worktree remove` without `--force`, so git
refuses once more if something was missed. What a sweep keeps it lists with the reason; the orchestrator, who knows
the fleet's work, settles a doubt by making it sure (the work committed, handed, shipped) - never by deleting it.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

#: Ignored paths an install or a build makes again; anything else ignored is somebody's and holds the tree.
REGENERABLE = {"node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".next", ".nuxt", ".svelte-kit",
               "target", "coverage", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox", ".nox", ".cache",
               ".turbo", ".parcel-cache", ".eslintcache", ".DS_Store", ".gradle", "htmlcov"}
REGENERABLE_SUFFIXES = (".pyc", ".pyo", ".tsbuildinfo")
#: Row states whose work is done with: the row no longer holds the branch.
DONE = {"shipped", "closed", "released"}


@dataclass
class Verdict:
    tree: str
    branch: str          # "" for a detached tree
    removable: bool
    reasons: list[str] = field(default_factory=list)
    tip: str = ""        # the commit judged: the branch is deleted only while it still points here


def _git(root, *args, run=subprocess.run) -> subprocess.CompletedProcess:
    return run(["git", "-C", str(root), *args], capture_output=True, text=True, check=False)


def _regenerable(path: str) -> bool:
    """The ignored entry ITSELF is what a build or install makes; a parent's name says nothing about a file inside
    (review of the cleanup, I1: `deploy/build/prod.env` passed for its parent)."""
    name = path.rstrip("/").rsplit("/", 1)[-1]
    return name in REGENERABLE or name.endswith(REGENERABLE_SUFFIXES)


def _trunk_on_origin(ledger) -> tuple[str | None, str]:
    """(origin's trunk commit, present here) or (None, why not)."""
    trunk = (ledger.profile.get("trunk") or {}).get("branch", "main")
    listed = _git(ledger.root, "ls-remote", "origin", f"refs/heads/{trunk}", run=ledger.run)
    sha = listed.stdout.split()[0] if listed.returncode == 0 and listed.stdout.split() else ""
    if not sha:
        return None, f"origin could not be asked for its trunk `{trunk}`"
    if _git(ledger.root, "cat-file", "-e", f"{sha}^{{commit}}", run=ledger.run).returncode != 0:
        _git(ledger.root, "fetch", "-q", "origin", trunk, run=ledger.run)
        if _git(ledger.root, "cat-file", "-e", f"{sha}^{{commit}}", run=ledger.run).returncode != 0:
            return None, f"origin's trunk {sha[:7]} is not here and could not be fetched"
    return sha, ""


def _shipped_tips(ledger) -> set[str]:
    """Tips the ledger SHIPPED - a ship move is checked against origin; a close alone is not (a local flow closes
    after landing on a local trunk; review of the cleanup, I4)."""
    return {row.tip for row in ledger.rows().values()
            if row.tip and any(step.get("move") == "ship" for step in row.history)}


def _on_trunk(ledger, tip: str, branch: str, trunk_sha: str) -> bool:
    if _git(ledger.root, "merge-base", "--is-ancestor", tip, trunk_sha, run=ledger.run).returncode == 0:
        return True
    return tip in _shipped_tips(ledger)   # a squash, shipped exactly as it stands


def _reflog_only(ledger, tree: str, branch: str, trunk_sha: str) -> list[str]:
    """Commits the tree's or the branch's reflog reaches and nothing else does - no other branch, remote ref, tag,
    trunk or shipped tip. They go with the tree and the branch (review of the cleanup, I3)."""
    logged = set(_git(tree, "reflog", "show", "--format=%H", "HEAD", run=ledger.run).stdout.split())
    if branch:
        logged |= set(_git(ledger.root, "reflog", "show", "--format=%H", f"refs/heads/{branch}",
                           run=ledger.run).stdout.split())
    if not logged:
        return []
    keep_out = [f"--exclude=refs/heads/{branch}"] if branch else []
    found = _git(ledger.root, "rev-list", *sorted(logged), "--not", trunk_sha, *sorted(_shipped_tips(ledger)),
                 *keep_out, "--branches", "--remotes", "--tags", run=ledger.run)
    return found.stdout.split() if found.returncode == 0 else ["(git could not walk the reflog)"]


def judge(ledger, tree, *, spare_rows=()) -> Verdict:
    """Whether `tree` and its branch can go: removable only when nothing is in doubt. `spare_rows` are row ids that
    do not hold it (the retiring seat's own post row)."""
    tree = str(Path(tree))
    reasons: list[str] = []
    head = _git(tree, "symbolic-ref", "-q", "--short", "HEAD", run=ledger.run)
    branch = head.stdout.strip() if head.returncode == 0 else ""
    tip = _git(tree, "rev-parse", "--verify", "-q", "HEAD", run=ledger.run).stdout.strip()
    if not tip:
        return Verdict(tree, branch, False, [f"git could not read {tree}"])
    # asked with the options spelled out: a person's status.showUntrackedFiles=no hid untracked work and an ignored
    # .env alike, and `git worktree remove` reads the same setting (review of the cleanup, C1)
    status = _git(tree, "-c", "status.showUntrackedFiles=all", "status", "--porcelain", "--untracked-files=all",
                  run=ledger.run)
    shown = _git(tree, "-c", "status.showUntrackedFiles=normal", "status", "--porcelain", "--untracked-files=normal",
                 "--ignored=traditional", run=ledger.run)
    if status.returncode != 0 or shown.returncode != 0:
        return Verdict(tree, branch, False, [f"git could not read the state of {tree}"])
    changed = [line[3:] for line in status.stdout.splitlines() if line]
    ignored = [line[3:] for line in shown.stdout.splitlines() if line.startswith("!! ")]
    hidden = [line[2:] for line in _git(tree, "ls-files", "-v", run=ledger.run).stdout.splitlines()
              if line[:1] == "S" or line[:1].islower()]
    if hidden:   # skip-worktree or assume-unchanged: an edit there shows in no status (review of the cleanup, I2)
        reasons.append(f"files git is told not to look at: {', '.join(hidden[:3])}")
    if changed:
        reasons.append(f"{len(changed)} uncommitted or untracked file(s): {', '.join(changed[:3])}")
    kept = [path for path in ignored if not _regenerable(path)]
    if kept:
        reasons.append(f"ignored files that nothing makes again: {', '.join(kept[:3])}")
    if branch:
        stashes = _git(ledger.root, "stash", "list", "--format=%gs", run=ledger.run).stdout.splitlines()
        if any(line.startswith((f"WIP on {branch}:", f"On {branch}:")) for line in stashes):
            reasons.append(f"a stash was made on `{branch}`")
        held = [row for row in ledger.rows().values()
                if row.branch == branch and row.is_open and row.state not in DONE and row.id not in spare_rows]
        if held:
            reasons.append(f"open row {held[0].id} ({held[0].state}) on `{branch}`")
        here = Path(tree).resolve()
        listing = _git(ledger.root, "worktree", "list", "--porcelain", run=ledger.run).stdout
        others, current = [], None
        for line in listing.splitlines():
            if line.startswith("worktree "):
                current = Path(line[len("worktree "):]).resolve()
            elif line == f"branch refs/heads/{branch}" and current != here:
                others.append(str(current))
        if others:
            reasons.append(f"`{branch}` is checked out in {others[0]} too")
    trunk_sha, why = _trunk_on_origin(ledger)
    if trunk_sha is None:
        reasons.append(why)
    else:
        if not _on_trunk(ledger, tip, branch, trunk_sha):
            reasons.append(f"{tip[:7]} is not on origin's trunk, and no shipped row carries it")
        lost = _reflog_only(ledger, tree, branch, trunk_sha)
        if lost:
            reasons.append(f"{len(lost)} commit(s) only its reflog reaches: {', '.join(c[:7] for c in lost[:3])}")
    return Verdict(tree, branch, not reasons, reasons, tip)


def remove(ledger, verdict: Verdict) -> list[str]:
    """Remove a tree judged removable, and its branch. Never with `--force`: git's own refusal is the last check."""
    if not verdict.removable:
        return []
    done = _git(ledger.root, "worktree", "remove", verdict.tree, run=ledger.run)
    if done.returncode != 0:
        return [f"kept {verdict.tree}: git refused to remove it ({(done.stderr or done.stdout).strip()[:160]})"]
    lines = [f"removed {verdict.tree}: its work is on origin's trunk"]
    if verdict.branch:
        lines.append(_delete_branch(ledger, verdict.branch, verdict.tip))
    return lines


def _delete_branch(ledger, branch: str, tip: str) -> str:
    """Delete `branch` only while it still points at the commit that was judged: a commit made since is not judged
    (review of the cleanup, M4)."""
    done = _git(ledger.root, "update-ref", "-d", f"refs/heads/{branch}", tip, run=ledger.run)
    return (f"deleted branch `{branch}`" if done.returncode == 0
            else f"kept branch `{branch}`: it moved since it was judged")


def _trees(ledger) -> list[tuple[Path, str]]:
    """(tree, branch) for every worktree of the project but the main checkout."""
    listing = _git(ledger.root, "worktree", "list", "--porcelain", run=ledger.run).stdout
    found, current, branch, first = [], None, "", True
    for line in listing.splitlines() + [""]:
        if line.startswith("worktree "):
            current, branch = Path(line[len("worktree "):]), ""
        elif line.startswith("branch refs/heads/"):
            branch = line[len("branch refs/heads/"):]
        elif not line and current is not None:
            if not first:   # git lists the main checkout first
                found.append((current, branch))
            first, current = False, None
    return found


def _branches_without_trees(ledger, checked_out: set[str]) -> list[str]:
    trunk = (ledger.profile.get("trunk") or {}).get("branch", "main")
    listed = _git(ledger.root, "for-each-ref", "--format=%(refname:short)", "refs/heads/", run=ledger.run).stdout
    return [name for name in listed.split() if name != trunk and name not in checked_out]


def sweep_branch(ledger, name: str) -> list[str]:
    """Delete one local branch no tree holds, when its work is surely on trunk; say so either way."""
    checked_out = {branch for _, branch in _trees(ledger) if branch}
    checked_out.add(_git(ledger.root, "symbolic-ref", "-q", "--short", "HEAD", run=ledger.run).stdout.strip())
    if name in checked_out or not _git(ledger.root, "rev-parse", "--verify", "-q", name, run=ledger.run).stdout:
        return []
    trunk_sha, why = _trunk_on_origin(ledger)
    tip = _git(ledger.root, "rev-parse", "--verify", "-q", name, run=ledger.run).stdout.strip()
    held = [row for row in ledger.rows().values() if row.branch == name and row.is_open and row.state not in DONE]
    if trunk_sha is None or held or not _on_trunk(ledger, tip, name, trunk_sha):
        return [f"kept branch `{name}`: " + (why if trunk_sha is None else "open row" if held
                                            else "its commits are not on origin's trunk")]
    return [_delete_branch(ledger, name, tip)]


def _made_by_flotilla(ledger) -> tuple[set[Path], set[str]]:
    """The trees and branches the fleet made: those the ledger names, a seat's tree by its name (`<main>-<post>-<n>`)
    and its home branch (`fleet/...`). A person's own tree or branch is not the sweep's to judge, however merged
    (review of the cleanup, I5)."""
    rows = list(ledger.rows().values())
    trees = {Path(row.tree).resolve() for row in rows if row.tree}
    branches = {row.branch for row in rows if row.branch}
    main = ledger.root.resolve()
    seat = re.compile(rf"^{re.escape(main.name)}-(?:{'|'.join(map(re.escape, ledger.posts))})-\d+$") \
        if ledger.posts else None
    for tree, _ in _trees(ledger):
        if seat is not None and tree.parent.resolve() == main.parent and seat.match(tree.name):
            trees.add(tree.resolve())
    return trees, branches


def sweep(ledger, *, sessions, act: bool) -> list[str]:
    """Remove (with `act`) every tree and local branch the fleet made whose work is surely on trunk; list the rest
    with why it stays."""
    lines: list[str] = []
    trees = _trees(ledger)
    ours_trees, ours_branches = _made_by_flotilla(ledger)
    live = {item.name for item in sessions if item.name}
    # a seat works in its tree from the main checkout (`--add-dir`): the census puts it there, not in its tree, so a
    # tree an open row holds for a live session is that session's, wherever the census says it runs
    held_for = {Path(row.tree).resolve(): row.owner for row in ledger.rows().values()
                if row.tree and row.is_open and row.owner in live}
    for tree, branch in trees:
        here = tree.resolve()
        if here not in ours_trees:
            lines.append(f"kept {tree}: flotilla did not make it")
            continue
        alive = [s.name for s in sessions if s.cwd and (Path(s.cwd).resolve() == here or here in Path(s.cwd).resolve().parents)]
        if here in held_for:
            alive.insert(0, held_for[here])
        if alive:
            lines.append(f"kept {tree}: {alive[0]} is alive and works in it")
            continue
        verdict = judge(ledger, tree)
        if not verdict.removable:
            lines.append(f"kept {tree}: {'; '.join(verdict.reasons)}")
        elif act:
            lines += remove(ledger, verdict)
        else:
            lines.append(f"would remove {tree}" + (f" and `{branch}`" if branch else "") + ": its work is on trunk")
    trunk_sha, why = _trunk_on_origin(ledger)
    checked_out = {branch for _, branch in _trees(ledger) if branch}
    main_branch = _git(ledger.root, "symbolic-ref", "-q", "--short", "HEAD", run=ledger.run).stdout.strip()
    checked_out.add(main_branch)
    for name in _branches_without_trees(ledger, checked_out):
        if name not in ours_branches and not name.startswith("fleet/"):
            lines.append(f"kept branch `{name}`: flotilla did not make it")
            continue
        if trunk_sha is None:
            lines.append(f"kept branch `{name}`: {why}")
            continue
        tip = _git(ledger.root, "rev-parse", "--verify", "-q", name, run=ledger.run).stdout.strip()
        held = [row for row in ledger.rows().values() if row.branch == name and row.is_open and row.state not in DONE]
        if held:
            lines.append(f"kept branch `{name}`: open row {held[0].id} ({held[0].state})")
        elif not tip or not _on_trunk(ledger, tip, name, trunk_sha):
            lines.append(f"kept branch `{name}`: its commits are not on origin's trunk")
        elif act:
            lines.append(_delete_branch(ledger, name, tip))
        else:
            lines.append(f"would delete branch `{name}`: on origin's trunk")
    return lines or ["nothing to clean: no tree or branch beside the main checkout"]
