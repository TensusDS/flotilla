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


def test_the_fleet_view_counts_uncommitted_files(tmp_path):
    fake = FakeClaude()
    root, ledger, seat = raised_world(tmp_path, fake)
    assert retire.fleet_view(ledger, fake.census())[0]["dirty"] == 0
    (seat.seat.tree / "draft.txt").write_text("unsaved\n", encoding="utf-8")
    assert retire.fleet_view(ledger, fake.census())[0]["dirty"] == 1


def test_retire_says_unknown_when_it_cannot_read_a_tree_that_exists(tmp_path):
    fake = FakeClaude()
    root, ledger, seat = raised_world(tmp_path, fake)
    (seat.seat.tree / ".git").unlink()
    lines = "\n".join(do_retire(ledger, fake))
    assert "state is unknown" in lines and "is gone" not in lines


def fleet_world(tmp_path, fake, counts):
    root = repo_with_origin(tmp_path)
    ledger = make_ledger(root, tmp_path / "state", profile={**PROFILE, "permissions": {"mode": "auto"}},
                         run=fake, census=fake.census)
    store = LocalLogStore(tmp_path / "state" / "fleet")
    spawn.spawn(ledger, counts, census=fake.census, store=store, caller=CALLER, wait=0.1, poll=0.05,
                sleep=lambda seconds: None)
    return ledger


def do_down(ledger, fake, me=""):
    return retire.down(ledger, caller="fleet down by the person", me=me, census=fake.census, wait=0.2, poll=0.05,
                       sleep=lambda seconds: None)


def test_fleet_down_retires_every_seat(tmp_path):
    fake = FakeClaude()
    ledger = fleet_world(tmp_path, fake, {"reviewer": 1, "sender": 1})
    lines, refused = do_down(ledger, fake)
    assert refused == 0 and retire.post_rows(ledger) == []
    assert any("retired review session 1" in line for line in lines)
    assert any("retired sender 1" in line for line in lines)


def test_fleet_down_keeps_the_callers_own_seat(tmp_path):
    fake = FakeClaude()
    ledger = fleet_world(tmp_path, fake, {"orchestrator": 1, "sender": 1})
    lines, refused = do_down(ledger, fake, me="orchestrator 1")
    assert [row.owner for row in retire.post_rows(ledger)] == ["orchestrator 1"]
    assert any("kept orchestrator 1" in line and "flotilla retire" in line for line in lines)


def test_fleet_down_refuses_whole_when_the_census_is_down(tmp_path):
    fake = FakeClaude()
    ledger = fleet_world(tmp_path, fake, {"reviewer": 1})
    fake.reachable = False
    with pytest.raises(retire.RetireRefused, match="nothing was stopped"):
        do_down(ledger, fake)
    assert fake.stopped == [] and [row.owner for row in retire.post_rows(ledger)] == ["review session 1"]


def test_fleet_down_goes_on_past_a_seat_it_cannot_retire(tmp_path):
    fake = FakeClaude()
    ledger = fleet_world(tmp_path, fake, {"reviewer": 1, "sender": 1})
    fake.stop_fails = {"review session 1"}
    lines, refused = do_down(ledger, fake)
    assert refused == 1 and [row.owner for row in retire.post_rows(ledger)] == ["review session 1"]
    assert any(line.startswith("refused review session 1:") for line in lines)


def test_fleet_down_goes_on_past_any_refused_move(tmp_path, monkeypatch):
    from flotilla.ledger.errors import MoveRefused
    fake = FakeClaude()
    ledger = fleet_world(tmp_path, fake, {"reviewer": 1, "sender": 1})
    real = retire.retire
    def refusing(ledger, name, **kwargs):
        if name == "review session 1":
            raise MoveRefused("the pre-released event script refused")
        return real(ledger, name, **kwargs)
    monkeypatch.setattr(retire, "retire", refusing)
    lines, refused = do_down(ledger, fake)
    assert refused == 1 and any("retired sender 1" in line for line in lines)
    assert any(line.startswith("refused review session 1:") and "event script" in line for line in lines)


def test_retire_stops_a_session_whose_turn_is_done(tmp_path):
    import dataclasses
    fake = FakeClaude()
    root, ledger, seat = raised_world(tmp_path, fake)
    fake.sessions = [dataclasses.replace(s, state="done") for s in fake.sessions]   # turn over, process alive
    lines = do_retire(ledger, fake)
    assert fake.stopped == [seat.short_id] and f"(stopped {seat.short_id})" in lines[0]
    assert retire.post_rows(ledger) == []


def test_fleet_down_names_the_sessions_that_stay_alive(tmp_path):
    import dataclasses

    from fleetkit import session
    fake = FakeClaude()
    ledger = fleet_world(tmp_path, fake, {"reviewer": 1})
    old_tree = tmp_path / "app-main-3"
    git(ledger.root, "worktree", "add", "-q", "-b", "fleet/main-3", str(old_tree))
    fake.sessions += [dataclasses.replace(session("main session 3", "0ld000"), cwd=str(old_tree)),
                      dataclasses.replace(session("elsewhere", "e15e00"), cwd=str(tmp_path / "other-repo")),
                      dataclasses.replace(session("person's own", "0ma000"), cwd=str(ledger.root / "src"))]
    lines, refused = do_down(ledger, fake)
    alive = [line for line in lines if line.startswith("still alive")]
    assert any("main session 3" in line and "claude stop 0ld000" in line for line in alive)
    assert any("person's own" in line for line in alive)
    assert not any("elsewhere" in line for line in alive)
    assert not any("review session 1" in line for line in alive)


def test_retire_frees_the_branch_the_dead_seats_tree_held(tmp_path):
    from flotilla.ledger import core
    fake = FakeClaude()
    root, ledger, seat = raised_world(tmp_path, fake)
    tree = seat.seat.tree
    git(tree, "switch", "-q", "-c", "feat/x")
    core.claim(ledger, actor(ledger, "main session 1"), "feat/x", tree=str(tree))
    do_retire(ledger, fake)
    assert git(tree, "rev-parse", "--abbrev-ref", "HEAD") == "HEAD"   # H36 through retire
