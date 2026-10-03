import pytest

from flotilla.ledger import core, findings, judging, outside
from flotilla.ledger.errors import MoveRefused
from ledgerkit import (PROFILE, actor, commit, drive, git, make_ledger, merge, repo_with_origin,
                       shipped_direct)

DIRECT = {**PROFILE, "flow": {"mode": "direct"}}


@pytest.fixture()
def world(tmp_path):
    root = repo_with_origin(tmp_path)
    return root, make_ledger(root, tmp_path / "state", profile=DIRECT)


def kinds(ledger):
    return sorted((item["kind"], item["branch"]) for item in findings.findings(ledger))


def test_a_quiet_ledger_has_no_findings(world):
    root, ledger = world
    drive(root, ledger)
    assert kinds(ledger) == []


def test_an_open_row_whose_branch_is_gone(world):
    root, ledger = world
    core.claim(ledger, actor(ledger, "main session 1"), "feat/ghost")
    assert kinds(ledger) == [("vanished", "feat/ghost")]


def test_work_after_close_is_seen(world):
    root, ledger = world
    shipped_direct(root, ledger)
    judging.close(ledger, actor(ledger, "main session 1"), "feat/x")
    git(root, "checkout", "-q", "feat/x")
    late = commit(root, "after the row closed", "late.txt")
    git(root, "checkout", "-q", "main")
    found = findings.findings(ledger)
    assert [(item["kind"], item["sha"]) for item in found] == [("after_close", late)]


def test_a_released_branch_that_did_not_move_is_quiet(world):
    root, ledger = world
    drive(root, ledger, to="handed")
    core.release(ledger, actor(ledger, "main session 1"), "feat/x", why="superseded")
    assert kinds(ledger) == []


def test_unaccepted_work_already_in_trunk(world):
    root, ledger = world
    row = drive(root, ledger, to="handed")
    merge(root, "feat/x")
    git(root, "push", "-q", "origin", "main")
    assert ("unread_in_trunk", "feat/x") in kinds(ledger)


def test_a_direct_commit_nobody_read(world):
    root, ledger = world
    drive(root, ledger)
    merge(root, "feat/x")
    stray = commit(root, "pushed straight to trunk", "stray.txt")
    git(root, "push", "-q", "origin", "main")
    found = [item for item in findings.findings(ledger) if item["kind"] == "direct_commit"]
    assert [item["sha"] for item in found] == [stray]


def test_work_born_in_a_batch_that_never_left(world):
    root, ledger = world
    fix = commit(root, "a fix inside the batch", "fix.txt")
    outside.inbatch(ledger, actor(ledger, "sender 1"), "batch/fix", commit=fix, read_by="review session 1",
                    why="a fix")
    assert kinds(ledger) == [("inbatch_not_pushed", "batch/fix")]
    git(root, "push", "-q", "origin", "main")
    assert kinds(ledger) == []


def test_a_fresh_branch_at_trunk_is_not_unread_work(world):
    root, ledger = world
    shipped_direct(root, ledger)                        # trunk moves on by a merge commit
    git(root, "branch", "feat/b", "main~1")
    core.claim(ledger, actor(ledger, "main session 1"), "feat/b")
    git(root, "branch", "-f", "feat/b", "main")         # the author moves the fresh branch to the new trunk
    assert kinds(ledger) == []
    stray = commit(root, "pushed straight to trunk", "stray.txt")
    git(root, "push", "-q", "origin", "main")
    git(root, "branch", "-f", "feat/b", "main")         # fast-forwarded over a commit nobody read
    found = [(item["kind"], item["branch"], item["sha"]) for item in findings.findings(ledger)]
    assert found == [("direct_commit", "main", stray)]


def test_after_close_is_quiet_when_another_open_row_carries_the_moved_tip(world):
    root, ledger = world
    drive(root, ledger, "feat/a", to="handed")
    core.release(ledger, actor(ledger, "main session 1"), "feat/a", why="goes on in another row")
    git(root, "checkout", "-q", "feat/a")
    moved = commit(root, "the work went on", "on.txt")
    git(root, "checkout", "-q", "main")
    assert [(item["kind"], item["sha"]) for item in findings.findings(ledger)] == [("after_close", moved)]
    git(root, "branch", "feat/b", "feat/a")
    core.claim(ledger, actor(ledger, "main session 1"), "feat/b")
    assert kinds(ledger) == []


