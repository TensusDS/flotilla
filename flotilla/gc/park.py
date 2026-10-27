"""Parking (gc design, section 5.4): a commit about to become unreachable - reflog-only, a squash-shipped tip, a tip
landed on a trunk origin does not hold - is pinned as `refs/flotilla/parked/<tree or branch>/<sha>` before its last
holder goes. Git would otherwise expire it silently after 30 days. Parked refs never expire and nothing here deletes
them: they go only on the person's recorded answer.
"""

from __future__ import annotations

import re

from flotilla.ledger.errors import MoveRefused

PARKED = "refs/flotilla/parked/"
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


class ParkFailed(MoveRefused):
    """Git refused a parked ref; the commit's holder is not removed."""


def _component(part: str) -> str:
    part = _UNSAFE.sub("-", part)
    while ".." in part:
        part = part.replace("..", "-")
    part = part.lstrip(".-")
    while part.endswith((".lock", ".")):
        part = part[:-5] if part.endswith(".lock") else part[:-1]
    return part


def ref_name(owner: str, sha: str) -> str:
    """The parked ref for `sha` under `owner` (a tree's or a branch's name), made legal for `git check-ref-format`."""
    parts = [part for part in (_component(item) for item in owner.split("/")) if part]
    return PARKED + "/".join(parts or ["unnamed"]) + "/" + sha


def park(ledger, owner: str, shas: list[str]) -> list[str]:
    """Pin each of `shas` under `owner`; the ref names, created now or already there at the same commit."""
    names = []
    for sha in dict.fromkeys(shas):
        name = ref_name(owner, sha)
        there = ledger.run(["git", "-C", str(ledger.root), "rev-parse", "--verify", "-q", name],
                           capture_output=True, text=True, check=False).stdout.strip()
        if there != sha:
            done = ledger.run(["git", "-C", str(ledger.root), "update-ref", name, sha, ""],
                              capture_output=True, text=True, check=False)
            if done.returncode != 0:
                raise ParkFailed(f"could not park {sha[:7]} as {name}: {(done.stderr or '').strip()[:160]}")
        names.append(name)
    return names
