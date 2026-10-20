"""Taking work from before flotilla into the ledger (spec 2026-10-19-organize-design, section 5).

The person's move, made through `flotilla organize apply` from their recorded answer, never a post's: the onboarding
session holds no post yet, the orchestrator may not claim, and post templates are never rewritten in a project
already onboarded. So it is checked as `approve` is, by who stands behind the call.

The row opens as work in progress under a reserved owner no census lists, so it reads as orphaned and the orchestrator
gives it to a seat like any orphaned row; the seat runs the handover tiers. It is pinned to the post its review
follows, and records origin's trunk at the moment it came in: accounting starts there, not at its base, which may
lie months back. What the answer authorized outside the ledger - the retarget of its pull request, the lower pull
requests to close once it ships - is recorded on it, and `queue` and `ship` carry it out.
"""

from __future__ import annotations

from flotilla.ledger import gitq
from flotilla.ledger.actor import Actor
from flotilla.ledger.core import Ledger, check_claim, link_ids, next_row_id
from flotilla.ledger.errors import MoveRefused
from flotilla.ledger.model import Row

#: The owner of taken-in work until a seat adopts it: parentheses keep it out of every post's name pattern.
TAKEN_IN = "(taken in)"


def take_in(ledger: Ledger, branch: str, *, base: str, pr: int | None = None, requires=(), after=(),
            retarget: str = "", closes=(), judged_as: str = "main") -> Row:
    from flotilla.core import caller
    refused = caller.person_refusal("takes work into the ledger")
    if refused:
        raise MoveRefused(refused)
    if not base:
        raise MoveRefused(f"taking `{branch}` in needs its base as a commit; nothing is taken in with an empty one")
    adopted_at = gitq.resolve(ledger.root, f"refs/remotes/origin/{ledger.trunk}", run=ledger.run)
    if not adopted_at:
        raise MoveRefused(f"git could not resolve origin's `{ledger.trunk}`; fetch it first, so the row records "
                          "where accounting starts")
    person = Actor("the person", None, "person", "the person")
    with ledger.session() as s:
        ids = check_claim(s.rows, branch, requires=requires)
        later = link_ids(s.rows, after, "--after")
        fields = {"branch": branch, "owner": TAKEN_IN, "tree": "", "base": base, "ref": "", "requires": ids,
                  "pr": str(pr) if pr is not None else "", "judged_as": judged_as, "adopted_at": adopted_at,
                  "retarget": retarget, "closes": [int(number) for number in closes]}
        if later:
            fields["after"] = later
        return s.append(person, next_row_id(s.rows), "take-in", "claimed", fields=fields)
