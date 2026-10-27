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
EVIDENCE = {"vendor": ("composer.lock", "go.mod"), "Pods": ("Podfile.lock",), "bower_components": ("bower.json",),
            ".bundle": ("Gemfile.lock",), "elm-stuff": ("elm.json",)}
CLAUDE = ".claude/"
GRANTED = ".claude/settings.local.json"


def _names(profile: dict) -> set[str]:
    extra = (profile.get("gc") or {}).get("regenerable") or []
    return REGENERABLE | {str(item) for item in extra if isinstance(item, str)}


def _by_name(parts: list[str], names: set[str]) -> bool:
    return any(part in names for part in parts) or (bool(parts) and parts[-1].endswith(REGENERABLE_SUFFIXES))


def _file_regenerable(tree: Path, main: Path, path: str, within: list[str], names: set[str]) -> bool:
    """`path` is relative to the tree; `within` its components below the entry, the only ones that vouch."""
    if path == GRANTED:
        mine, theirs = tree / GRANTED, main / GRANTED
        return theirs.is_file() and filecmp.cmp(mine, theirs, shallow=False)
    return path.startswith(CLAUDE) or _by_name(within, names)


def _evidence(tree: Path, entry: str) -> bool:
    path = Path(entry.rstrip("/"))
    beside = tree / path.parent
    return any((beside / name).is_file() for name in EVIDENCE.get(path.name, ()))


def _entry_regenerable(tree: Path, main: Path, entry: str, names: set[str]) -> bool:
    bare = entry.rstrip("/")
    own = bare.rsplit("/", 1)[-1]
    if _by_name([own], names) or _evidence(tree, entry):
        return True
    if not entry.endswith("/"):
        return _file_regenerable(tree, main, bare, [own], names)
    top = tree / bare
    for here, dirs, files in os.walk(top, followlinks=False):
        below = Path(here).relative_to(top).parts
        links = [name for name in dirs if (Path(here) / name).is_symlink()]
        dirs[:] = [name for name in dirs if name not in links and name not in names]   # a regenerable dir is whole
        for name in files + links:
            path = f"{bare}/{'/'.join([*below, name])}"
            if not _file_regenerable(tree, main, path, [own, *below, name][1:], names):
                return False
    return True


def holding(tree, *, main, profile: dict, run=subprocess.run) -> list[str] | None:
    """The ignored entries that hold `tree` (paths relative to it), or None when git cannot be asked."""
    tree, main = Path(tree), Path(main)
    shown = run(["git", "-C", str(tree), "-c", "status.showUntrackedFiles=all", "status", "--porcelain", "-z",
                 "--untracked-files=all", "--ignored=matching"], capture_output=True, text=True, check=False)
    if shown.returncode != 0:
        return None
    names = _names(profile)
    entries = [item[3:] for item in shown.stdout.split("\0") if item.startswith("!! ")]
    return [entry for entry in entries if not _entry_regenerable(tree, main, entry, names)]
