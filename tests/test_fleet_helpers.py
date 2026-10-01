import pytest

from flotilla.core.storage import LocalLogStore
from flotilla.fleet import helpers, spawn
from flotilla.ledger import core
from flotilla.ledger.errors import MoveRefused
from fleetkit import FakeClaude
from ledgerkit import PROFILE, actor, branch, commit, git, make_ledger, repo_with_origin

OWNER = "main session 1"


@pytest.fixture(autouse=True)
def ready_checkout(monkeypatch):
    from flotilla.core import claude_state
    monkeypatch.setattr(claude_state, "setup_problems", lambda main, **kw: ([], []))


def world(tmp_path, profile=None):
    fake = FakeClaude()
    root = repo_with_origin(tmp_path)
    ledger = make_ledger(root, tmp_path / "state", profile=profile or {**PROFILE, "permissions": {"mode": "auto"}},
                         run=fake, census=fake.census)
    tip = branch(root, "feat/x", "the parent's work")
    row = core.claim(ledger, actor(ledger, OWNER), "feat/x")
    return root, ledger, fake, row, tip


def raise_one(ledger, fake, task="write the storm tests", who=OWNER, **more):
    return helpers.raise_helper(ledger, actor(ledger, who), "feat/x", task=task, census=fake.census,
                                store=LocalLogStore(ledger.state_dir / "fleet"), caller="helper raise by " + who,
                                wait=0.1, poll=0.05, sleep=lambda seconds: None, **more)


def test_a_helper_is_raised_in_its_own_tree_from_the_parents_tip(tmp_path):
    root, ledger, fake, row, tip = world(tmp_path)
    raised = raise_one(ledger, fake)
    assert raised.seat.name == "helper 1" and raised.seat.branch == "fleet/helper-1"
    assert git(raised.seat.tree, "rev-parse", "HEAD") == tip
    seat_row = next(r for r in ledger.rows().values() if r.owner == "helper 1")
    assert (seat_row.state, seat_row.helper_of) == ("reserved", row.id)
    prompt = fake.launched[-1][-1]
    assert "write the storm tests" in prompt and "feat/x" in prompt and OWNER in prompt
    assert "flotilla helper done" in prompt


def test_only_the_owner_of_open_work_raises_a_helper(tmp_path):
    root, ledger, fake, row, tip = world(tmp_path)
    with pytest.raises(MoveRefused, match="belongs to main session 1"):
        raise_one(ledger, fake, who="minor session 1")
    assert fake.launched == []


def test_a_row_past_its_work_gets_no_helper(tmp_path):
    from flotilla.ledger import handover
    root, ledger, fake, row, tip = world(tmp_path)
    handover.hand(ledger, actor(ledger, OWNER), "feat/x")
    with pytest.raises(MoveRefused, match="claimed or fixing"):
        raise_one(ledger, fake)


def test_the_helper_limit_and_the_memory_floor_hold(tmp_path, monkeypatch):
    root, ledger, fake, row, tip = world(tmp_path, {**PROFILE, "permissions": {"mode": "auto"},
                                                    "fleet": {"helpers_per_seat": 1}})
    raise_one(ledger, fake)
    with pytest.raises(MoveRefused, match="already has 1 live helper"):
        raise_one(ledger, fake)
    monkeypatch.setattr(spawn, "available_mb", lambda: 500)
    ledger.profile["fleet"]["helpers_per_seat"] = 5
    with pytest.raises(MoveRefused, match="500 MB"):
        raise_one(ledger, fake)
    assert raise_one(ledger, fake, anyway=True).seat.name == "helper 2"


def test_a_helper_finishes_with_a_record_and_a_letter_for_its_parent(tmp_path):
    root, ledger, fake, row, tip = world(tmp_path)
    raised = raise_one(ledger, fake)
    done_tip = commit(raised.seat.tree, "storm tests", "storm_test.txt")
    released, letter = helpers.done(ledger, actor(ledger, "helper 1"), summary="three storm tests, all green")
    assert released.state == "released"
    evidence = released.history[-1]["evidence"]
    assert (evidence["helped"], evidence["tip"], evidence["summary"]) == (row.id, done_tip,
                                                                          "three storm tests, all green")
    assert letter.startswith(f"letter for {OWNER}")
    assert "merge fleet/helper-1" in letter and 'flotilla retire "helper 1"' in letter


