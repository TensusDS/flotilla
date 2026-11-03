"""The person's queue (person inbox design, goal 1 and "Decided"): what waits on the person, grouped by what they are
asked to do, computed from the ledger and nothing else."""

import pytest

from flotilla.watch import inbox
from flotilla.watch.fleet import PERSON, QUESTION
from flotilla.watch.whose import Item
from watchkit import AT, PR, context, row, rows, sess

ORCH = sess("orchestrator 1", state="working")
HUMAN = {**PR, "flow": {"mode": "pr", "merge_authorized_by": "human"}}


@pytest.fixture(autouse=True)
def _in_tmp(tmp_path, monkeypatch):
    """The watch context installs post templates under its root: each test's own folder, never the checkout."""
    monkeypatch.chdir(tmp_path)


def waited(id, by_post, **fields):
    """A row whose last wait on the person was recorded by a session of `by_post`."""
    history = [{"at": AT, "move": "claim", "by": "main session 1", "post": "main"},
               {"at": AT, "move": "wait", "by": f"{by_post} session 1", "post": by_post}]
    return row(id=id, branch=f"feat/{id}", waiting_on="the person", history=history,
               **{"note": f"question {id}", **fields})


def collect(*items, profile=PR, fleet_items=(), **kw):
    ctx = context(".", me=ORCH, rows_=rows(*items), profile=profile)
    return inbox.collect(ctx, fleet_items=list(fleet_items), **kw)


def test_waits_group_by_the_post_that_recorded_them():
    found = collect(waited("r1", "sender", state="accepted"), waited("r2", "judge", state="shipped"),
                    waited("r3", "main"))
    assert {entry.branch: entry.group for entry in found} == {"feat/r1": "ship", "feat/r2": "check",
                                                              "feat/r3": "question"}


def test_an_unknown_post_is_other():
    assert [entry.group for entry in collect(waited("r1", "reviewer"))] == ["other"]


def test_a_hold_until_the_person_is_put_off_with_or_without_a_wait():
    held_wait = waited("r1", "main", held_until="the person", held_by="orchestrator 1", held_why="after the teaser")
    held_only = row(id="r2", branch="feat/r2", state="accepted", held_until="the person", held_by="orchestrator 1",
                    held_why="release on Friday")
    found = {entry.branch: entry for entry in collect(held_wait, held_only)}
    assert {branch: entry.group for branch, entry in found.items()} == {"feat/r1": "put off", "feat/r2": "put off"}
    assert found["feat/r1"].why == "after the teaser" and found["feat/r2"].why == "release on Friday"


def test_a_row_parked_in_words_stays_active():
    parked = waited("r1", "main", note="parked until after the teaser")
    [entry] = collect(parked)
    assert entry.group == "question" and entry.why == "parked until after the teaser"


def test_a_closed_row_waits_on_nobody():
    assert collect(waited("r1", "main", state="closed")) == []


def test_urgent_comes_first_and_is_a_blocker():
    plain = waited("r1", "main", updated_at="2026-09-27T09:00:00+00:00")
    hurry = waited("r2", "sender", state="accepted", urgent_at=AT, urgent_why="the demo")
    found = collect(plain, hurry)
    assert [entry.branch for entry in found] == ["feat/r2", "feat/r1"]
    assert found[0].urgent and found[0].blocker


def test_a_wait_is_a_blocker_when_its_session_has_nothing_else():
    asking = waited("r1", "main")
    assert collect(asking)[0].blocker
    other_work = row(id="r2", branch="feat/other", owner="main session 1", state="claimed")
    assert not collect(asking, other_work)[0].blocker


def test_the_person_merging_a_pr_is_an_entry():
    profile = {**PR, "pr": {"merged_by": "human"}}
    [entry] = collect(row(id="r1", state="queued"), profile=profile)
    assert (entry.group, entry.key) == ("merge", "merge:r1")


def test_approval_waits_for_the_persons_yes():
    accepted = row(id="r1", state="accepted", tip="abc1234")
    [entry] = collect(accepted, profile=HUMAN, must_approve=lambda r: True)
    assert (entry.group, entry.key) == ("approve", "approve:r1")
    assert collect(accepted, profile=HUMAN, must_approve=lambda r: False) == []


def test_an_approval_that_cannot_be_asked_counts_as_waiting():
    def broken(_row):
        raise OSError("git is gone")
    [entry] = collect(row(id="r1", state="accepted", tip="abc1234"), profile=HUMAN, must_approve=broken)
    assert entry.group == "approve"


def test_a_row_is_listed_once():
    held = waited("r1", "sender", state="accepted", held_until="the person", held_by="orchestrator 1",
                  held_why="later")
    found = collect(held, profile=HUMAN, must_approve=lambda r: True)
    assert [entry.group for entry in found] == ["put off"]


def test_permission_questions_rig_requests_and_waiting_windows_are_entries():
    items = [Item(QUESTION, "main session 2", "asks to run make; answer with /flotilla:permit", AT, who="1.5"),
             Item("rig", "", "rig: main session 2 asks for a machine", AT, who="rig:request:r7"),
             Item("rig", "", "rig: machine m1 STUCK", AT, who="rig:stuck:m1:0"),
             Item(PERSON, "", "main session 3 waits on the person (census: waiting)", "", who="main session 3"),
             Item(PERSON, "feat/x", "waits on the person: q", AT, who="q")]
    found = collect(fleet_items=items)
    assert sorted((entry.group, entry.key) for entry in found) == [
        ("permission", "question:main session 2:1.5"), ("rig", "rig:r7"), ("window", "window:main session 3")]
    assert all(entry.blocker for entry in found if entry.group in ("permission", "window"))


def test_counts_match_the_entries():
    found = collect(waited("r1", "sender", state="accepted"), waited("r2", "judge", state="shipped",
                                                                      urgent_at=AT))
    counted = inbox.counts(found)
    assert counted["total"] == len(found) == 2
    assert (counted["ship"], counted["check"], counted["urgent"]) == (1, 1, 1)


def test_text_another_session_wrote_is_one_line():
    [entry] = collect(waited("r1", "main", note="line one\nline two\x1b[2J"))
    assert "\n" not in entry.why and "\x1b" not in entry.why
