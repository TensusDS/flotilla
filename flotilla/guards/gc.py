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
    if verb == "worktree" and args[:1] == ["remove"]:   # forced or not: git drops ignored files silently (I2)
        return "worktree-remove"
    if verb == "worktree" and args[:1] == ["prune"]:
        return "prune"
    if verb == "branch":
        deleting = _long(args, "--delete") or _short(args, "dD")
        forced = _long(args, "--force") or _short(args, "Df")
        if deleting and forced:
            return "branch"
    if verb == "update-ref" and (_long(args, "--stdin") or _long(args, "--delete") or _short(args, "d")
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


def _reaches(path: Path, seats: set[Path], archive: Path, own: set[Path] = frozenset()) -> bool:
    """What `rm -r` must not take: a seat tree itself or anything holding one, anything inside a seat tree other than
    the one the command runs in (`own`), and the archive, anything in it or holding it. Inside one's own tree is the
    session's own work (final review of gc stage 2, I3; security review of dd5d577)."""
    return (path in seats or any(path in seat.parents for seat in seats)
            or any(seat in path.parents for seat in seats - set(own))
            or path == archive or archive in path.parents or path in archive.parents)


def _unsure(word: str) -> bool:
    """An operand whose expansion may climb: a substitution or a variable, a glob that can match `..`, or a `..`
    after a glob (security review of dd5d577)."""
    parts = Path(word).parts
    globbed = next((at for at, part in enumerate(parts) if any(mark in part for mark in UNREADABLE)), None)
    return any(mark in word for mark in ("$", "`")) or any(
        part.startswith(".") and any(mark in part for mark in UNREADABLE) for part in parts) or (
        globbed is not None and any(".." in part for part in parts[globbed:])) or "{" in word


def _operands(words) -> list[str]:
    out, ended = [], False
    for word in words:
        if word == "--" and not ended:
            ended = True
        elif ended or not word.startswith("-"):
            out.append(word)
    return out


def check(segment, *, seat_trees: set[Path], fleet_branches: set[str], archive: Path,
          home: Path | None = None) -> Finding | None:
    """`home` is where the session stands (the hook's cwd): the seat tree holding it is the session's own, and a `cd`
    inside the command does not change whose tree it is. Without it no tree is the session's own."""
    kind = door(segment)
    if not kind:
        return None
    refusal = Finding(GUARD, True, REFUSAL.format(command=segment.text.strip()[:120]))
    if kind == "prune":
        return refusal
    if kind == "rm":
        base = segment.cwd.resolve() if segment.cwd else None
        stands = home.resolve() if home else None
        own = {seat for seat in seat_trees if stands is not None and (stands == seat or seat in stands.parents)}
        for word in _operands(segment.words[1:]):
            target = _resolve(base, word)
            if target is None:
                literal = []
                for part in Path(word).parts:
                    if any(mark in part for mark in UNREADABLE):
                        break
                    literal.append(part)
                prefix = _resolve(base, str(Path(*literal))) if literal else None
                # judged by where it runs whenever the prefix cannot bound it (security review of b4a4213)
                # an operand that cannot be bounded is refused anywhere in a seat tree or the archive
                if (prefix is None or _unsure(word)) and (base is None or _touches(base, [*seat_trees, archive])):
                    return refusal
                if prefix is not None and _reaches(prefix, seat_trees, archive, own):
                    return refusal
                continue
            if _reaches(target, seat_trees, archive, own):
                return refusal
        return None
    verb, args, directory = segment.git()
    if kind == "update-ref":
        if _long(args, "--stdin"):
            return refusal
        names = _operands(args)   # every operand: `-m <why>` puts the message first (security review of b4a4213)
        protected = any(ref.startswith(("refs/flotilla/", "refs/heads/fleet/")) or (
            ref.startswith("refs/heads/") and ref[len("refs/heads/"):] in fleet_branches) for ref in names)
        return refusal if protected or not names else None
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
