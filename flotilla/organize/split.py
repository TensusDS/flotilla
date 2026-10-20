"""Carrying out a confirmed plan for a tree's uncommitted edits (spec, section 7), so no edit is lost.

Each branch is committed in a tree of its own, cut from the tree's own tip and kept under flotilla's state
directory, never `.claude/worktrees/` (Claude Code's): files are copied in, `git commit` runs plainly so the
repository's hooks run, and each committed path is compared with the working file as git would store it. Only when
every branch is committed and matches do the moved paths leave the original tree. A hook that refuses, or a file
that does not match, stops it all with nothing removed. A file is never split by hunks: it goes whole to one branch.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from flotilla.organize import keep, questions

PROFILE_PATHS = (".flotilla/", ".claude/settings.json")


class Stopped(RuntimeError):
    """Carrying out stopped; nothing was removed from the original tree."""


def validate_plan(plan, dirty: list[str]) -> list[str]:
    errors = questions.plan_errors(plan)
    if errors:
        return errors
    seen_paths: set[str] = set()
    for entry in plan:
        for path in entry["paths"]:
            if path.startswith(PROFILE_PATHS):
                errors.append(f"`{path}` is onboarding's own file, never organized")
            elif path not in dirty:
                errors.append(f"`{path}` is not among the tree's uncommitted edits")
            elif path in seen_paths:
                errors.append(f"`{path}` is named twice; a file goes whole to one branch")
            seen_paths.add(path)
    return errors


def _git(tree: Path, *args: str, run) -> subprocess.CompletedProcess:
    return run(["git", "-C", str(tree), *args], capture_output=True, text=True, check=False)


def _copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() or target.is_symlink():
        target.unlink()
    if source.is_symlink():
        os.symlink(os.readlink(source), target)
    else:
        shutil.copy2(source, target)


def _drop_tree(root: Path, where: Path, branch: str, run, *, branch_too: bool) -> None:
    _git(root, "worktree", "remove", "--force", str(where), run=run)
    if branch_too:
        _git(root, "branch", "-D", branch, run=run)


def carry_out(root: Path, tree: Path, plan: list[dict], *, start: str, state: Path,
              run=subprocess.run) -> tuple[list[str], list[str]]:
    """(lines, the branches made). Raises Stopped, with the original tree untouched."""
    from flotilla.core import repo
    root, tree = Path(root), Path(tree)
    for entry in plan:
        for path in entry["paths"]:
            ignored = _git(tree, "check-ignore", "-q", "--", path, run=run).returncode == 0
            tracked = _git(tree, "ls-files", "--error-unmatch", "--", path, run=run).returncode == 0
            if ignored and not tracked:
                raise Stopped(f"`{path}` is ignored by the repository; ignored files are never added")
    home = Path(state) / "organize" / "trees" / repo.identify(root, run=run).key
    home.mkdir(parents=True, exist_ok=True)
    made, lines = [], []
    for entry in plan:
        branch, paths = entry["branch"], entry["paths"]
        where = home / branch.replace("/", "-")
        if where.exists():
            raise Stopped(f"{where} exists from an earlier run; remove it (`git worktree remove {where}`) first")
        cut = _git(root, "worktree", "add", "-q", "-b", branch, str(where), start, run=run)
        if cut.returncode != 0:
            raise Stopped(f"git could not cut `{branch}`: {cut.stderr.strip()[:200]}; nothing was removed")
        for path in paths:
            source = tree / path
            if source.exists() or source.is_symlink():
                _copy(source, where / path)
            elif (where / path).exists():
                (where / path).unlink()
        _git(where, "add", "-A", "--", *paths, run=run)
        done = _git(where, "commit", "-q", "-m", f"{branch}: edits carried from {tree.name} (flotilla organize)",
                    run=run)
        if done.returncode != 0:
            _drop_tree(root, where, branch, run, branch_too=True)
            raise Stopped(f"the commit of `{branch}` was refused (a hook, or git: "
                          f"{(done.stderr or done.stdout).strip()[:200]}); nothing was removed from {tree}")
        head = _git(where, "rev-parse", "HEAD", run=run).stdout.strip()
        differing = keep.differs(tree, head, paths, run=run)
        _drop_tree(root, where, branch, run, branch_too=False)
        if differing:
            raise Stopped(f"`{branch}` does not hold {', '.join(differing)} as the tree has it; nothing was removed")
        made.append(branch)
        lines.append(f"`{branch}` holds {len(paths)} path(s), committed with the repository's hooks")
    moved = [path for entry in plan for path in entry["paths"]]
    for path in moved:
        if _git(tree, "cat-file", "-e", f"HEAD:{path}", run=run).returncode == 0:
            _git(tree, "restore", "--staged", "--worktree", "--source=HEAD", "--", path, run=run)
        else:
            _git(tree, "rm", "-q", "--cached", "--ignore-unmatch", "--", path, run=run)
            target = tree / path
            if target.exists() or target.is_symlink():
                target.unlink()
    lines.append(f"the moved edits left {tree}; it stays on its branch")
    return lines, made
