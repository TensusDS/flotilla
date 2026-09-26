import sys

import pytest

from flotilla.ledger import core, gitq, handover, receipts
from flotilla.ledger.errors import MoveRefused
from ledgerkit import PROFILE, actor, branch, commit, git, make_ledger, repo_with_origin

GREEN = f"{sys.executable} -c \"print('1 passed')\""


@pytest.fixture()
def world(tmp_path):
    root = repo_with_origin(tmp_path)
    return root, make_ledger(root, tmp_path / "state")


def claimed(root, ledger, name="feat/x", owner="main session 1"):
    branch(root, name, "work")
    return core.claim(ledger, actor(ledger, owner), name)


def test_hand_records_the_tip(world):
    root, ledger = world
    claimed(root, ledger)
    row = handover.hand(ledger, actor(ledger, "main session 1"), "feat/x")
    assert row.state == "handed" and row.tip == gitq.branch_tip(root, "feat/x")


def test_only_the_owner_hands_over(world):
    root, ledger = world
    claimed(root, ledger)
    with pytest.raises(MoveRefused, match="only the owner"):
        handover.hand(ledger, actor(ledger, "minor session 1"), "feat/x")


def test_a_named_tip_must_be_the_branch_tip(world):
    root, ledger = world
    claimed(root, ledger)
    with pytest.raises(MoveRefused, match="not the branch tip"):
        handover.hand(ledger, actor(ledger, "main session 1"), "feat/x", tip=git(root, "rev-parse", "main"))


def test_hand_needs_a_green_handover_receipt(tmp_path):
    root = repo_with_origin(tmp_path)
    profile = {**PROFILE, "tests": {"tier": [{"name": "unit", "command": GREEN, "required_for": ["handover"]}]}}
    ledger = make_ledger(root, tmp_path / "state", profile=profile)
    claimed(root, ledger)
    with pytest.raises(MoveRefused, match="receipt run"):
        handover.hand(ledger, actor(ledger, "main session 1"), "feat/x")
    tree = tmp_path / "t"
    git(root, "worktree", "add", "-q", str(tree), "feat/x")
    receipts.run_receipt(tree, state=tmp_path / "state", repo_key=ledger.repo_key, purpose="handover",
                         profile=profile, timeout=60)
    assert handover.hand(ledger, actor(ledger, "main session 1"), "feat/x").state == "handed"


def test_a_stack_on_unaccepted_work_is_recorded(world):
    root, ledger = world
    branch(root, "feat/a", "a")
    core.claim(ledger, actor(ledger, "main session 1"), "feat/a")
    git(root, "checkout", "-q", "feat/a")
    git(root, "checkout", "-q", "-b", "feat/b")
    commit(root, "b", "b.txt")
    git(root, "checkout", "-q", "main")
    core.claim(ledger, actor(ledger, "minor session 1"), "feat/b")
    row = handover.hand(ledger, actor(ledger, "minor session 1"), "feat/b")
    assert row.history[-1]["evidence"]["stacked_on"] == ["feat/a"]


def test_moved_is_free_before_a_reader_takes_it(world):
    root, ledger = world
    claimed(root, ledger)
    handover.hand(ledger, actor(ledger, "main session 1"), "feat/x")
    git(root, "checkout", "-q", "feat/x")
    new = commit(root, "more", "more.txt")
    git(root, "checkout", "-q", "main")
    row = handover.moved(ledger, actor(ledger, "main session 1"), "feat/x", tip=new)
    assert row.tip == new and row.state == "handed"


def test_wait_records_whom_and_why_then_clears(world):
    root, ledger = world
    claimed(root, ledger)
    row = handover.wait(ledger, actor(ledger, "main session 1"), "feat/x", on="Max", why="needs a decision")
    assert (row.waiting_on, row.note, row.state) == ("Max", "needs a decision", "claimed")
    assert handover.wait(ledger, actor(ledger, "main session 1"), "feat/x", clear=True).waiting_on == ""


def test_only_the_owner_or_the_reader_waits(world):
    root, ledger = world
    claimed(root, ledger)
    with pytest.raises(MoveRefused, match="owner"):
        handover.wait(ledger, actor(ledger, "minor session 1"), "feat/x", on="Max", why="x")


def test_a_state_change_ends_a_recorded_wait(world):
    root, ledger = world
    claimed(root, ledger)
    handover.wait(ledger, actor(ledger, "main session 1"), "feat/x", on="Max", why="needs a decision")
    row = handover.hand(ledger, actor(ledger, "main session 1"), "feat/x")
    assert (row.waiting_on, row.note) == ("", "")
