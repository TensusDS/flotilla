"""Ignored files that hold a tree, by rule 1 of the gc design (section 6).

`git worktree remove` deletes ignored files without a word, so an ignored entry holds the tree unless a build or an
install makes it again. The name list is applied to the entry itself (review of the cleanup, I1: `deploy/build/
prod.env` passed for its parent). Entries are read in git's `matching` mode, so a directory shows as the entry a
pattern matched; one whose name is not in the list is judged by what is in it - regenerable when every file in it
is (twosuns: `tools/crystal3d/` held only `__pycache__`, and the folded name read as somebody's data). A directory
named for what a manifest installs is regenerable when that manifest sits beside it. Claude Code's runtime state
under `.claude/` is regenerable, except `settings.local.json`, which holds permissions a person granted - unless it
is byte-identical to the main checkout's. The project's `[gc] regenerable` adds names.

`classify` goes on with rules 2 to 5 for every file the name list does not vouch for: a byte-identical twin that
survives the pass (`twins.py`) lets it go; a file only a global or per-repository exclude ignores is the person's
(`git check-ignore -v`: no `.gitignore` the tree tracks matches it); a `.git` under an entry makes the whole entry a
nested repository, the person's; the rest is unique, bound for the archive. `holding` keeps stage 1's answer: the
entries the name list does not vouch for.
"""

from __future__ import annotations

import filecmp
import os
import stat
import subprocess
from dataclasses import dataclass, field
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


def _unvouched(tree: Path, main: Path, entry: str, profile: dict) -> list[str] | None:
    """The files of an ignored `entry` the name list does not vouch for (paths relative to the tree), or None when a
    nested repository lies in it - the person's, whatever it is called (gc design 6, rule 5)."""
    bare = entry.rstrip("/")
    own = bare.rsplit("/", 1)[-1]
    top = tree / bare
    if (top / ".git").exists():
        return None
    if own in REGENERABLE | _extra(profile) or own.endswith(REGENERABLE_SUFFIXES) or _evidence(top.parent, own):
        return []
    if not entry.endswith("/"):
        return [] if _file_regenerable(tree, main, bare, [own], CACHES | _extra(profile)) else [bare]
    vouching = CACHES | _extra(profile)
    found: list[str] = []

    def unreadable(err: OSError) -> None:   # not walked is not vouched for (second review of the branch, m-4)
        found.append(Path(err.filename).relative_to(tree).as_posix() if err.filename else bare)
    for here, dirs, files in os.walk(top, onerror=unreadable, followlinks=False):
        here_path = Path(here)
        below = here_path.relative_to(top).parts
        if ".git" in dirs or ".git" in files:
            return None
        links = [name for name in dirs if (here_path / name).is_symlink()]
        dirs[:] = [name for name in dirs if name not in links and name not in vouching
                   and not (name in EVIDENCE and _evidence(here_path, name))]   # a cache or an install is whole
        for name in sorted(files + links):
            path = f"{bare}/{'/'.join([*below, name])}"
            if not _file_regenerable(tree, main, path, [*below, name], vouching):
                found.append(path)
    return found


def _entries(tree: Path, run) -> list[str] | None:
    shown = run(["git", "-C", str(tree), "-c", "status.showUntrackedFiles=all", "status", "--porcelain", "-z",
                 "--untracked-files=all", "--ignored=matching"], capture_output=True, text=True, check=False)
    if shown.returncode != 0:
        return None
    return [item[3:] for item in shown.stdout.split("\0") if item.startswith("!! ")]


def holding(tree, *, main, profile: dict, run=subprocess.run) -> list[str] | None:
    """The ignored entries that hold `tree` (paths relative to it), or None when git cannot be asked."""
    tree, main = Path(tree), Path(main)
    entries = _entries(tree, run)
    if entries is None:
        return None
    return [entry for entry in entries if _unvouched(tree, main, entry, profile) != []]


@dataclass
class Ignored:
    twins: list = field(default_factory=list)      # (path, Twin)
    unique: list = field(default_factory=list)     # (path, size, sha256); sha256 "" when no twin was looked for
    personal: list = field(default_factory=list)   # paths only an exclude outside the project ignores
    nested: list = field(default_factory=list)     # entries holding a nested repository


def _persons(tree: Path, candidates: list[str], run) -> set[str]:
    """Which of `candidates` only a pattern from outside the project ignores: not a `.gitignore` the tree tracks."""
    if not candidates:
        return set()
    tracked = run(["git", "-C", str(tree), "ls-files", "-z", "--", ".gitignore", "*/.gitignore"],
                  capture_output=True, text=True, check=False)
    project = set(tracked.stdout.split("\0")) - {""} if tracked.returncode == 0 else set()
    shown = run(["git", "-C", str(tree), "check-ignore", "-v", "-z", "--no-index", "--stdin"],
                input="".join(f"{path}\0" for path in candidates), capture_output=True, text=True, check=False)
    fields = shown.stdout.split("\0")
    found = set()
    for at in range(0, len(fields) - 3, 4):
        source, pattern, path = fields[at], fields[at + 2], fields[at + 3]
        if source and not pattern.startswith("!") and source not in project:
            found.add(path)
    return found


def classify(tree, *, main, profile: dict, twins=None, run=subprocess.run) -> Ignored | None:
    """Every ignored file the name list does not vouch for, sorted by rules 2 to 5; None when git cannot be asked.
    `twins` is a `twins.Twins`; without it no twin is ever found."""
    from flotilla.gc import twins as twins_module
    tree, main = Path(tree), Path(main)
    entries = _entries(tree, run)
    if entries is None:
        return None
    out = Ignored()
    left: list[str] = []
    for entry in entries:
        files = _unvouched(tree, main, entry, profile)
        if files is None:
            out.nested.append(entry)
            continue
        for path in files:
            try:
                plain = stat.S_ISREG((tree / path).lstat().st_mode) or (tree / path).is_symlink()
                twin = twins.find(tree / path) if twins is not None and plain else None
            except OSError:
                plain, twin = False, None
            if not plain:   # unreadable, a pipe, a device: the person's (final review of gc stage 2, I4)
                out.personal.append(path)
            elif twin is not None:
                out.twins.append((path, twin))
            else:
                left.append(path)
    persons = _persons(tree, left, run)
    for path in left:
        if path in persons:
            out.personal.append(path)
            continue
        full = tree / path
        try:
            digest = twins_module.sha256(full, twins.cache) if twins is not None else ""
        except OSError:
            out.personal.append(path)
            continue
        out.unique.append((path, full.lstat().st_size, digest))
    return out
