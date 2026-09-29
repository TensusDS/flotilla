import pytest

from flotilla.core.storage import LocalLogStore
from flotilla.fleet import spawn
from fleetkit import FakeClaude, session
from ledgerkit import PROFILE, commit, git, make_ledger, repo_with_origin

CALLER = "spawn by main-control 1"


@pytest.fixture(autouse=True)
def ready_checkout(monkeypatch):
    """The fake world has no `claude plugin list` and no ~/.claude.json of its own; setup checks are tested in
    test_claude_state.py and by the tests below that replace this answer."""
    from flotilla.core import claude_state
    monkeypatch.setattr(claude_state, "setup_problems", lambda main, **kw: ([], []))


def world(tmp_path, fake, profile=None):
    root = repo_with_origin(tmp_path)
    ledger = make_ledger(root, tmp_path / "state", profile=profile or {**PROFILE, "permissions": {"mode": "auto"}},
                         run=fake, census=fake.census)
    return root, ledger, LocalLogStore(tmp_path / "state" / "fleet")


def run(ledger, store, fake, counts, **more):
    return spawn.spawn(ledger, counts, census=fake.census, store=store, caller=CALLER, wait=0.2, poll=0.05,
                       sleep=lambda seconds: None, **more)


def test_spawn_raises_acceptors_first_each_in_its_own_tree(tmp_path):
    fake = FakeClaude()
    root, ledger, store = world(tmp_path, fake)
    raised, warnings = run(ledger, store, fake, {"main": 1, "review": 1})
    assert [item.seat.name for item in raised] == ["review session 1", "main session 1"]
    assert all(item.short_id for item in raised) and warnings == []
    for item in raised:
        assert item.seat.tree.is_dir()
        assert git(root, "rev-parse", "--abbrev-ref", "HEAD") == "main"
    assert [cmd[3] for cmd in fake.launched] == ["review session 1", "main session 1"]


def test_each_seat_gets_a_post_row_recorded_by_the_spawner(tmp_path):
    fake = FakeClaude()
    root, ledger, store = world(tmp_path, fake)
    raised, _ = run(ledger, store, fake, {"main": 1})
    row = next(iter(ledger.rows().values()))
    assert (row.state, row.owner, row.branch, row.tree) == ("reserved", "main session 1", "fleet/main-1",
                                                           str(raised[0].seat.tree))
    event = ledger.store.read(ledger.repo_key).records[-1]
    assert (event["by"], event["via"], event["caller"]) == ("main session 1", "spawn", CALLER)


def test_the_tree_is_locked_while_the_session_lives(tmp_path):
    fake = FakeClaude()
    root, ledger, store = world(tmp_path, fake)
    raised, _ = run(ledger, store, fake, {"main": 1})
    listing = git(root, "worktree", "list", "--porcelain")
    assert f"worktree {raised[0].seat.tree}" in listing and "locked flotilla: main session 1" in listing


def test_names_skip_live_sessions_and_ledger_names(tmp_path):
    fake = FakeClaude([session("main session 4", "aaaaaa")])
    root, ledger, store = world(tmp_path, fake)
    raised, _ = run(ledger, store, fake, {"main": 1})
    assert raised[0].seat.name == "main session 5"


def test_a_second_sender_is_refused_naming_the_live_one(tmp_path):
    fake = FakeClaude([session("sender 1", "aaaaaa")])
    root, ledger, store = world(tmp_path, fake)
    with pytest.raises(spawn.SpawnRefused, match="one-copy"):
        run(ledger, store, fake, {"sender": 1})
    assert fake.launched == []


def test_a_failed_launch_takes_back_the_tree_the_branch_and_the_row(tmp_path):
    fake = FakeClaude(fail_launch=True)
    root, ledger, store = world(tmp_path, fake)
    with pytest.raises(spawn.SpawnRefused, match="not logged in"):
        run(ledger, store, fake, {"main": 1})
    assert not (tmp_path / "app-main-1").exists()
    assert git(root, "branch", "--list", "fleet/main-1") == ""
    assert [row.state for row in ledger.rows().values()] == ["released"]


def test_a_session_not_yet_in_the_census_is_reported_not_retried(tmp_path):
    fake = FakeClaude(appear=False)
    root, ledger, store = world(tmp_path, fake)
    raised, _ = run(ledger, store, fake, {"main": 1})
    assert raised[0].short_id is None and "do not launch it again" in raised[0].note
    assert raised[0].seat.tree.is_dir() and len(fake.launched) == 1
    assert [row.state for row in ledger.rows().values()] == ["reserved"]


def test_spawn_refuses_without_the_census(tmp_path):
    fake = FakeClaude(reachable=False)
    root, ledger, store = world(tmp_path, fake)
    with pytest.raises(spawn.SpawnRefused, match="census"):
        run(ledger, store, fake, {"main": 1})
    assert fake.launched == []


def test_a_dry_run_plans_names_and_changes_nothing(tmp_path):
    fake = FakeClaude()
    root, ledger, store = world(tmp_path, fake)
    seats, _ = spawn.plan(ledger, {"main": 2}, census=fake.census, store=store, reserve=False)
    assert [seat.name for seat in seats] == ["main session 1", "main session 2"]
    assert fake.launched == [] and ledger.rows() == {} and not seats[0].tree.exists()
    seats, _ = spawn.plan(ledger, {"main": 1}, census=fake.census, store=store, reserve=False)
    assert seats[0].name == "main session 1"


