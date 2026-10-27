"""Ignored files that hold a tree, by rule 1 of the gc design (section 6).

`git worktree remove` deletes ignored files without a word, so an ignored entry holds the tree unless a build or an
install makes it again. The name list is applied to the entry itself (review of the cleanup, I1: `deploy/build/
prod.env` passed for its parent). Entries are read in git's `matching` mode, so a directory shows as the entry a
pattern matched; one whose name is not in the list is judged by what is in it - regenerable when every file in it
is (twosuns: `tools/crystal3d/` held only `__pycache__`, and the folded name read as somebody's data). A directory
named for what a manifest installs is regenerable when that manifest sits beside it. Claude Code's runtime state
under `.claude/` is regenerable, except `settings.local.json`, which holds permissions a person granted - unless it
is byte-identical to the main checkout's. The project's `[gc] regenerable` adds names.

Rules 2 to 5 (twins elsewhere, the archive) are stage 2; until then whatever this returns keeps the tree, named.
"""

from __future__ import annotations

import filecmp
import os
import subprocess
from pathlib import Path

#: Ignored paths an install or a build makes again; anything else ignored is somebody's and holds the tree.
REGENERABLE = {"node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".next", ".nuxt", ".svelte-kit",
               "target", "coverage", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox", ".nox", ".cache",
               ".turbo", ".parcel-cache", ".eslintcache", ".DS_Store", ".gradle", "htmlcov"}
REGENERABLE_SUFFIXES = (".pyc", ".pyo", ".tsbuildinfo")
#: A directory an install makes again when one of these files sits beside it.
EVIDENCE = {"node_modules": ("package.json",), ".venv": ("pyproject.toml", "requirements.txt", "uv.lock"),
            "vendor": ("composer.lock", "go.mod"), "Pods": ("Podfile.lock",), "bower_components": ("bower.json",),
            ".bundle": ("Gemfile.lock",), "elm-stuff": ("elm.json",)}
#: Inside a directory being walked, only these vouch for what is under them: true caches. A name like `build` or
#: `dist` there vouches for nothing - `deploy/build/prod.env` (final review of gc stage 1, C2; review of the cleanup,
#: I1).
CACHES = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
#: Claude Code's own runtime state under `.claude/`, by name (a field project excludes these). Not
#: `.claude/worktrees/` (Claude Code's own worktrees, with work in them), not agent memory, not plans: those are
#: somebody's (final review of gc stage 1, C1).
CLAUDE_RUNTIME = (".claude/scheduled_tasks.lock", ".claude/scheduled_tasks.json", ".claude/routines/.state/",
                  ".claude/checkpoints/", ".claude/mailbox/", ".claude/agent-registry.json", ".claude/first-run",
                  ".claude/assistant-daemon-state.json", ".claude/shell-snapshots/", ".claude/todos/",
                  ".claude/statsig/")
GRANTED = ".claude/settings.local.json"


def _extra(profile: dict) -> set[str]:
    found = (profile.get("gc") or {}).get("regenerable") or []
    return {str(item) for item in found if isinstance(item, str)}


def _runtime(path: str) -> bool:
    return any(path == item.rstrip("/") or (item.endswith("/") and path.startswith(item)) for item in CLAUDE_RUNTIME)


def _file_regenerable(tree: Path, main: Path, path: str, within: list[str], vouching: set[str]) -> bool:
    """`path` is relative to the tree; `within` its components below the entry, the only ones that vouch."""
    if path == GRANTED:
        mine, theirs = tree / GRANTED, main / GRANTED
        return theirs.is_file() and filecmp.cmp(mine, theirs, shallow=False)
    if _runtime(path):
        return True
    return any(part in vouching for part in within[:-1]) or (bool(within) and within[-1].endswith(
        REGENERABLE_SUFFIXES))


def _evidence(where: Path, name: str) -> bool:
    return any((where / item).is_file() for item in EVIDENCE.get(name, ()))


def _entry_regenerable(tree: Path, main: Path, entry: str, profile: dict) -> bool:
    bare = entry.rstrip("/")
    own = bare.rsplit("/", 1)[-1]
    top = tree / bare
    if (top / ".git").exists():   # a nested repository is the person's, whatever it is called (gc design 6, rule 5)
        return False
    if own in REGENERABLE | _extra(profile) or own.endswith(REGENERABLE_SUFFIXES) or _evidence(top.parent, own):
        return True
    if not entry.endswith("/"):
        return _file_regenerable(tree, main, bare, [own], CACHES | _extra(profile))
    vouching = CACHES | _extra(profile)
    for here, dirs, files in os.walk(top, followlinks=False):
        here_path = Path(here)
        below = here_path.relative_to(top).parts
        if ".git" in dirs or ".git" in files:
            return False
        links = [name for name in dirs if (here_path / name).is_symlink()]
        dirs[:] = [name for name in dirs if name not in links and name not in vouching
                   and not (name in EVIDENCE and _evidence(here_path, name))]   # a cache or an install is whole
        for name in files + links:
            path = f"{bare}/{'/'.join([*below, name])}"
            if not _file_regenerable(tree, main, path, [*below, name], vouching):
                return False
    return True


def holding(tree, *, main, profile: dict, run=subprocess.run) -> list[str] | None:
    """The ignored entries that hold `tree` (paths relative to it), or None when git cannot be asked."""
    tree, main = Path(tree), Path(main)
    shown = run(["git", "-C", str(tree), "-c", "status.showUntrackedFiles=all", "status", "--porcelain", "-z",
                 "--untracked-files=all", "--ignored=matching"], capture_output=True, text=True, check=False)
    if shown.returncode != 0:
        return None
    entries = [item[3:] for item in shown.stdout.split("\0") if item.startswith("!! ")]
    return [entry for entry in entries if not _entry_regenerable(tree, main, entry, profile)]
