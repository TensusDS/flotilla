"""Carrying accepted work to trunk: queue, land, ship (spec, sections 4.6, 5, 6.2-6.4).

`queue` puts reviewed work in the sender's batch: its dependencies must be delivered, the branch must still be at
the revision read, and in PR mode the named pull request must be open, for this branch, into trunk, at that
revision. Every fact about a pull request is asked of GitHub through `gh`; none is taken from the caller.

`land` (direct push and local only) records the commit that carries the revision read — a merge of it or a squash
of the same change — on the local trunk or, in direct-push mode, already on origin's trunk, because the local trunk
is checked out in the main checkout, which belongs to nobody. It refuses when the push carries a commit no verdict
covers.

`ship` is never typed: it asks. In PR mode, GitHub says the PR merged the revision read and names the merge commit,
which origin's trunk must contain; in direct mode origin's trunk must contain the landed commit. Then the gate:
required CI jobs, the gate command, or a push receipt. Anything not proved yet raises `NotYet` and records nothing.
`reconcile` asks for every queued or landed row at once.
"""

from __future__ import annotations

import json
import shlex

from flotilla.ledger import batch, gitq
from flotilla.ledger import gate as gates
from flotilla.ledger.actor import Actor, require_may
from flotilla.ledger.core import Ledger
from flotilla.ledger.errors import MoveRefused, NotYet
from flotilla.ledger.model import Row, blocked_by
from flotilla.ledger.transitions import moves_from, next_state
from flotilla.ledger.views import approve_command


#: What gh says when the pull request does not exist: an answer, so a refusal; any other failure is an instrument
#: that could not ask, so unknown - "not yet", ask again (TODO, ledger part B).
NO_SUCH_PR = ("Could not resolve to a PullRequest", "no pull requests found")


def pr_view(ledger: Ledger, pr: int, fields: str) -> dict:
    done = ledger.run(["gh", "pr", "view", str(pr), "--json", fields], cwd=str(ledger.root), capture_output=True,
                      text=True, check=False)
    if done.returncode != 0:
        said = (done.stderr or done.stdout).strip()
        if any(words in said for words in NO_SUCH_PR):
            raise MoveRefused(f"GitHub has no PR #{pr}: {said}")
        raise NotYet(f"could not ask GitHub about PR #{pr}: {said}")
    try:
        answer = json.loads(done.stdout)
    except ValueError as err:
        raise NotYet(f"GitHub's answer about PR #{pr} is not JSON; ask again") from err
    if not isinstance(answer, dict):
        raise NotYet(f"GitHub's answer about PR #{pr} is not an object; ask again")
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


def approve(ledger: Ledger, branch: str) -> Row:
    """The person approves accepted work for trunk, at the revision that was read (merge_authorized_by = human).
    Only a person: the yes a sender waited for came as a message, and a message can be written by anyone (F24)."""
    from flotilla.core import caller
    refused = caller.person_refusal("approves a merge into trunk")
    if refused:
        raise MoveRefused(refused)
    person = Actor("the person", None, "person", "the person")
    with ledger.session() as s:
        row = s.open_row(branch) or next(   # work born in the batch is a closed row under its label
            (item for item in reversed(list(s.rows.values())) if item.branch == branch and item.state == "inbatch"),
            None) or s.need_open_row(branch)
        if row.state == "inbatch":
            revision, state = row.merge, row.state
        elif row.state == "accepted" and row.verdict:
            revision, state = row.verdict, s.next_state(row, "approve")
        elif "queue" in moves_from(row.state, ledger.profile, ledger.owner_post(row)):
            revision = gitq.branch_tip(ledger.root, branch, run=ledger.run) or ""   # no review: the tip as it is
            state = s.next_state(row, "approve")
        else:
            revision, state = "", ""
        if not revision:
            raise MoveRefused(f"`{branch}` is {row.state}; a person approves work on its way to trunk: accepted, "
                              "born in the batch, or not reviewed where the project skips review")
        return s.append(person, row.id, "approve", state, fields={"approved": revision},
                        evidence={"revision": revision})


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
            def named(links):
                return ", ".join(f"`{other.branch or other.id}` ({other.state or 'unknown'})" for other in links)
            builds = [other for other in waiting if other.id in (row.requires or [])]
            orders = [other for other in waiting if other.id not in (row.requires or [])]
            said = ([f"requires {named(builds)}"] if builds else []) + ([f"goes after {named(orders)}"] if orders
                                                                        else [])
            raise MoveRefused(f"`{branch}` {' and '.join(said)}; it is queued once they are delivered")
        if row.verdict and current != row.verdict:
            raise MoveRefused(f"`{branch}` moved since it was accepted ({row.verdict[:7]} -> {current[:7]}); the "
                              "owner records it with `flotilla work moved` and the reader accepts again")
        if (ledger.profile.get("flow") or {}).get("merge_authorized_by") == "human" and row.approved != current:
            raise MoveRefused(f"a person authorizes every merge into trunk here, and nobody approved `{branch}` at "
                              f"{current[:7]}: the person types `! {approve_command(ledger, branch)}` in their own "
                              f"session, or runs it in a terminal; record the wait with `flotilla work wait {branch} --on "
                              "\"the person\" --why \"approve before queue\"`")
        evidence = _check_pr(ledger, pr, branch, current) if pr is not None else {}
        fields = {"tip": current}
        if pr is not None:
            fields["pr"] = str(pr)
        return s.append(actor, row.id, "queue", state, fields=fields, evidence=evidence)