def test_a_reader_closes_a_direct_commit_by_vouching_for_it_on_trunk(world):
    """Twosuns field test of 0.6.7, W5: a profile commit straight to trunk was a finding no move closed - `vouch`
    and `offledger` both wanted an open row, `inbatch` sent the sender to `offledger`, and trunk never has a row. The
    reviewer's first try was right: a reader vouches for the commit on trunk, and that reading accounts it."""
    root, ledger = world
    drive(root, ledger)
    merge(root, "feat/x")
    stray = commit(root, "flotilla 0.6 profile", "stray.txt")
    git(root, "push", "-q", "origin", "main")
    said = [item for item in findings.findings(ledger) if item["kind"] == "direct_commit"]
    assert said and "flotilla work vouch main --commit" in said[0]["why"]
    row = outside.vouch(ledger, actor(ledger, "review session 1"), "main", commit=stray)
    assert (row.state, row.reader, row.merge) == ("offledger", "review session 1", stray)
    assert not [item for item in findings.findings(ledger) if item["kind"] == "direct_commit"]


def test_a_vouch_on_trunk_is_for_an_unread_commit_on_trunk_by_a_reader(world):
    root, ledger = world
    drive(root, ledger)
    merge(root, "feat/x")
    git(root, "push", "-q", "origin", "main")
    read = git(root, "rev-parse", "main")
    with pytest.raises(MoveRefused, match="already accounted"):
        outside.vouch(ledger, actor(ledger, "review session 1"), "main", commit=read)
    git(root, "checkout", "-q", "-b", "side")
    off = commit(root, "not on trunk", "off.txt")
    with pytest.raises(MoveRefused, match="not on `"):
        outside.vouch(ledger, actor(ledger, "review session 1"), "main", commit=off)
    git(root, "checkout", "-q", "main")
    stray = commit(root, "straight to trunk", "stray.txt")
    git(root, "push", "-q", "origin", "main")
    with pytest.raises(MoveRefused, match="may not `vouch`"):   # the one who merges reads nothing it merges
        outside.vouch(ledger, actor(ledger, "sender 1"), "main", commit=stray)


def test_the_refusals_around_a_direct_commit_point_to_the_reader_s_vouch(world):
    """W5: inbatch sent the sender to offledger, and offledger refused for want of a row - a circle."""
    root, ledger = world
    stray = commit(root, "straight to trunk", "stray.txt")
    git(root, "push", "-q", "origin", "main")
    sender = actor(ledger, "sender 1")
    with pytest.raises(MoveRefused, match="flotilla work vouch main --commit"):
        outside.offledger(ledger, sender, "main", merge=stray, witness="review session 1")
    with pytest.raises(MoveRefused, match="flotilla work vouch main --commit"):
        outside.inbatch(ledger, sender, "chore/x", commit=stray, read_by="review session 1", why="x")


def test_a_reader_never_vouches_for_a_commit_it_made(world, tmp_path):
    """Review of 0.6.9, I1: the trunk vouch had no owner to compare with, so a reader could push a commit straight to
    trunk and clear the only trace of it by vouching for it itself. Its seat's tree and branches say what it made."""
    root, ledger = world
    reviewer = actor(ledger, "review session 1")
    home = tmp_path / "reviewer-1"
    git(root, "worktree", "add", "-q", "-b", "fleet/reviewer-1", str(home), "main")
    core.reserve(ledger, reviewer, "fleet/reviewer-1", tree=str(home))
    git(home, "checkout", "-q", "--detach")
    mine = commit(home, "a reader's own change", "mine.txt")
    git(home, "push", "-q", "origin", "HEAD:main")
    git(root, "pull", "-q", "origin", "main")
    with pytest.raises(MoveRefused, match="made it"):
        outside.vouch(ledger, reviewer, "main", commit=mine)
    stray = commit(root, "someone else's", "stray.txt")   # made in the main checkout, not the reader's tree
    git(root, "push", "-q", "origin", "main")
    git(home, "fetch", "-q", "origin")
    git(home, "checkout", "-q", "--detach", "origin/main")   # moved to it, not made there: still someone else's
    row = outside.vouch(ledger, reviewer, "main", commit=stray)
    assert row.history[-1]["evidence"]["author"]
