"""Whose yes a piece of work needs on its way to trunk (twosuns field report B1, the person's decision 2026-10-27).

Where a person authorizes merges (`merge_authorized_by = "human"`), their yes is asked of every row - unless the
profile names the paths it is for: `approve_paths` under [flow], gitignore-like patterns, `!` excluding. Then only
work that touches one of them waits for the person, and docs, credits and data ship on the sender's word, so the
questions put to the person stay few. What git cannot answer - an unknown revision, no base - asks the person:
doubt errs towards the yes, never past it.
"""

from __future__ import annotations

import re
import subprocess
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=256)
def _regex(pattern: str) -> re.Pattern:
    anchored = "/" in pattern.rstrip("/")
    body = pattern.strip("/")
    out, at = [], 0
    while at < len(body):
        if body.startswith("**/", at):
            out.append("(?:.*/)?")
            at += 3
        elif body.startswith("**", at):
            out.append(".*")
            at += 2
        elif body[at] == "*":
            out.append("[^/]*")
            at += 1
        elif body[at] == "?":
            out.append("[^/]")
            at += 1
        else:
            out.append(re.escape(body[at]))
            at += 1
    # a pattern without a slash names a file or directory anywhere, as in .gitignore; a directory covers its files
    lead = "" if anchored else "(?:.*/)?"
    return re.compile(f"{lead}{''.join(out)}(?:/.*)?")


def matches(path: str, patterns) -> bool:
    """Whether `path` falls under an include pattern and under no `!` exclude."""
    includes = [p for p in patterns if p and not p.startswith("!")]
    excludes = [p[1:] for p in patterns if p.startswith("!")]
    return (any(_regex(p).fullmatch(path) for p in includes)
            and not any(_regex(p).fullmatch(path) for p in excludes))


def _person(profile: dict) -> bool:
    return (profile.get("flow") or {}).get("merge_authorized_by") == "human"


def paths_of(profile: dict) -> list[str]:
    found = (profile.get("flow") or {}).get("approve_paths") or []
    return [str(p) for p in found] if isinstance(found, list) else []


def _names(raw: bytes) -> list[str]:
    return [name.strip(b"\n").decode("utf-8", "surrogateescape") for name in raw.split(b"\0") if name.strip(b"\n")]


def _changed(root: Path, base: str, revision: str, run, trunk: str = "", against: str = "") -> list[str] | None:
    """Every file any commit of the work touches - not the net diff: a commit that adds code and one that takes it
    back cancel in the diff, and the first can reach trunk alone. A merge counts by what it resolved (--cc); what
    `trunk` already holds is no part of the work. With `against`, also every file landing would change there
    (`against...revision`): a merge that keeps one side whole resolves nothing --cc shows, yet takes trunk's code out
    (security review of the code-paths change, 2)."""
    if not base or not revision:
        return None
    # --no-renames: a file moved out of the code paths changes them as much as one edited there
    done = run(["git", "-C", str(root), "log", "--no-renames", "--format=", "--name-only", "-z", "--cc",
                f"{base}..{revision}", *([f"^{trunk}"] if trunk else []), "--"], capture_output=True, check=False)
    if done.returncode != 0:
        return None
    names = _names(done.stdout)
    if against:
        landing = run(["git", "-C", str(root), "diff", "--no-renames", "--no-ext-diff", "--name-only", "-z",
                       f"{against}...{revision}", "--"], capture_output=True, check=False)
        if landing.returncode != 0:
            return None
        names += _names(landing.stdout)
    return list(dict.fromkeys(names))


def touched(root: Path, profile: dict, base: str, revision: str, run=subprocess.run, *,
            trunk: str = "", against: str = "") -> list[str] | None:
    """The changed files that need the person's yes, or None when git cannot say what changed."""
    names = _changed(root, base, revision, run, trunk, against)
    if names is None:
        return None
    patterns = paths_of(profile)
    return [name for name in names if matches(name, patterns)]


def needs_person(root: Path, profile: dict, base: str, revision: str, run=subprocess.run, *,
                 trunk: str = "", against: str = "") -> bool:
    """Whether the work `base..revision` (less what `trunk` holds) waits for the person's yes on its way to trunk."""
    if not _person(profile):
        return False
    if not paths_of(profile):
        return True
    found = touched(root, profile, base, revision, run=run, trunk=trunk, against=against)
    return found is None or bool(found)
