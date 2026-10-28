"""The gc guard: only the collector removes fleet trees, fleet branches, parked commits and the archive (gc design,
section 9).

The person's recorded answer is the authority, and `flotilla fleet down`, `flotilla fleet clean --yes` and `flotilla
gc apply` carry it out under their checks. This guard refuses the same removals typed by a session: `git worktree
remove --force` of a seat tree (or of a target it cannot name), any `git worktree prune` (the collector names missing
trees and leaves their records), a forced `git branch` deletion of a `fleet/*` branch or one the ledger names, a
`git update-ref -d` of a parked ref, and an `rm -r` whose target is a seat tree or the archive, lies inside one, or
lies above one - or holds a glob or an expansion while it runs inside or above one. A plain `git worktree remove`
passes: git itself refuses a tree with changes. The guard reads what the tools read (security review of 7590fff):
an abbreviated long option counts as the option, a worktree named by git's path suffix is that worktree, a glob's
literal prefix is judged as a path, and `git update-ref` that deletes - `-d`, a zero value, or `--stdin`, whose
operations no text shows - of a fleet branch or a `refs/flotilla/` ref is refused. Like the organize guard it stops
a mistaken session, not a determined one (decision 245); what no text guard sees is the guards' common ceiling
(`flotilla guard --help`).
"""

from __future__ import annotations

import os
from pathlib import Path

from flotilla.guards import Finding

GUARD = "gc"
REFUSAL = ("`{command}` removes what the collector judges; only `flotilla fleet down`, `flotilla fleet clean --yes` "
           "and `flotilla gc apply` remove fleet trees, branches and the archive, from the person's recorded answer")
UNREADABLE = ("*", "?", "[", "$", "`", "{")


def _short(words, letters: str) -> bool:
    return any(word.startswith("-") and not word.startswith("--") and set(letters) & set(word[1:]) for word in words)


def _long(words, name: str) -> bool:
    """`name` (`--force`) or any prefix of it the tools accept (`--forc`), with or without `=value`."""
    return any(len(head) >= 3 and head.startswith("--") and name.startswith(head)
               for head in (word.split("=", 1)[0] for word in words))


def _zero(word: str) -> bool:
    return len(word) in (40, 64) and set(word) == {"0"}


def door(segment) -> str:
    """"" when the segment is no door this guard judges; else its kind."""
    if segment.program == "rm":
        words = list(segment.words[1:])
        if _long(words, "--recursive") or _short([word for word in words if word != "--"], "rR"):
            return "rm"
        return ""
    found = segment.git()
    if found is None:
        return ""
    verb, args, _ = found
    if verb == "worktree" and args[:1] == ["remove"] and (_long(args, "--force") or _short(args, "f")):
        return "worktree-force"
    if verb == "worktree" and args[:1] == ["prune"]:
        return "prune"
    if verb == "branch":
        deleting = _long(args, "--delete") or _short(args, "dD")
        forced = _long(args, "--force") or _short(args, "Df")
        if deleting and forced:
            return "branch"
    if verb == "update-ref" and ("--stdin" in args or _long(args, "--delete") or _short(args, "d")
                                 or any(_zero(arg) for arg in args)):
        return "update-ref"
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
    if kind == "prune":
        return refusal
    if kind == "rm":
        guarded = [*seat_trees, archive]
        base = segment.cwd.resolve() if segment.cwd else None
        for word in _operands(segment.words[1:]):
            target = _resolve(base, word)
            if target is None:
                literal = []
                for part in Path(word).parts:
                    if any(mark in part for mark in UNREADABLE):
                        break
                    literal.append(part)
                prefix = _resolve(base, str(Path(*literal))) if literal else None
                if prefix is None:   # nothing of it can be named: judged by where it runs
                    if base is None or _touches(base, guarded):
                        return refusal
                elif _touches(prefix, guarded):
                    return refusal
                continue
            if _touches(target, guarded):
                return refusal
        return None
    verb, args, directory = segment.git()
    if kind == "update-ref":
        if "--stdin" in args:
            return refusal
        names = _operands(args)
        ref = names[0] if names else ""
        protected = ref.startswith(("refs/flotilla/", "refs/heads/fleet/")) or (
            ref.startswith("refs/heads/") and ref[len("refs/heads/"):] in fleet_branches)
        return refusal if protected or not ref else None
    if kind == "branch":
        names = _operands(args)
        return refusal if any(name.startswith("fleet/") or name in fleet_branches for name in names) else None
    targets = _operands(args[1:])
    if not targets:
        return refusal
    target = _resolve(directory, targets[0])
    tail = "/" + targets[0].strip("/")
    if target is None or target in seat_trees or _touches(target, [archive]) or any(
            str(seat).endswith(tail) for seat in seat_trees):   # git also finds a worktree by its path's suffix
        return refusal
    return None
