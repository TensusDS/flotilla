"""Carrying accepted work to trunk: queue, land, ship (spec, sections 4.6, 5, 6.2-6.4).

`queue` puts reviewed work in the sender's batch: its dependencies must be delivered, the branch must still be at
the revision read, and in PR mode the named pull request must be open, for this branch, into trunk, at that
revision. Every fact about a pull request is asked of GitHub through `gh`; none is taken from the caller.

`land` (direct push and local only) records the commit on the local trunk that carries the revision read — a merge
of it or a squash of the same change — and refuses when the push would also carry a commit no verdict covers.

`ship` is never typed: it asks. In PR mode, GitHub says the PR merged the revision read and names the merge commit,
which origin's trunk must contain; in direct mode origin's trunk must contain the landed commit. Then the gate:
required CI jobs, the gate command, or a push receipt. Anything not proved yet raises `NotYet` and records nothing.
`reconcile` asks for every queued or landed row at once.
"""

from __future__ import annotations

import json

from flotilla.ledger import batch, gitq
from flotilla.ledger import gate as gates
from flotilla.ledger.actor import Actor, require_may
from flotilla.ledger.core import Ledger
from flotilla.ledger.errors import MoveRefused, NotYet
from flotilla.ledger.model import Row, blocked_by
from flotilla.ledger.transitions import next_state


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


def land(ledger: Ledger, actor: Actor, branch: str, *, merge: str | None = None) -> Row:
    require_may(actor, "land", ledger.posts)
    trunk_head = gitq.resolve(ledger.root, f"refs/heads/{ledger.trunk}", run=ledger.run)
    if trunk_head is None:
        raise MoveRefused(f"git could not resolve the local trunk `{ledger.trunk}`")
    with ledger.session() as s:
        row = s.need_open_row(branch)
        state = s.next_state(row, "land")
        read = batch.revision_of(row)
        if not read:
            raise MoveRefused(f"`{branch}` carries no recorded revision; nothing lands that nobody read")
        commit = gitq.resolve(ledger.root, merge, run=ledger.run) if merge else trunk_head
        if commit is None:
            raise MoveRefused(f"git could not resolve --merge {merge}")
        if gitq.is_ancestor(ledger.root, commit, trunk_head, run=ledger.run) is not True:
            raise MoveRefused(f"{commit[:7]} is not on the local `{ledger.trunk}`; land records work merged there")
        contains = gitq.is_ancestor(ledger.root, read, commit, run=ledger.run) is True
        if not contains and not _squash_on_trunk(ledger, row, read, commit):
            raise MoveRefused(f"{commit[:7]} does not contain the revision read ({read[:7]}); merge `{branch}` "
                              f"into `{ledger.trunk}` first")
        loose = batch.unaccounted(ledger, s.rows, trunk_head, base=row.base)   # what the push carries
        if loose is None:
            raise MoveRefused("could not tell what the push would carry: no origin, and no base recorded on the row")
        if loose:
            named = "; ".join(f"{sha[:7]} {batch.subject(ledger, sha)}" for sha in loose[:5])
            more = f" and {len(loose) - 5} more" if len(loose) > 5 else ""
            raise MoveRefused(f"the batch carries work nobody read: {named}{more}. Hand it over for review, or "
                              "record work born in the batch with `flotilla work inbatch`")
        return s.append(actor, row.id, "land", state, fields={"merge": commit}, evidence={"trunk": trunk_head})


def _squash_on_trunk(ledger: Ledger, row: Row, read: str, commit: str) -> bool:
    """Whether some commit between the row's base and `commit` carries exactly the change read."""
    if not row.base:
        return False
    listed = ledger.run(["git", "-C", str(ledger.root), "rev-list", commit, f"^{row.base}"], capture_output=True,
                        text=True, check=False)
    candidates = listed.stdout.split() if listed.returncode == 0 else []
    return any(batch.carries_change(ledger, sha, row.base, read) for sha in candidates)


def _fetch(ledger: Ledger) -> None:
    ledger.run(["git", "-C", str(ledger.root), "fetch", "--quiet", "origin", ledger.trunk], capture_output=True,
               text=True, check=False)


