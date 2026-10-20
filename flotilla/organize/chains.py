"""Chains of pull requests, and which shapes each is offered (spec, section 5).

A pull request whose base is another open pull request's head stands on it; a chain is the path from the bottom one -
based on trunk, on a branch trunk already contains, or on nothing open (off trunk) - up to the top. Two pull requests
on the same one make the chain forked. The person picks the shape of a whole chain at once: separate works, one work
in parts, one work whole, or Claude works it out. Where a lower part's commits will not reach trunk under their own
ids (a squash or rebase merge, or a method the profile does not name), only one work whole is offered; a forked or an
off-trunk chain is offered no shape - ignore or own words.
"""

from __future__ import annotations

from flotilla.organize.github import Pull

SHAPES = ("separate", "parts", "whole", "claude")
KEEPS_IDS = ("merge",)


def _by_base(pulls: list[Pull]) -> dict[str, list[Pull]]:
    found: dict[str, list[Pull]] = {}
    for item in sorted(pulls, key=lambda item: item.number):
        found.setdefault(item.base, []).append(item)
    return found


def chains(pulls: list[Pull], trunk: str, *, trunk_contains) -> tuple[list[list[Pull]], list[Pull], list[str]]:
    """(chains bottom first, pull requests standing alone, notes). `trunk_contains(branch)` says whether a base
    branch's tip is already in trunk."""
    heads = {item.branch for item in pulls if not item.fork}
    on = _by_base([item for item in pulls if not item.fork])
    bottoms = [item for item in sorted(pulls, key=lambda item: item.number)
               if not item.fork and item.base not in heads]
    found, used = [], set()
    for bottom in bottoms:
        chain, here = [bottom], bottom
        while on.get(here.branch):
            here = on[here.branch][0]   # a fork follows the lowest number; the chain is marked forked
            if here.number in used or here in chain:
                break
            chain.append(here)
        if len(chain) > 1:
            found.append(chain)
            used.update(item.number for item in chain)
    singles = [item for item in sorted(pulls, key=lambda item: item.number)
               if item.number not in used and not (item.base in heads and not item.fork)]
    left = [item for item in pulls if item.number not in used and item not in singles]
    notes = [f"PR #{item.number} stands on a pull request outside every chain read; it is listed, not offered"
             for item in sorted(left, key=lambda item: item.number)]
    return found, singles, notes


def is_forked(chain: list[Pull], pulls: list[Pull]) -> bool:
    on = _by_base([item for item in pulls if not item.fork])
    return any(len(on.get(item.branch, [])) > 1 for item in chain)


def bottom_base_in_trunk(chain: list[Pull], trunk: str, *, trunk_contains) -> bool:
    return chain[0].base != trunk and bool(trunk_contains(chain[0].base))


def is_off_trunk(chain: list[Pull], trunk: str, *, trunk_contains) -> bool:
    return chain[0].base != trunk and not trunk_contains(chain[0].base)


def offered(chain: list[Pull], *, method: str, forked: bool, off_trunk: bool) -> list[str]:
    if forked or off_trunk:
        return []
    if method not in KEEPS_IDS:
        return ["whole"]
    return list(SHAPES)
