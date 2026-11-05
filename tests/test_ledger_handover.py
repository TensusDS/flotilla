import sys

import pytest

from flotilla.ledger import core, delivery, gitq, handover, reading, receipts
from flotilla.ledger.errors import MoveRefused
from ledgerkit import PROFILE, actor, branch, commit, drive, git, make_ledger, repo_with_origin

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
    row = handover.wait(ledger, actor(ledger, "main session 1"), "feat/x", on="the person", why="needs a decision")
    assert (row.waiting_on, row.note, row.state) == ("the person", "needs a decision", "claimed")
    assert handover.wait(ledger, actor(ledger, "main session 1"), "feat/x", clear=True).waiting_on == ""


def test_only_the_owner_or_the_reader_waits(world):
    root, ledger = world
    claimed(root, ledger)
    with pytest.raises(MoveRefused, match="owner"):
        handover.wait(ledger, actor(ledger, "minor session 1"), "feat/x", on="the person", why="x")


def test_a_state_change_ends_a_recorded_wait(world):
    root, ledger = world
    claimed(root, ledger)
    handover.wait(ledger, actor(ledger, "main session 1"), "feat/x", on="the person", why="needs a decision")
    row = handover.hand(ledger, actor(ledger, "main session 1"), "feat/x")
    assert (row.waiting_on, row.note) == ("", "")


def test_moving_an_accepted_tip_needs_the_reader_and_drops_the_verdict(world):
    root, ledger = world
    claimed(root, ledger)
    row = handover.hand(ledger, actor(ledger, "main session 1"), "feat/x")
    reading.take(ledger, actor(ledger, "review session 1"), "feat/x")
    reading.accept(ledger, actor(ledger, "review session 1"), "feat/x", reviewed=row.tip)
    git(root, "checkout", "-q", "feat/x")
    new = commit(root, "more", "more.txt")
    git(root, "checkout", "-q", "main")
    with pytest.raises(MoveRefused, match="agreement"):
        handover.moved(ledger, actor(ledger, "main session 1"), "feat/x", tip=new)
    moved = handover.moved(ledger, actor(ledger, "main session 1"), "feat/x", tip=new, agreed_by="review session 1")
    assert (moved.state, moved.verdict, moved.tip) == ("handed", "", new)


def test_the_sender_records_a_wait_on_the_row_it_must_move(tmp_path):
    root = repo_with_origin(tmp_path)
    ledger = make_ledger(root, tmp_path / "state", profile={**PROFILE, "flow": {"mode": "direct"}})
    drive(root, ledger)
    delivery.queue(ledger, actor(ledger, "sender 1"), "feat/x")
    row = handover.wait(ledger, actor(ledger, "sender 1"), "feat/x", on="the person", why="asked about the batch")
    assert row.waiting_on == "the person"


def test_a_session_that_does_not_move_the_row_cannot_record_its_wait(tmp_path):
    root = repo_with_origin(tmp_path)
    ledger = make_ledger(root, tmp_path / "state", profile={**PROFILE, "flow": {"mode": "direct"}})
    drive(root, ledger)
    with pytest.raises(MoveRefused, match="records a wait"):
        handover.wait(ledger, actor(ledger, "review session 2"), "feat/x", on="x", why="y")


def test_a_branch_with_no_commits_of_its_own_is_not_handed_over(tmp_path):
    root = repo_with_origin(tmp_path)
    ledger = make_ledger(root, tmp_path / "state")
    git(root, "branch", "fix/empty", "main")
    core.claim(ledger, actor(ledger, "main session 1"), "fix/empty")
    with pytest.raises(MoveRefused, match="no commits of its own.*--settled-by"):
        handover.hand(ledger, actor(ledger, "main session 1"), "fix/empty")


def accepted_then_moved_on(tmp_path, **kwargs):
    root = repo_with_origin(tmp_path)
    ledger = make_ledger(root, tmp_path / "state", **kwargs)
    claimed(root, ledger)
    row = handover.hand(ledger, actor(ledger, "main session 1"), "feat/x")
    reading.take(ledger, actor(ledger, "review session 1"), "feat/x")
    reading.accept(ledger, actor(ledger, "review session 1"), "feat/x", reviewed=row.tip)
    git(root, "checkout", "-q", "feat/x")
    new = commit(root, "merged main", "merged.txt")
    git(root, "checkout", "-q", "main")
    return ledger, new


def test_a_moved_tip_goes_back_to_reading_when_its_reader_is_gone(tmp_path):
    ledger, new = accepted_then_moved_on(tmp_path, live=("main session 1", "sender 1", "orchestrator 1"))
    row = handover.moved(ledger, actor(ledger, "main session 1"), "feat/x", tip=new)
    assert (row.state, row.tip, row.verdict, row.reader, row.taken) == ("handed", new, "", "", False)
    assert row.history[-1]["evidence"]["reader_gone"] == "review session 1"


def test_a_live_reader_still_has_to_agree(tmp_path):
    ledger, new = accepted_then_moved_on(tmp_path)
    with pytest.raises(MoveRefused, match="agreement"):
        handover.moved(ledger, actor(ledger, "main session 1"), "feat/x", tip=new)


def test_a_census_that_cannot_be_asked_keeps_the_refusal(tmp_path):
    from flotilla.core.census import CensusUnavailable

    def no_census():
        raise CensusUnavailable("claude agents timed out")
    ledger, new = accepted_then_moved_on(tmp_path, census=no_census)
    with pytest.raises(MoveRefused, match="agreement"):
        handover.moved(ledger, actor(ledger, "main session 1"), "feat/x", tip=new)


