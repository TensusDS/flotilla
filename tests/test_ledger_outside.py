import pytest

from flotilla.ledger import batch, outside
from flotilla.ledger.errors import MoveRefused
from ledgerkit import PROFILE, actor, commit, drive, git, make_ledger, merge, repo_with_origin

SENDER = "sender 1"
DIRECT = {**PROFILE, "flow": {"mode": "direct"}}


@pytest.fixture()
def world(tmp_path):
    root = repo_with_origin(tmp_path)
    return root, make_ledger(root, tmp_path / "state", profile=DIRECT)


def push(root):
    git(root, "push", "-q", "origin", "main")


def test_inbatch_records_a_commit_born_in_the_batch_and_the_batch_accounts_it(world):
    root, ledger = world
    fix = commit(root, "fix a typo while merging", "typo.txt")
    row = outside.inbatch(ledger, actor(ledger, SENDER), "batch/typo", commit=fix, read_by="review session 1",
                          why="a typo in the README")
    assert (row.state, row.merge, row.reader, row.owner) == ("inbatch", fix, "review session 1", SENDER)
    assert batch.unaccounted(ledger, ledger.rows(), "main") == []


def test_inbatch_needs_a_reader_other_than_the_sender(world):
    root, ledger = world
    fix = commit(root, "fix", "typo.txt")
    with pytest.raises(MoveRefused, match="someone else reads it"):
        outside.inbatch(ledger, actor(ledger, SENDER), "batch/typo", commit=fix, read_by=SENDER, why="typo")
    with pytest.raises(MoveRefused, match="--read-by"):
        outside.inbatch(ledger, actor(ledger, SENDER), "batch/typo", commit=fix, read_by="", why="typo")


def test_inbatch_refuses_a_commit_off_trunk(world):
    root, ledger = world
    drive(root, ledger, "feat/y", to="claimed")
    side = git(root, "rev-parse", "feat/y")
    with pytest.raises(MoveRefused, match="not on the local `main`"):
        outside.inbatch(ledger, actor(ledger, SENDER), "batch/y", commit=side, read_by="review session 1", why="y")


def test_pr_projects_have_no_inbatch(tmp_path):
    root = repo_with_origin(tmp_path)
    ledger = make_ledger(root, tmp_path / "state")
    fix = commit(root, "fix", "typo.txt")
    with pytest.raises(MoveRefused, match="through a PR"):
        outside.inbatch(ledger, actor(ledger, SENDER), "batch/typo", commit=fix, read_by="review session 1",
                        why="typo")


def test_offledger_measures_the_merge_that_brought_the_tip(world):
    root, ledger = world
    drive(root, ledger, to="claimed")
    head = merge(root, "feat/x")
    push(root)
    row = outside.offledger(ledger, actor(ledger, SENDER), "feat/x", merge=head, witness="review session 1")
    assert (row.state, row.merge) == ("offledger", head)
    assert "brought tip" in row.history[-1]["evidence"]["proof"]


def test_offledger_recognises_a_squash_by_content(world):
    root, ledger = world
    drive(root, ledger, to="claimed")
    squashed = merge(root, "feat/x", squash=True)
    push(root)
    row = outside.offledger(ledger, actor(ledger, SENDER), "feat/x", merge=squashed, witness="review session 1")
    assert "patch-id" in row.history[-1]["evidence"]["proof"]


def test_offledger_without_a_measure_needs_attested_text(world):
    root, ledger = world
    drive(root, ledger, to="claimed")
    other = commit(root, "unrelated", "unrelated.txt")
    push(root)
    with pytest.raises(MoveRefused, match="--attested"):
        outside.offledger(ledger, actor(ledger, SENDER), "feat/x", merge=other, witness="review session 1")
    row = outside.offledger(ledger, actor(ledger, SENDER), "feat/x", merge=other, witness="review session 1",
                            attested="read the merged diff by hand")
    assert row.history[-1]["evidence"]["proof"].startswith("not measured")


def test_offledger_needs_the_commit_on_trunk_and_a_witness_who_is_not_the_owner(world):
    root, ledger = world
    drive(root, ledger, to="claimed")
    head = merge(root, "feat/x")
    with pytest.raises(MoveRefused, match="has not reached trunk"):
        outside.offledger(ledger, actor(ledger, SENDER), "feat/x", merge=head, witness="review session 1")
    push(root)
    with pytest.raises(MoveRefused, match="--witness"):
        outside.offledger(ledger, actor(ledger, SENDER), "feat/x", merge=head, witness="")
    with pytest.raises(MoveRefused, match="owner cannot witness"):
        outside.offledger(ledger, actor(ledger, SENDER), "feat/x", merge=head, witness="main session 1")


def test_a_merge_that_did_not_bring_the_tip_is_refused(world):
    root, ledger = world
    drive(root, ledger, to="claimed")
    merge(root, "feat/x")
    later = merge_empty(root)
    push(root)
    with pytest.raises(MoveRefused, match="did not bring"):
        outside.offledger(ledger, actor(ledger, SENDER), "feat/x", merge=later, witness="review session 1")


def merge_empty(root):
    """A second merge after feat/x is already on trunk: its first parent holds the tip."""
    git(root, "checkout", "-q", "-b", "side")
    commit(root, "side work", "side.txt")
    git(root, "checkout", "-q", "main")
    return merge(root, "side")
