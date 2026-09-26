import pytest

from flotilla.ledger import core, handover, reading
from flotilla.ledger.errors import MoveRefused
from flotilla.posts import load_post
from ledgerkit import actor, branch, commit, git, make_ledger, repo_with_origin

SOLO = """---
name: solo
description: Does everything, to test the author rule on its own.
name_pattern: "solo {n}"
may: [claim, hand, take, accept, fix]
---
"""


@pytest.fixture()
def world(tmp_path):
    root = repo_with_origin(tmp_path)
    return root, make_ledger(root, tmp_path / "state")


def handed(root, ledger):
    branch(root, "feat/x", "work")
    core.claim(ledger, actor(ledger, "main session 1"), "feat/x")
    return handover.hand(ledger, actor(ledger, "main session 1"), "feat/x")


def test_take_names_the_reader(world):
    root, ledger = world
    handed(root, ledger)
    row = reading.take(ledger, actor(ledger, "review session 1"), "feat/x")
    assert (row.reader, row.taken, row.state) == ("review session 1", True, "handed")


def test_only_the_assigned_reader_takes(world):
    root, ledger = world
    handed(root, ledger)
    reading.assign(ledger, actor(ledger, "orchestrator 1"), "feat/x", reader="review session 1")
    with pytest.raises(MoveRefused, match="assigned to review session 1"):
        reading.take(ledger, actor(ledger, "review session 2"), "feat/x")


def test_recuse_is_the_readers_and_clears_the_reader(world):
    root, ledger = world
    handed(root, ledger)
    reading.take(ledger, actor(ledger, "review session 1"), "feat/x")
    with pytest.raises(MoveRefused, match="not assigned"):
        reading.recuse(ledger, actor(ledger, "review session 2"), "feat/x")
    row = reading.recuse(ledger, actor(ledger, "review session 1"), "feat/x")
    assert (row.reader, row.taken) == ("", False)


def test_fix_names_what_must_change(world):
    root, ledger = world
    handed(root, ledger)
    with pytest.raises(MoveRefused, match="--why"):
        reading.fix(ledger, actor(ledger, "review session 1"), "feat/x", why=" ")
    row = reading.fix(ledger, actor(ledger, "review session 1"), "feat/x", why="no test for an empty file")
    assert (row.state, row.reader) == ("fixing", "review session 1")
    assert row.history[-1]["evidence"] == {"why": "no test for an empty file"}


def test_assign_refuses_the_author(world):
    root, ledger = world
    handed(root, ledger)
    with pytest.raises(MoveRefused, match="author"):
        reading.assign(ledger, actor(ledger, "orchestrator 1"), "feat/x", reader="main session 1")


def test_assign_refuses_a_reader_who_is_not_alive(tmp_path):
    root = repo_with_origin(tmp_path)
    ledger = make_ledger(root, tmp_path / "state", live=("main session 1", "orchestrator 1"))
    handed(root, ledger)
    with pytest.raises(MoveRefused, match="not alive"):
        reading.assign(ledger, actor(ledger, "orchestrator 1"), "feat/x", reader="review session 7")


def test_assign_refuses_a_reader_whose_post_may_not_accept(world):
    root, ledger = world
    handed(root, ledger)
    with pytest.raises(MoveRefused, match="may accept"):
        reading.assign(ledger, actor(ledger, "orchestrator 1"), "feat/x", reader="minor session 1")


def test_assign_returns_a_letter_for_the_reader(world):
    root, ledger = world
    handed_row = handed(root, ledger)
    row, text = reading.assign(ledger, actor(ledger, "orchestrator 1"), "feat/x", reader="review session 1")
    assert row.reader == "review session 1" and row.taken is False
    assert "flotilla work take feat/x" in text and handed_row.tip[:7] in text


def test_accept_over_the_handed_tip(world):
    root, ledger = world
    handed_row = handed(root, ledger)
    reading.take(ledger, actor(ledger, "review session 1"), "feat/x")
    row = reading.accept(ledger, actor(ledger, "review session 1"), "feat/x", reviewed=handed_row.tip[:8])
    assert row.state == "accepted" and row.verdict == handed_row.tip


def test_accept_refuses_another_revision(world):
    root, ledger = world
    handed(root, ledger)
    with pytest.raises(MoveRefused, match="handed tip"):
        reading.accept(ledger, actor(ledger, "review session 1"), "feat/x", reviewed=git(root, "rev-parse", "main"))


def test_accept_refuses_a_branch_that_moved_since_handover(world):
    root, ledger = world
    handed_row = handed(root, ledger)
    git(root, "checkout", "-q", "feat/x")
    commit(root, "sneaky", "sneaky.txt")
    git(root, "checkout", "-q", "main")
    with pytest.raises(MoveRefused, match="moved since handover"):
        reading.accept(ledger, actor(ledger, "review session 1"), "feat/x", reviewed=handed_row.tip)


def test_accept_refuses_an_unknown_revision(world):
    root, ledger = world
    handed(root, ledger)
    with pytest.raises(MoveRefused, match="could not"):
        reading.accept(ledger, actor(ledger, "review session 1"), "feat/x", reviewed="deadbeef")


def test_accept_refuses_when_the_branch_is_gone(world):
    root, ledger = world
    handed_row = handed(root, ledger)
    git(root, "branch", "-D", "feat/x")
    with pytest.raises(MoveRefused, match="could not"):
        reading.accept(ledger, actor(ledger, "review session 1"), "feat/x", reviewed=handed_row.tip)


def test_an_author_never_reads_or_accepts_their_own_work(tmp_path):
    root = repo_with_origin(tmp_path)
    path = tmp_path / "solo.md"
    path.write_text(SOLO, encoding="utf-8")
    ledger = make_ledger(root, tmp_path / "state", posts={"solo": load_post(path)})
    branch(root, "feat/x", "work")
    me = actor(ledger, "solo 1")
    core.claim(ledger, me, "feat/x")
    row = handover.hand(ledger, me, "feat/x")
    with pytest.raises(MoveRefused, match="own work"):
        reading.take(ledger, me, "feat/x")
    with pytest.raises(MoveRefused, match="own work"):
        reading.accept(ledger, me, "feat/x", reviewed=row.tip)


def test_moved_after_a_take_needs_the_readers_agreement(world):
    root, ledger = world
    handed(root, ledger)
    reading.take(ledger, actor(ledger, "review session 1"), "feat/x")
    git(root, "checkout", "-q", "feat/x")
    new = commit(root, "more", "more.txt")
    git(root, "checkout", "-q", "main")
    with pytest.raises(MoveRefused, match="agreement"):
        handover.moved(ledger, actor(ledger, "main session 1"), "feat/x", tip=new)
    row = handover.moved(ledger, actor(ledger, "main session 1"), "feat/x", tip=new, agreed_by="review session 1")
    assert row.tip == new and row.reader == "review session 1"


def test_a_return_keeps_its_reason_on_the_row(world):
    root, ledger = world
    handed(root, ledger)
    row = reading.fix(ledger, actor(ledger, "review session 1"), "feat/x", why="no test for an empty file")
    assert row.why == "no test for an empty file"
