"""Carrying accepted work to trunk: queue, land, ship (spec, sections 4.6, 5, 6.2-6.4).

`queue` puts reviewed work in the sender's batch: its dependencies must be delivered, the branch must still be at
the revision read, and in PR mode the named pull request must be open, for this branch, into trunk, at that
revision. Every fact about a pull request is asked of GitHub through `gh`; none is taken from the caller.
"""

from __future__ import annotations

import json

from flotilla.ledger import gitq
from flotilla.ledger.actor import Actor, require_may
from flotilla.ledger.core import Ledger
from flotilla.ledger.errors import MoveRefused
from flotilla.ledger.model import Row, blocked_by


def pr_view(ledger: Ledger, pr: int, fields: str) -> dict:
    done = ledger.run(["gh", "pr", "view", str(pr), "--json", fields], cwd=str(ledger.root), capture_output=True,
                      text=True, check=False)
    if done.returncode != 0:
        raise MoveRefused(f"could not ask GitHub about PR #{pr}: {(done.stderr or done.stdout).strip()}")
    try:
        answer = json.loads(done.stdout)
    except ValueError as err:
        raise MoveRefused(f"GitHub's answer about PR #{pr} is not JSON") from err
    if not isinstance(answer, dict):
        raise MoveRefused(f"GitHub's answer about PR #{pr} is not an object")
    return answer


def _check_pr(ledger: Ledger, pr: int, branch: str, tip: str) -> dict:
    view = pr_view(ledger, pr, "state,headRefName,headRefOid,baseRefName")
    if view.get("headRefName") != branch:
        raise MoveRefused(f"PR #{pr} is for `{view.get('headRefName')}`, not `{branch}`")
    if view.get("baseRefName") != ledger.trunk:
        raise MoveRefused(f"PR #{pr} targets `{view.get('baseRefName')}`, not trunk `{ledger.trunk}`")
    if view.get("state") != "OPEN":
        raise MoveRefused(f"PR #{pr} is {view.get('state')}; a queued PR is open")
    head = view.get("headRefOid") or ""
    if head != tip:
        raise MoveRefused(f"PR #{pr} head is {head[:7] or 'unknown'}, but `{branch}` is at {tip[:7]}; push the "
                          "branch first")
    return {"pr_head": head}


def queue(ledger: Ledger, actor: Actor, branch: str, *, pr: int | None = None) -> Row:
    require_may(actor, "queue", ledger.posts)
    if ledger.mode == "pr" and pr is None:
        raise MoveRefused("this project ships through pull requests; name the PR (--pr <number>)")
    if ledger.mode != "pr" and pr is not None:
        raise MoveRefused(f"this project does not use pull requests (flow `{ledger.mode}`); queue without --pr")
    current = gitq.branch_tip(ledger.root, branch, run=ledger.run)
    if current is None:
        raise MoveRefused(f"git could not resolve the tip of `{branch}`; nothing is queued that nobody can name")
    with ledger.session() as s:
        row = s.need_open_row(branch)
        state = s.next_state(row, "queue")
        waiting = blocked_by(s.rows, row, ledger.profile)
        if waiting:
            names = ", ".join(f"`{other.branch or other.id}` ({other.state or 'unknown'})" for other in waiting)
            raise MoveRefused(f"`{branch}` requires {names}; it is queued once they are delivered")
        if row.verdict and current != row.verdict:
            raise MoveRefused(f"`{branch}` moved since it was accepted ({row.verdict[:7]} -> {current[:7]}); the "
                              "owner records it with `flotilla work moved` and the reader accepts again")
        evidence = _check_pr(ledger, pr, branch, current) if pr is not None else {}
        fields = {"tip": current}
        if pr is not None:
            fields["pr"] = str(pr)
        return s.append(actor, row.id, "queue", state, fields=fields, evidence=evidence)