def send_back(ledger: Ledger, actor: Actor, branch: str, *, why: str) -> Row:
    """The sender returns an accepted or queued row to its author: it no longer merges with trunk, or trunk moved
    under it. The verdict was over the old tip; the author merges trunk and hands it over for a read (H26c)."""
    require_may(actor, "return", ledger.posts)
    if not why.strip():
        raise MoveRefused("say why it goes back (--why), for example the conflict")
    with ledger.session() as s:
        row = s.need_open_row(branch)
        state = s.next_state(row, "return")
        evidence = {"why": why.strip()}
        if row.pr:   # the author pushes the fix to the same branch; the sender queues it again with the same --pr
            evidence["pr"] = f"PR #{row.pr} stays open: push the fix to `{branch}` and it updates"
        return s.append(actor, row.id, "return", state, fields={"verdict": "", "vouched": [], "why": why.strip()},
                        evidence=evidence)

SEQUENCE = ("merge `{branch}` in your own tree on a branch from `origin/{trunk}`, run `flotilla receipt run "
            "--purpose push` there, push HEAD:{trunk}, then `flotilla work land {branch} --merge <that commit>`")


def land(ledger: Ledger, actor: Actor, branch: str, *, merge: str | None = None) -> Row:
    """Record the commit that carries the revision read: on the local trunk, or, in direct-push mode, already on
    origin's trunk (the local trunk is checked out in the main checkout, which belongs to nobody)."""
    require_may(actor, "land", ledger.posts)
    trunk_head = gitq.resolve(ledger.root, f"refs/heads/{ledger.trunk}", run=ledger.run)
    origin_head = None
    if ledger.mode == "direct":
        _fetch(ledger)
        origin_head = gitq.resolve(ledger.root, f"refs/remotes/origin/{ledger.trunk}", run=ledger.run)
    if trunk_head is None and origin_head is None:
        raise MoveRefused(f"git could not resolve the local trunk `{ledger.trunk}` or origin's")
    with ledger.session() as s:
        row = s.need_open_row(branch)
        state = s.next_state(row, "land")
        read = batch.revision_of(row)
        if not read:
            raise MoveRefused(f"`{branch}` carries no recorded revision; nothing lands that nobody read")
        if merge:
            commit = gitq.resolve(ledger.root, merge, run=ledger.run)
            if commit is None:
                raise MoveRefused(f"git could not resolve --merge {merge}")
        elif origin_head and not (trunk_head and gitq.is_ancestor(ledger.root, read, trunk_head, run=ledger.run))\
                and gitq.is_ancestor(ledger.root, read, origin_head, run=ledger.run):
            commit = _carrying(ledger, read, origin_head)   # pushed from the sender's own tree
        else:
            commit = trunk_head or origin_head
        on_local = bool(trunk_head) and gitq.is_ancestor(ledger.root, commit, trunk_head, run=ledger.run) is True
        on_origin = (not on_local and bool(origin_head)
                     and gitq.is_ancestor(ledger.root, commit, origin_head, run=ledger.run) is True)
        if not on_local and not on_origin:
            where = f"the local `{ledger.trunk}`" + (f" or origin's" if ledger.mode == "direct" else "")
            raise MoveRefused(f"{commit[:7]} is not on {where}; "
                              + (SEQUENCE.format(branch=branch, trunk=ledger.trunk) if ledger.mode == "direct"
                                 else f"merge `{branch}` into `{ledger.trunk}` first"))
        contains = gitq.is_ancestor(ledger.root, read, commit, run=ledger.run) is True
        if not contains and not _squash_on_trunk(ledger, row, read, commit):
            raise MoveRefused(f"{commit[:7]} does not contain the revision read ({read[:7]}); "
                              + (SEQUENCE.format(branch=branch, trunk=ledger.trunk) if ledger.mode == "direct"
                                 else f"merge `{branch}` into `{ledger.trunk}` first"))
        # what the trunk gained from the moment the sender merged, whatever the main checkout holds: a pulled
        # local trunk equals origin, and counting only what the push will carry would count nothing (H29)
        before = gitq.resolve(ledger.root, f"{commit}^1", run=ledger.run) or row.base
        gained = batch.unaccounted(ledger, s.rows, trunk_head if on_local else origin_head, since=before) \
            if before else None
        unknown = (f"could not tell what `{ledger.trunk}` gained with {commit[:7]}: it has no parent and the row "
                   "records no base")
        if on_local and gained is not None:
            push = batch.unaccounted(ledger, s.rows, trunk_head, base=row.base)   # what the push will carry
            loose = None if push is None else gained + [sha for sha in push if sha not in gained]
            unknown = "could not tell what the push carries: no origin, and no base recorded on the row"
        else:
            loose = gained
        if loose is None:
            raise MoveRefused(unknown)
        if loose:
            named = "; ".join(f"{sha[:7]} {batch.subject(ledger, sha)}" for sha in loose[:5])
            more = f" and {len(loose) - 5} more" if len(loose) > 5 else ""
            raise MoveRefused(f"the batch carries work nobody read: {named}{more}. Hand it over for review, or "
                              "ask a reader to read it and record it with their own `flotilla work vouch`; nobody "
                              "else's word stands for a reading")
        where = "trunk" if on_local else f"origin/{ledger.trunk}"
        return s.append(actor, row.id, "land", state, fields={"merge": commit},
                        evidence={"trunk": trunk_head if on_local else origin_head, "on": where})


