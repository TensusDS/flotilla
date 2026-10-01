"""Work that reaches trunk outside the review path (spec, sections 6.2-6.3).

`inbatch`: a change the sender makes inside the batch (a conflict resolution, a one-line fix) has no branch and no
claim. It is recorded as its own finished row before the push: the commit must be on the local trunk and not yet on
origin; a merge qualifies only when it holds something of its own (a resolution); and its reader is a live session
whose post may accept, never the sender. The land door then accounts it. PR projects have no inbatch: every change
there reaches trunk through a pull request.

`offledger`: work that reached trunk without the ledger (merged by hand, by another tool, through a PR opened
outside the fleet). The row ends with the commit that carried it and a named witness who is not its owner. Git
answers what it can — the merge brought the recorded tip, or the commit carries the same change — and whatever git
cannot answer is recorded as attested text, marked unmeasured.
"""

from __future__ import annotations

from flotilla.ledger import batch, core, gitq
from flotilla.ledger.actor import Actor, require_may
from flotilla.ledger.core import Ledger
from flotilla.ledger.errors import MoveRefused
from flotilla.ledger.model import Row, next_row_id
from flotilla.posts import PostError, post_for_session


def inbatch(ledger: Ledger, actor: Actor, label: str, *, commit: str, read_by: str, why: str) -> Row:
    require_may(actor, "inbatch", ledger.posts)
    if ledger.mode == "pr":
        raise MoveRefused("in a pull-request project every change reaches trunk through a PR; hand it over and "
                          "queue it")
    read_by, why = read_by.strip(), why.strip()
    if not why:
        raise MoveRefused("say what the change is (--why)")
    if not read_by:
        raise MoveRefused("name who read it (--read-by): work born in the batch still has a reader")
    if read_by == actor.name:
        raise MoveRefused(f"{actor.name} wrote it; someone else reads it")
    try:
        known = ledger.live_names()
    except MoveRefused:
        known = {name for row in ledger.rows().values() for name in (row.owner, row.reader)} - {""}
    if read_by not in known:
        raise MoveRefused(f"`{read_by}` is not a live session (or, without the census, one the ledger knows); the "
                          "reader of batch work is a session that read it")
    try:
        post = post_for_session(ledger.posts, read_by)
    except PostError as err:
        raise MoveRefused(str(err)) from err
    if post is None or "accept" not in post.may:
        raise MoveRefused(f"{read_by} may not accept work; the reader of batch work holds a post that may")
    sha = gitq.resolve(ledger.root, commit, run=ledger.run)
    if sha is None:
        raise MoveRefused(f"git could not resolve --commit {commit}")
    head = gitq.resolve(ledger.root, f"refs/heads/{ledger.trunk}", run=ledger.run)
    if head is None or gitq.is_ancestor(ledger.root, sha, head, run=ledger.run) is not True:
        raise MoveRefused(f"{sha[:7]} is not on the local `{ledger.trunk}`; inbatch records a commit in the batch")
    origin = f"refs/remotes/origin/{ledger.trunk}"
    if gitq.resolve(ledger.root, origin, run=ledger.run) and \
            gitq.is_ancestor(ledger.root, sha, origin, run=ledger.run) is True:
        raise MoveRefused(f"{sha[:7]} is already on origin; work that reached trunk outside the ledger is recorded "
                          "with offledger")
    merge = batch.is_merge(ledger, sha)
    if merge is None or (merge and batch.clean_merge(ledger, sha) is not False):
        raise MoveRefused(f"{sha[:7]} is a merge that adds nothing of its own, or git could not say; name the commit "
                          "that holds the change")
    with ledger.session() as s:
        core.check_claim(s.rows, label)
        fields = {"branch": label, "owner": actor.name, "merge": sha, "reader": read_by}
        return s.append(actor, next_row_id(s.rows), "inbatch", "inbatch", fields=fields, evidence={"why": why})


def _parents(ledger: Ledger, sha: str) -> list[str] | None:
    done = ledger.run(["git", "-C", str(ledger.root), "rev-list", "--parents", "-n", "1", sha], capture_output=True,
                      text=True, check=False)
    return done.stdout.split()[1:] if done.returncode == 0 else None