def test_a_seat_whose_tree_already_exists_is_refused_before_anything_starts(tmp_path):
    fake = FakeClaude()
    root, ledger, store = world(tmp_path, fake)
    (tmp_path / "app-main-1").mkdir()
    with pytest.raises(spawn.SpawnRefused, match="app-main-1 already exists"):
        run(ledger, store, fake, {"main": 1})
    assert fake.launched == []


def test_a_branch_made_after_the_plan_is_never_deleted(tmp_path):
    fake = FakeClaude()
    root, ledger, store = world(tmp_path, fake)
    seats, _ = spawn.plan(ledger, {"main": 1}, census=fake.census, store=store, reserve=True)
    git(root, "checkout", "-q", "-b", "fleet/main-1")
    kept = commit(root, "a person's work", "mine.txt")
    git(root, "checkout", "-q", "main")
    with pytest.raises(spawn.SpawnRefused, match="fleet/main-1"):
        spawn.raise_seat(ledger, seats[0], caller=CALLER, census=fake.census, wait=0.1, poll=0.05,
                         sleep=lambda seconds: None)
    assert git(root, "rev-parse", "fleet/main-1") == kept


def test_a_tree_made_after_the_plan_is_never_removed(tmp_path):
    fake = FakeClaude()
    root, ledger, store = world(tmp_path, fake)
    seats, _ = spawn.plan(ledger, {"main": 1}, census=fake.census, store=store, reserve=True)
    seats[0].tree.mkdir()
    (seats[0].tree / "draft.txt").write_text("unsaved\n", encoding="utf-8")
    with pytest.raises(spawn.SpawnRefused, match="already exists"):
        spawn.raise_seat(ledger, seats[0], caller=CALLER, census=fake.census, wait=0.1, poll=0.05,
                         sleep=lambda seconds: None)
    assert (seats[0].tree / "draft.txt").read_text(encoding="utf-8") == "unsaved\n"


def test_a_launch_that_times_out_but_started_is_kept(tmp_path):
    fake = FakeClaude(timeout=True)
    root, ledger, store = world(tmp_path, fake)
    raised, _ = run(ledger, store, fake, {"main": 1})
    assert raised[0].short_id and raised[0].seat.tree.is_dir() and "timed out" in raised[0].note
    assert [row.state for row in ledger.rows().values()] == ["reserved"]


def test_a_launch_that_exits_non_zero_but_started_is_kept(tmp_path):
    fake = FakeClaude(appear_then_fail=True)
    root, ledger, store = world(tmp_path, fake)
    raised, _ = run(ledger, store, fake, {"main": 1})
    assert raised[0].short_id and "not logged in" in raised[0].note
    assert [row.state for row in ledger.rows().values()] == ["reserved"]


def test_a_refused_post_row_takes_back_the_tree_and_the_branch(tmp_path):
    import sys
    fake = FakeClaude()
    root, ledger, store = world(tmp_path, fake)
    ledger.events = {"pre-reserved": (f"#!{sys.executable}\nimport sys; print('no posts today'); sys.exit(2)\n"
                                      .encode(), True)}
    with pytest.raises(spawn.SpawnRefused, match="no posts today"):
        run(ledger, store, fake, {"main": 1})
    assert not (tmp_path / "app-main-1").exists() and git(root, "branch", "--list", "fleet/main-1") == ""
    assert ledger.rows() == {} and fake.launched == []


def test_a_refused_release_still_takes_back_git_and_keeps_the_first_cause(tmp_path):
    import sys
    fake = FakeClaude(fail_launch=True)
    root, ledger, store = world(tmp_path, fake)
    ledger.events = {"pre-released": (f"#!{sys.executable}\nimport sys; sys.exit(2)\n".encode(), True)}
    with pytest.raises(spawn.SpawnRefused, match="not logged in.*could not be released"):
        run(ledger, store, fake, {"main": 1})
    assert not (tmp_path / "app-main-1").exists() and git(root, "branch", "--list", "fleet/main-1") == ""


def test_a_spawn_that_stops_partway_names_the_sessions_already_raised(tmp_path):
    fake = FakeClaude(fail_on={"main session 1"})
    root, ledger, store = world(tmp_path, fake)
    with pytest.raises(spawn.SpawnStopped) as stopped:
        run(ledger, store, fake, {"main": 1, "review": 1})
    assert [item.seat.name for item in stopped.value.raised] == ["review session 1"]
    assert "not logged in" in str(stopped.value)


def test_spawn_refuses_when_the_main_checkout_is_not_ready(tmp_path, monkeypatch):
    from flotilla.core import claude_state
    monkeypatch.setattr(claude_state, "setup_problems",
                        lambda main, **kw: ([f"flotilla is not enabled in {main}: run ..."], []))
    fake = FakeClaude()
    root, ledger, store = world(tmp_path, fake)
    with pytest.raises(spawn.SpawnRefused, match="not enabled"):
        spawn.plan(ledger, {"main": 1}, census=fake.census, store=store, reserve=False)
    assert fake.launched == []


def test_a_dry_run_lists_what_spawn_would_refuse_as_warnings(tmp_path, monkeypatch):
    from flotilla.core import claude_state
    monkeypatch.setattr(claude_state, "setup_problems", lambda main, **kw: (["not trusted"], ["unknown plugin"]))
    fake = FakeClaude()
    root, ledger, store = world(tmp_path, fake)
    seats, warnings = spawn.plan(ledger, {"main": 1}, census=fake.census, store=store, reserve=False, strict=False)
    assert len(seats) == 1 and "not trusted" in warnings and "unknown plugin" in warnings