def _census(*names):
    from flotilla.core.census import Session
    return lambda: [Session(name=name, session_id=name, kind="background", pid=None, short_id=None, status="idle",
                            state=None, cwd="", started_at_ms=None) for name in names]


def test_a_short_name_resolves_to_the_one_live_session(tmp_path):
    """Twosuns, 2026-11-01, item 5: a wait on "orchestrator 4" was recorded word for word while the live session was
    "twosuns-orchestrator 4", so the watch could never tell it was over."""
    root = repo_with_origin(tmp_path)
    ledger = make_ledger(root, tmp_path / "state", census=_census("main session 1", "twosuns-orchestrator 4"))
    claimed(root, ledger)
    row = handover.wait(ledger, actor(ledger, "main session 1"), "feat/x", on="orchestrator 4", why="its call")
    assert row.waiting_on == "twosuns-orchestrator 4"
    assert row.history[-1]["evidence"]["resolved_from"] == "orchestrator 4"
    from flotilla.ledger.commands import _waited
    assert _waited(row, "orchestrator 4")[1] == 'waits on twosuns-orchestrator 4 (resolved from "orchestrator 4")'


def test_a_gone_session_with_no_live_match_is_refused(tmp_path):
    root = repo_with_origin(tmp_path)
    ledger = make_ledger(root, tmp_path / "state", census=_census("main session 1", "orchestrator 5"))
    core.reserve(ledger, actor(ledger, "orchestrator 4"), "orchestrator-home")   # the ledger knows the name
    claimed(root, ledger)
    with pytest.raises(MoveRefused, match=r"`orchestrator 4` is not alive.*main session 1, orchestrator 5"):
        handover.wait(ledger, actor(ledger, "main session 1"), "feat/x", on="orchestrator 4", why="its call")


def test_two_live_matches_are_refused_as_ambiguous(tmp_path):
    root = repo_with_origin(tmp_path)
    ledger = make_ledger(root, tmp_path / "state",
                         census=_census("main session 1", "game-orchestrator 4", "site-orchestrator 4"))
    claimed(root, ledger)
    with pytest.raises(MoveRefused, match="ambiguous.*game-orchestrator 4, site-orchestrator 4"):
        handover.wait(ledger, actor(ledger, "main session 1"), "feat/x", on="orchestrator 4", why="its call")


def test_free_words_stay_free(tmp_path):
    root = repo_with_origin(tmp_path)
    ledger = make_ledger(root, tmp_path / "state", census=_census("main session 1"))
    claimed(root, ledger)
    row = handover.wait(ledger, actor(ledger, "main session 1"), "feat/x", on="the lane", why="a suite runs")
    assert row.waiting_on == "the lane" and "resolved_from" not in (row.history[-1].get("evidence") or {})


def test_an_unaskable_census_records_the_name_as_written(tmp_path):
    from flotilla.core.census import CensusUnavailable

    def unaskable():
        raise CensusUnavailable("no sessions directory")
    root = repo_with_origin(tmp_path)
    ledger = make_ledger(root, tmp_path / "state", census=unaskable)
    claimed(root, ledger)
    row = handover.wait(ledger, actor(ledger, "main session 1"), "feat/x", on="orchestrator 4", why="its call")
    assert row.waiting_on == "orchestrator 4"
    assert "not checked" in row.history[-1]["evidence"]["unchecked"]


def test_a_known_gone_name_is_never_redirected_to_a_live_suffix_match(tmp_path):
    """Security review of the short-name wait: `orchestrator 4` is a session the ledger knows and it is gone; a live
    `other-orchestrator 4` is another session, so the wait is refused and the candidate named, not redirected."""
    root = repo_with_origin(tmp_path)
    ledger = make_ledger(root, tmp_path / "state", census=_census("main session 1", "other-orchestrator 4"))
    core.reserve(ledger, actor(ledger, "orchestrator 4"), "orchestrator-home")
    claimed(root, ledger)
    with pytest.raises(MoveRefused, match="if you mean other-orchestrator 4, name it in full"):
        handover.wait(ledger, actor(ledger, "main session 1"), "feat/x", on="orchestrator 4", why="its call")


def test_a_refused_wait_shows_session_names_as_data(tmp_path):
    root = repo_with_origin(tmp_path)
    ledger = make_ledger(root, tmp_path / "state",
                         census=_census("main session 1", "a-orchestrator 4\x1b[2J", "b-orchestrator 4\x1b[2J"))
    claimed(root, ledger)
    with pytest.raises(MoveRefused) as refused:
        handover.wait(ledger, actor(ledger, "main session 1"), "feat/x", on="orchestrator 4\x1b[2J", why="x")
    assert "\x1b" not in str(refused.value)


def test_the_person_is_a_wait_target_after_the_person_has_moved_on_the_ledger(world):
    """void-lanes, 2026-11-04: an approve or a take-in records "the person" as a mover, and a wait on the person -
    the form the flotilla skill prescribes - was refused as a wait on a session that is not alive."""
    from types import SimpleNamespace
    root, ledger = world
    claimed(root, ledger)
    rows = dict(ledger.rows())
    rows["r-person"] = SimpleNamespace(owner="main session 1", reader="", history=[{"by": "the person"}])
    assert handover._whom(ledger, rows, "the person") == ("the person", {})


def test_the_person_is_never_a_live_session_by_suffix(tmp_path):
    """Review of 0.29.1, M6: a live session whose name ends in " the person" took a wait on the person."""
    root = repo_with_origin(tmp_path)
    ledger = make_ledger(root, tmp_path / "state", live=("main session 1", "ask the person"))
    assert handover._whom(ledger, ledger.rows(), "the person") == ("the person", {})