def _on_origin(ledger: Ledger, sha: str) -> bool | None:
    return gitq.is_ancestor(ledger.root, sha, f"refs/remotes/origin/{ledger.trunk}", run=ledger.run)


def _shipped_pr(ledger: Ledger, row: Row) -> tuple[str, dict, list[str]]:
    if not row.pr:
        raise MoveRefused(f"`{row.branch}` names no PR; queue it with --pr")
    view = pr_view(ledger, int(row.pr), "state,headRefOid,mergeCommit")
    status = view.get("state")
    if status == "OPEN":
        raise NotYet(f"PR #{row.pr} is still open")
    if status != "MERGED":
        raise MoveRefused(f"PR #{row.pr} is {status}; a PR closed without merging did not ship. Release the row, "
                          "or queue it again with a new PR")
    read = batch.revision_of(row)
    head = view.get("headRefOid") or ""
    if head != read:
        raise MoveRefused(f"PR #{row.pr} merged {head[:7] or 'an unknown head'}, but the revision read is "
                          f"{read[:7]}: what shipped is not what was read. Record it with `flotilla work "
                          "offledger` and a witness")
    merged = (view.get("mergeCommit") or {}).get("oid") or ""
    if not merged:
        raise NotYet(f"GitHub names no merge commit for PR #{row.pr} yet")
    _fetch(ledger)
    if _on_origin(ledger, merged) is not True:
        raise NotYet(f"origin's `{ledger.trunk}` does not have {merged[:7]} yet (fetched)")
    return merged, {"pr": row.pr, "pr_head": head}, [read]


def _shipped_direct(ledger: Ledger, row: Row) -> tuple[str, dict, list[str]]:
    if not row.merge:
        raise MoveRefused(f"`{row.branch}` has no landed commit recorded")
    _fetch(ledger)
    if _on_origin(ledger, row.merge) is not True:
        raise NotYet(f"origin's `{ledger.trunk}` does not have {row.merge[:7]}: landed, not pushed")
    later = ledger.run(["git", "-C", str(ledger.root), "rev-list", "--ancestry-path",
                        f"{row.merge}..refs/remotes/origin/{ledger.trunk}"], capture_output=True, text=True,
                       check=False)
    return row.merge, {}, list(reversed(later.stdout.split())) if later.returncode == 0 else []


def ship(ledger: Ledger, actor: Actor, branch: str) -> Row:
    require_may(actor, "ship", ledger.posts)
    row = next((r for r in reversed(list(ledger.rows().values())) if r.branch == branch and r.is_open), None)
    if row is None:
        raise MoveRefused(f"no open ledger row for `{branch}`")
    next_state(row.state, "ship", ledger.profile, ledger.owner_post(row))
    commit, evidence, candidates = (_shipped_pr if ledger.mode == "pr" else _shipped_direct)(ledger, row)
    found = gates.gate_for(ledger, commit, candidates=candidates)
    if found.status in (gates.PENDING, gates.UNKNOWN):
        raise NotYet(f"not shipped yet: {found.text}")
    if found.status == gates.RED:
        raise MoveRefused(f"not shipped: {found.text}")
    with ledger.session() as s:
        current = s.need_open_row(branch)
        if current.id != row.id or current.state != row.state:
            raise MoveRefused(f"`{branch}` changed while origin was asked; run it again")
        state = s.next_state(current, "ship")
        return s.append(actor, current.id, "ship", state, fields={"merge": commit, "gate": found.text},
                        evidence=evidence)


def reconcile(ledger: Ledger, actor: Actor) -> list[str]:
    """Ask origin (or the PR) about every queued or landed row; ship what is proved, report the rest."""
    require_may(actor, "ship", ledger.posts)
    lines = []
    for row in list(ledger.rows().values()):
        if not row.is_open or row.state not in ("queued", "landed") or ledger.mode == "local":
            continue
        if ledger.mode == "direct" and row.state == "queued":
            continue
        try:
            shipped = ship(ledger, actor, row.branch)
            lines.append(f"shipped {shipped.branch} ({shipped.gate})")
        except NotYet as err:
            lines.append(f"not yet {row.branch}: {err}")
        except MoveRefused as err:
            lines.append(f"refused {row.branch}: {err}")
    return lines