def _carrying(ledger: Ledger, read: str, head: str) -> str:
    """The earliest commit on `head`'s first-parent line that carries `read`: the one this row landed in, not a
    later one that also carries other rows' work."""
    listed = ledger.run(["git", "-C", str(ledger.root), "rev-list", "--first-parent", head, f"^{read}^@"],
                        capture_output=True, text=True, check=False)
    found = None
    for sha in listed.stdout.split() if listed.returncode == 0 else []:
        if gitq.is_ancestor(ledger.root, read, sha, run=ledger.run) is not True:
            break
        found = sha
    return found or head


def _squash_on_trunk(ledger: Ledger, row: Row, read: str, commit: str) -> bool:
    """Whether some commit between the row's base and `commit` carries exactly the change read."""
    if not row.base:
        return False
    listed = ledger.run(["git", "-C", str(ledger.root), "rev-list", commit, f"^{row.base}"], capture_output=True,
                        text=True, check=False)
    candidates = listed.stdout.split() if listed.returncode == 0 else []
    return any(batch.carries_change(ledger, sha, row.base, read) for sha in candidates)


def _fetch(ledger: Ledger) -> bool:
    done = ledger.run(["git", "-C", str(ledger.root), "fetch", "--quiet", "origin", ledger.trunk],
                      capture_output=True, text=True, check=False)
    return done.returncode == 0


def _not_on_origin(ledger: Ledger, sha: str, fetched: bool, on: bool | None, tail: str = "") -> str:
    """Why a commit is not proved on origin, saying only what was asked: git that could not place the commit is not
    "does not have", and a fetch that failed is not "(fetched)" (TODO, ledger part B)."""
    where = f"origin's `{ledger.trunk}`"
    said = (f"git could not tell whether {where} has {sha[:7]}" if on is None
            else f"{where} does not have {sha[:7]}{tail}")
    return said + (" (fetched)" if fetched else " (the fetch failed; origin is read as last fetched here)")


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
        release = "flotilla work release " + shlex.quote(row.branch) + ' --why "<why>"'
        raise MoveRefused(f"PR #{row.pr} is {status}; a PR closed without merging did not ship. Release the row "
                          f"(`{release}`), and claim the work again to ship it through a new PR")
    read = batch.revision_of(row)
    head = view.get("headRefOid") or ""
    if head != read:
        raise MoveRefused(f"PR #{row.pr} merged {head[:7] or 'an unknown head'}, but the revision read is "
                          f"{read[:7]}: what shipped is not what was read. Record it with `flotilla work "
                          "offledger` and a witness")
    merged = (view.get("mergeCommit") or {}).get("oid") or ""
    if not merged:
        raise NotYet(f"GitHub names no merge commit for PR #{row.pr} yet")
    fetched = _fetch(ledger)
    on = _on_origin(ledger, merged)
    if on is not True:
        raise NotYet(_not_on_origin(ledger, merged, fetched, on, " yet"))
    return merged, {"pr": row.pr, "pr_head": head}, [read]


def _shipped_direct(ledger: Ledger, row: Row) -> tuple[str, dict, list[str]]:
    if not row.merge:
        raise MoveRefused(f"`{row.branch}` has no landed commit recorded")
    fetched = _fetch(ledger)
    on = _on_origin(ledger, row.merge)
    if on is not True:
        raise NotYet(_not_on_origin(ledger, row.merge, fetched, on, ": landed, not pushed"))
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
    fetched = False
    for row in list(ledger.rows().values()):
        if not row.is_open or row.state not in ("queued", "landed") or ledger.mode == "local":
            continue
        if ledger.mode == "direct" and row.state == "queued":
            if not fetched:
                _fetch(ledger)
                fetched = True
            read = batch.revision_of(row)
            if read and _on_origin(ledger, read) is True:   # pushed from the sender's tree, never recorded
                lines.append(f"pushed, not landed {row.branch}: origin's `{ledger.trunk}` carries {read[:7]}; "
                             f"record it with `flotilla work land {row.branch}` - it finds the merge that carries it")
            continue
        try:
            shipped = ship(ledger, actor, row.branch)
            lines.append(f"shipped {shipped.branch} ({shipped.gate})")
        except NotYet as err:
            lines.append(f"not yet {row.branch}: {err}")
        except MoveRefused as err:
            lines.append(f"refused {row.branch}: {err}")
    return lines