def _proof(ledger: Ledger, row: Row, sha: str, attested: str) -> str:
    tip = row.tip or gitq.branch_tip(ledger.root, row.branch, run=ledger.run) or ""
    parents = _parents(ledger, sha)
    if parents is None:
        raise MoveRefused(f"git could not read {sha[:7]}")
    if len(parents) > 1 and tip:
        if gitq.is_ancestor(ledger.root, tip, parents[0], run=ledger.run) is True:
            raise MoveRefused(f"{tip[:7]} was on trunk before {sha[:7]}; that merge did not bring `{row.branch}`")
        if gitq.is_ancestor(ledger.root, tip, parents[1], run=ledger.run) is True:
            return f"merge {sha[:7]} brought tip {tip[:7]} (measured)"
    if len(parents) == 1 and batch.carries_change(ledger, sha, row.base, tip):
        return f"{sha[:7]} carries the change {row.base[:7]}..{tip[:7]} (git patch-id, measured)"
    if attested.strip():
        return f"not measured; attested: {attested.strip()}"
    raise MoveRefused(f"no instrument ties {sha[:7]} to `{row.branch}`: it did not bring the recorded tip and does "
                      "not carry the same change. Say what you checked with --attested \"<what and how>\"; it is "
                      "recorded as unmeasured")


def offledger(ledger: Ledger, actor: Actor, branch: str, *, merge: str, witness: str, attested: str = "") -> Row:
    require_may(actor, "offledger", ledger.posts)
    if (ledger.profile.get("flow") or {}).get("merge_authorized_by") == "human":
        from flotilla.core import caller   # work that reached trunk unapproved is recorded by the person (F24)
        refused = caller.person_refusal("records work that reached trunk outside the ledger, where a person "
                                        "authorizes merges")
        if refused:
            raise MoveRefused(refused)
    witness = witness.strip()
    if not witness:
        raise MoveRefused("name the witness (--witness): half of this proof is measured, the other half is signed")
    trunk = gitq.trunk_ref(ledger.root, ledger.trunk, run=ledger.run)
    sha = gitq.resolve(ledger.root, merge, run=ledger.run)
    if sha is None:
        raise MoveRefused(f"git could not resolve --merge {merge}")
    if gitq.is_ancestor(ledger.root, sha, trunk, run=ledger.run) is not True:
        raise MoveRefused(f"{sha[:7]} is not on `{trunk}`: the work has not reached trunk, so it is not offledger")
    with ledger.session() as s:
        row = s.need_open_row(branch)
        state = s.next_state(row, "offledger")
        if witness == row.owner:
            raise MoveRefused(f"{witness} owns `{branch}`; the owner cannot witness their own work reaching trunk")
        evidence = {"witness": witness, "proof": _proof(ledger, row, sha, attested)}
        if attested.strip():
            evidence["attested"] = attested.strip()
        return s.append(actor, row.id, "offledger", state, fields={"merge": sha}, evidence=evidence)


VOUCHABLE = ("accepted", "queued", "landed", "inbatch")


def vouch(ledger: Ledger, actor: Actor, branch: str, *, commit: str) -> Row:
    """A reader vouches for a commit the batch carries that no verdict covers: the sender's conflict resolution,
    read by someone who did not write it. The sender asks for it; it never writes a reader's name itself (H21)."""
    require_may(actor, "vouch", ledger.posts)
    sha = gitq.resolve(ledger.root, commit, run=ledger.run)
    if sha is None:
        raise MoveRefused(f"git could not resolve --commit {commit}")
    with ledger.session() as s:
        row = s.open_row(branch) or next(   # batch work recorded with inbatch is a closed row under its label
            (item for item in reversed(list(s.rows.values())) if item.branch == branch and item.state == "inbatch"),
            None) or s.need_open_row(branch)
        if row.state not in VOUCHABLE:
            raise MoveRefused(f"`{branch}` is {row.state}; a vouch is for work on its way to trunk (accepted, "
                              "queued or landed)")
        if row.owner == actor.name:
            raise MoveRefused(f"{actor.name} owns `{branch}`; someone else vouches for it")
        if actor.post is not None and ("land" in actor.post.may or actor.post.writes_one_copy):
            raise MoveRefused(f"{actor.name}'s post may land; the one who merges never vouches for what it merged")
        # a vouch annotates; on a finished inbatch row it is the only move, and the row stays what it was
        state = row.state if row.state == "inbatch" else s.next_state(row, "vouch")
        vouched = list(row.vouched) + ([sha] if sha not in row.vouched else [])
        return s.append(actor, row.id, "vouch", state, fields={"vouched": vouched}, evidence={"commit": sha})
