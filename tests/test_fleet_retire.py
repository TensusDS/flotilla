import pytest

from flotilla.core.storage import LocalLogStore
from flotilla.fleet import retire, spawn
from flotilla.ledger import core
from fleetkit import FakeClaude
from ledgerkit import PROFILE, actor, commit, git, make_ledger, repo_with_origin

CALLER = "spawn by main-control 1"


def raised_world(tmp_path, fake):
    root = repo_with_origin(tmp_path)
    ledger = make_ledger(root, tmp_path / "state", profile={**PROFILE, "permissions": {"mode": "auto"}},
                         run=fake, census=fake.census)
    store = LocalLogStore(tmp_path / "state" / "fleet")
    raised, _ = spawn.spawn(ledger, {"main": 1}, census=fake.census, store=store, caller=CALLER, wait=0.1,
                            poll=0.05, sleep=lambda seconds: None)
    return root, ledger, raised[0]


def do_retire(ledger, fake, name="main session 1"):
    return retire.retire(ledger, name, caller="retire by main-control 1", census=fake.census, wait=0.2, poll=0.05,
                         sleep=lambda seconds: None)


def test_the_fleet_view_lists_each_post_row_with_its_session(tmp_path):
    fake = FakeClaude()
    root, ledger, seat = raised_world(tmp_path, fake)
    view = retire.fleet_view(ledger, fake.census())
    assert [(item["name"], item["post"], item["live"], item["locked"], item["tree_exists"]) for item in view] == [
        ("main session 1", "main", True, True, True)]
    assert retire.fleet_view(ledger, None)[0]["live"] is None


def test_retire_stops_the_session_unlocks_the_tree_and_releases_the_post_row(tmp_path):
    fake = FakeClaude()
    root, ledger, seat = raised_world(tmp_path, fake)
    lines = do_retire(ledger, fake)
    assert fake.stopped == [seat.short_id]
    assert "locked" not in git(root, "worktree", "list", "--porcelain")
    assert [row.state for row in ledger.rows().values()] == ["released"]
    assert seat.seat.tree.is_dir() and any(str(seat.seat.tree) in line for line in lines)


def test_retire_names_what_the_session_leaves_behind(tmp_path):
    fake = FakeClaude()
    root, ledger, seat = raised_world(tmp_path, fake)
    (seat.seat.tree / "draft.txt").write_text("unsaved\n", encoding="utf-8")
    core.claim(ledger, actor(ledger, "main session 1"), "feat/x")
    lines = "\n".join(do_retire(ledger, fake))
    assert "1 uncommitted file" in lines
    assert "orphaned: `feat/x` (claimed)" in lines and "flotilla work adopt feat/x" in lines
    assert ledger.rows()["r2"].state == "claimed"


def test_retire_refuses_without_the_census(tmp_path):
    fake = FakeClaude()
    root, ledger, seat = raised_world(tmp_path, fake)
    fake.reachable = False
    with pytest.raises(retire.RetireRefused, match="census"):
        do_retire(ledger, fake)
    assert fake.stopped == [] and ledger.rows()["r1"].state == "reserved"


def test_retire_refuses_when_the_session_does_not_stop(tmp_path):
    fake = FakeClaude()
    root, ledger, seat = raised_world(tmp_path, fake)
    fake.stops = False
    with pytest.raises(retire.RetireRefused, match="still running"):
        do_retire(ledger, fake)
    assert ledger.rows()["r1"].state == "reserved"


def test_retire_of_a_name_without_a_post_row_is_refused(tmp_path):
    fake = FakeClaude()
    root, ledger, seat = raised_world(tmp_path, fake)
    with pytest.raises(retire.RetireRefused, match="no post row for `review session 9`"):
        do_retire(ledger, fake, "review session 9")