def test_done_needs_a_summary_and_a_helper(tmp_path):
    root, ledger, fake, row, tip = world(tmp_path)
    raise_one(ledger, fake)
    with pytest.raises(MoveRefused, match="--summary"):
        helpers.done(ledger, actor(ledger, "helper 1"), summary=" ")
    with pytest.raises(MoveRefused, match="not a helper"):
        helpers.done(ledger, actor(ledger, OWNER), summary="x")


def test_spawn_does_not_raise_helpers(tmp_path):
    root, ledger, fake, row, tip = world(tmp_path)
    with pytest.raises(spawn.SpawnRefused, match="flotilla helper raise"):
        spawn.plan(ledger, {"helper": 1}, census=fake.census, store=LocalLogStore(tmp_path / "s"), reserve=False)


def finished(tmp_path):
    root, ledger, fake, row, tip = world(tmp_path)
    raised = raise_one(ledger, fake)
    commit(raised.seat.tree, "storm tests", "storm_test.txt")
    helpers.done(ledger, actor(ledger, "helper 1"), summary="three storm tests")
    return root, ledger, fake, raised


def test_a_finished_helper_is_retired_by_the_command_its_letter_names(tmp_path):
    from flotilla.fleet import retire
    root, ledger, fake, raised = finished(tmp_path)
    lines = retire.retire(ledger, "helper 1", caller="retire by main session 1", census=fake.census, wait=0.2,
                          poll=0.05, sleep=lambda seconds: None)
    assert any(line.startswith("retired helper 1") for line in lines)
    assert "helper 1" not in [s.name for s in fake.census()]
    assert git(root, "rev-parse", "--verify", "fleet/helper-1")   # its branch is kept for the merge


def test_fleet_down_stops_a_finished_helper(tmp_path):
    from flotilla.fleet import retire
    root, ledger, fake, raised = finished(tmp_path)
    lines, refused = retire.down(ledger, caller="fleet down by the person", me="", census=fake.census, wait=0.2,
                                 poll=0.05, sleep=lambda seconds: None)
    assert refused == 0 and "helper 1" not in [s.name for s in fake.census()]
    assert any(line.startswith("retired helper 1") for line in lines)


def test_done_refuses_while_work_is_uncommitted(tmp_path):
    root, ledger, fake, row, tip = world(tmp_path)
    raised = raise_one(ledger, fake)
    (raised.seat.tree / "half.txt").write_text("not committed\n", encoding="utf-8")
    with pytest.raises(MoveRefused, match="1 uncommitted"):
        helpers.done(ledger, actor(ledger, "helper 1"), summary="done")


def test_the_letter_reads_like_every_other_letter_and_the_prompt_does_not_send_a_helper_switching(tmp_path):
    root, ledger, fake, row, tip = world(tmp_path)
    raised = raise_one(ledger, fake)
    _, letter = helpers.done(ledger, actor(ledger, "helper 1"), summary="nothing to do")
    assert letter.startswith(f"letter for {OWNER} - send it with SendMessage; an idle background session is woken")
    system = fake.launched[-1][fake.launched[-1].index("--append-system-prompt") + 1]
    assert "tree switch" not in system


def test_a_helper_steps_over_a_seat_branch_left_by_an_older_fleet(tmp_path):
    """Review of 0.6.0, I3: helpers built their taken names without the seat branches still in the repository, so a
    project counting from 1 again met `fleet/helper-1` at `git worktree add -b` and burnt a number per retry."""
    root, ledger, fake, row, tip = world(tmp_path)
    git(root, "branch", "fleet/helper-1")
    raised = raise_one(ledger, fake)
    assert raised.seat.name == "helper 2" and raised.seat.branch == "fleet/helper-2"
