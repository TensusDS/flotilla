"""The gc guard: only the collector removes fleet trees, fleet branches, parked commits and the archive (gc design,
section 9).

The person's recorded answer is the authority, and `flotilla fleet down`, `flotilla fleet clean --yes` and `flotilla
gc apply` carry it out under their checks. This guard refuses the same removals typed by a session: `git worktree
remove --force` of a seat tree (or of a target it cannot name), any `git worktree prune` (the collector names missing
trees and leaves their records), a forced `git branch` deletion of a `fleet/*` branch or one the ledger names, a
`git update-ref -d` of a parked ref, and an `rm -r` whose target is a seat tree or the archive, lies inside one, or
lies above one - or holds a glob or an expansion while it runs inside or above one. A plain `git worktree remove`
passes: git itself refuses a tree with changes. Like the organize guard it stops a mistaken session, not a determined
one (decision 245); what no text guard sees is the guards' common ceiling (`flotilla guard --help`).
"""

from __future__ import annotations

import os
from pathlib import Path

from flotilla.guards import Finding

GUARD = "gc"
PARKED = "refs/flotilla/parked/"
REFUSAL = ("`{command}` removes what the collector judges; only `flotilla fleet down`, `flotilla fleet clean --yes` "
           "and `flotilla gc apply` remove fleet trees, branches and the archive, from the person's recorded answer")
UNREADABLE = ("*", "?", "[", "$", "`", "{")


def _short(words, letters: str) -> bool:
    return any(word.startswith("-") and not word.startswith("--") and set(letters) & set(word[1:]) for word in words)


def door(segment) -> str:
    """"" when the segment is no door this guard judges; else its kind."""
    if segment.program == "rm":
        words = list(segment.words[1:])
        if "--recursive" in words or _short([word for word in words if word != "--"], "rR"):
            return "rm"
        return ""
    found = segment.git()
    if found is None:
        return ""
    verb, args, _ = found
    if verb == "worktree" and args[:1] == ["remove"] and ("--force" in args or _short(args, "f")):
        return "worktree-force"
    if verb == "worktree" and args[:1] == ["prune"]:
        return "prune"
    if verb == "branch":
        deleting = "-D" in args or "--delete" in args or _short(args, "dD")
        forced = "-D" in args or "--force" in args or _short(args, "Df") or _short(args, "D")
        if deleting and forced:
            return "branch"
    if verb == "update-ref" and ("-d" in args or "--delete" in args) and any(arg.startswith(PARKED) for arg in args):
        return "parked"
    return ""


def _resolve(base: Path | None, word: str) -> Path | None:
    if any(mark in word for mark in UNREADABLE) or word == "-":
        return None
    path = Path(os.path.expanduser(word))
    if not path.is_absolute():
        if base is None:
            return None
        path = base / path
    return path.resolve()


def _touches(path: Path, guarded: list[Path]) -> bool:
    """`path` is a guarded place, lies inside one, or holds one."""
    return any(path == place or place in path.parents or path in place.parents for place in guarded)


def _operands(words) -> list[str]:
    out, ended = [], False
    for word in words:
        if word == "--" and not ended:
            ended = True
        elif ended or not word.startswith("-"):
            out.append(word)
    return out


def check(segment, *, seat_trees: set[Path], fleet_branches: set[str], archive: Path) -> Finding | None:
    kind = door(segment)
    if not kind:
        return None
    refusal = Finding(GUARD, True, REFUSAL.format(command=segment.text.strip()[:120]))
    if kind in ("prune", "parked"):
        return refusal
    if kind == "rm":
        guarded = [*seat_trees, archive]
        base = segment.cwd.resolve() if segment.cwd else None
        for word in _operands(segment.words[1:]):
            target = _resolve(base, word)
            if target is None:
                if base is None or _touches(base, guarded):
                    return refusal
                continue
            if _touches(target, guarded):
                return refusal
        return None
    verb, args, directory = segment.git()
    if kind == "branch":
        names = _operands(args)
        return refusal if any(name.startswith("fleet/") or name in fleet_branches for name in names) else None
    targets = _operands(args[1:])
    if not targets:
        return refusal
    target = _resolve(directory, targets[0])
    if target is None or target in seat_trees or _touches(target, [archive]):
        return refusal
    return None
