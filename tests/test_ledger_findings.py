import pytest

from flotilla.ledger import core, findings, judging, outside
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
