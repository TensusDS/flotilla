"""Keeping what could be lost before anything moves (spec, sections 6, 7 and 10).

Kept commits live under `refs/flotilla/<kind>/<sha>`, outside `refs/heads`, so no later walk finds them as work; each
ref is named by the commit it keeps and written create-only, so no run ever overwrites another's.
"""

from __future__ import annotations

import datetime as dt
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

ZERO = "0" * 40
#: Kept commits are flotilla's, not the person's: an identity of their own, so a repository without one still keeps.
IDENTITY = ("-c", "user.name=flotilla organize", "-c", "user.email=flotilla@localhost")


def _git(tree: Path, *args: str, run, env=None) -> str:
    done = run(["git", "-C", str(tree), *args], capture_output=True, text=True, check=False,
               **({"env": env} if env is not None else {}))
    if done.returncode != 0:
        raise RuntimeError(f"git {args[0]} failed: {(done.stderr or done.stdout).strip()[:200]}")
    return done.stdout.strip()


def keep_all(tree: Path, run=subprocess.run) -> str:
    """One commit holding the whole dirty state of a tree - its working files, untracked ones included, with the
    index as its second parent - made without touching the tree or the index. `git stash create` will not do: it
    keeps no untracked file, and in git 2.53 `stash create -u` takes `-u` as the message."""
    index = Path(_git(tree, "rev-parse", "--path-format=absolute", "--git-path", "index", run=run))
    head = _git(tree, "rev-parse", "HEAD", run=run)
    staged_tree = _git(tree, "write-tree", run=run)
    with tempfile.TemporaryDirectory(prefix="flotilla-keep-") as scratch:
        temporary = Path(scratch) / "index"
        if index.exists():
            shutil.copyfile(index, temporary)
        env = {**os.environ, "GIT_INDEX_FILE": str(temporary)}
        _git(tree, "add", "-A", run=run, env=env)
        working_tree = _git(tree, "write-tree", run=run, env=env)
    staged = _git(tree, *IDENTITY, "commit-tree", staged_tree, "-p", head, "-m", "flotilla organize: the index",
                  run=run)
    when = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    return _git(tree, *IDENTITY, "commit-tree", working_tree, "-p", head, "-p", staged, "-m",
                f"flotilla organize: kept {when}", run=run)


def differs(tree: Path, commit: str, paths: list[str], run=subprocess.run) -> list[str]:
    """The paths whose working file, stored as git would store it (filters and line-ending rules applied), is not
    the blob `commit` holds - or that one side has and the other has not."""
    out = []
    for path in paths:
        stored = run(["git", "-C", str(tree), "rev-parse", "--verify", "--quiet", f"{commit}:{path}"],
                     capture_output=True, text=True, check=False)
        here = Path(tree) / path
        if not here.exists() and not here.is_symlink():
            if stored.returncode == 0:
                out.append(path)
            continue
        hashed = run(["git", "-C", str(tree), "hash-object", f"--path={path}", "--", path], capture_output=True,
                     text=True, check=False)
        if stored.returncode != 0 or hashed.returncode != 0 or hashed.stdout.strip() != stored.stdout.strip():
            out.append(path)
    return out


def create_ref(root: Path, kind: str, sha: str, run=subprocess.run) -> str:
    ref = f"refs/flotilla/{kind}/{sha}"
    done = run(["git", "-C", str(root), "update-ref", ref, sha, ZERO], capture_output=True, text=True, check=False)
    if done.returncode != 0:
        raise RuntimeError(f"git would not write {ref} (it may exist already): {done.stderr.strip()[:200]}")
    return ref
