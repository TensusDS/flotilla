import subprocess
import sys
import time
from pathlib import Path

import pytest

from flotilla.core.census import CensusUnavailable
from flotilla.ledger import core
from flotilla.ledger.errors import MoveRefused
from ledgerkit import PROFILE, actor, branch, git, make_ledger, repo_with_origin

ROOT = Path(__file__).resolve().parent.parent
TESTS = Path(__file__).resolve().parent


@pytest.fixture()
def world(tmp_path):
    root = repo_with_origin(tmp_path)
    return root, make_ledger(root, tmp_path / "state")


def test_claim_records_owner_and_fork_point(world):
    root, ledger = world
    branch(root, "feat/x", "one")
    row = core.claim(ledger, actor(ledger, "main session 1"), "feat/x", ref="LIN-1")
    assert (row.id, row.state, row.owner, row.ref) == ("r1", "claimed", "main session 1", "LIN-1")
    assert row.base == git(root, "rev-parse", "origin/main")


def test_a_claimed_branch_cannot_be_claimed_again(world):
    root, ledger = world
    core.claim(ledger, actor(ledger, "main session 1"), "feat/x")
    with pytest.raises(MoveRefused, match="main session 1"):
        core.claim(ledger, actor(ledger, "minor session 1"), "feat/x")


def test_the_same_ref_twice_is_refused_by_name(world):
    root, ledger = world
    core.claim(ledger, actor(ledger, "main session 1"), "feat/x", ref="LIN-1")
    with pytest.raises(MoveRefused, match=r"LIN-1.*feat/x.*--also"):
        core.claim(ledger, actor(ledger, "minor session 1"), "feat/y", ref="LIN-1")


def test_also_allows_it_and_records_why(world):
    root, ledger = world
    core.claim(ledger, actor(ledger, "main session 1"), "feat/x", ref="LIN-1")
    row = core.claim(ledger, actor(ledger, "minor session 1"), "feat/y", ref="LIN-1", also="the UI half")
    assert row.history[-1]["evidence"] == {"also": "the UI half"}


def test_requires_resolves_branches_to_rows(world):
    root, ledger = world
    core.claim(ledger, actor(ledger, "main session 1"), "feat/x")
    row = core.claim(ledger, actor(ledger, "minor session 1"), "feat/y", requires=["feat/x"])
    assert row.requires == ["r1"]


def test_an_unknown_requirement_is_refused(world):
    root, ledger = world
    with pytest.raises(MoveRefused, match="feat/none"):
        core.claim(ledger, actor(ledger, "main session 1"), "feat/y", requires=["feat/none"])


def test_after_resolves_branches_to_rows_apart_from_requires(world):
    root, ledger = world
    core.claim(ledger, actor(ledger, "main session 1"), "feat/x")
    row = core.claim(ledger, actor(ledger, "minor session 1"), "tool/measure-x", after=["feat/x"])
    assert (row.after, row.requires) == (["r1"], [])


def test_an_unknown_after_is_refused_like_an_unknown_requirement(world):
    root, ledger = world
    with pytest.raises(MoveRefused, match=r"--after `feat/none`: no such row or open branch"):
        core.claim(ledger, actor(ledger, "main session 1"), "feat/y", after=["feat/none"])
    assert ledger.rows() == {}


def test_a_claim_without_after_writes_no_after_field(world):
    root, ledger = world
    row = core.claim(ledger, actor(ledger, "main session 1"), "feat/x")
    events = ledger.store.read(ledger.repo_key).records
    assert "after" not in events[-1]["fields"] and row.after == []


def test_a_post_that_may_not_claim_is_refused(world):
    root, ledger = world
    with pytest.raises(MoveRefused, match="may not `claim`"):
        core.claim(ledger, actor(ledger, "review session 1"), "feat/x")


def test_one_reserved_post_per_session(world):
    root, ledger = world
    core.reserve(ledger, actor(ledger, "review session 1"), "fleet/review-1")
    with pytest.raises(MoveRefused, match="already holds"):
        core.reserve(ledger, actor(ledger, "review session 1"), "fleet/review-1b")


def test_release_needs_a_reason(world):
    root, ledger = world
    core.claim(ledger, actor(ledger, "main session 1"), "feat/x")
    with pytest.raises(MoveRefused, match="--why"):
        core.release(ledger, actor(ledger, "main session 1"), "feat/x", why=" ")
    assert core.release(ledger, actor(ledger, "main session 1"), "feat/x", why="dropped").state == "released"


def test_a_live_owners_work_is_released_only_by_them(world):
    root, ledger = world
    core.claim(ledger, actor(ledger, "main session 1"), "feat/x")
    with pytest.raises(MoveRefused, match="alive"):
        core.release(ledger, actor(ledger, "orchestrator 1"), "feat/x", why="tidy")


def test_a_dead_owners_work_can_be_released_by_another(tmp_path):
    root = repo_with_origin(tmp_path)
    ledger = make_ledger(root, tmp_path / "state", live=("orchestrator 1",))
    core.claim(ledger, actor(ledger, "main session 9"), "feat/x")
    assert core.release(ledger, actor(ledger, "orchestrator 1"), "feat/x", why="owner gone").state == "released"


def test_release_with_the_census_unreachable_is_refused_not_guessed(tmp_path):
    root = repo_with_origin(tmp_path)

    def broken():
        raise CensusUnavailable("`claude` is not on PATH")

    ledger = make_ledger(root, tmp_path / "state", census=broken)
    core.claim(ledger, actor(ledger, "main session 1"), "feat/x")
    with pytest.raises(MoveRefused, match="could not ask"):
        core.release(ledger, actor(ledger, "orchestrator 1"), "feat/x", why="tidy")


def test_two_claims_at_once_one_wins(tmp_path):
    # Every process imports, builds its ledger, reports ready, then waits at a start line; the claims are
    # released together. Without the start line, interpreter start-up spreads them apart and the race never
    # happens: measured 2026-09-24, zero of five runs failed with the lock removed.
    root = repo_with_origin(tmp_path)
    go = tmp_path / "go"
    script = (
        "import sys, time\n"
        f"sys.path.insert(0, {str(ROOT)!r}); sys.path.insert(0, {str(TESTS)!r})\n"
        "from pathlib import Path\n"
        "from ledgerkit import actor, make_ledger\n"
        "from flotilla.ledger import core\n"
        "from flotilla.ledger.errors import MoveRefused\n"
        "from flotilla.core import caller\n"
        "caller.has_terminal = lambda: True   # the harness stands for the person naming who acts (--as)\n"
        f"ledger = make_ledger({str(root)!r}, {str(tmp_path / 'state')!r})\n"
        "me = actor(ledger, sys.argv[1])\n"
        f"Path({str(tmp_path)!r}, 'ready-' + sys.argv[2]).touch()\n"
        f"while not Path({str(go)!r}).exists():\n"
        "    time.sleep(0.001)\n"
        "try:\n"
        "    core.claim(ledger, me, 'feat/x')\n"
        "    print('won')\n"
        "except MoveRefused:\n"
        "    print('refused')\n"
    )
    names = [f"{post} session {n}" for n in range(1, 5) for post in ("main", "minor")]
    procs = [subprocess.Popen([sys.executable, "-c", script, name, str(i)], stdout=subprocess.PIPE, text=True)
             for i, name in enumerate(names)]
    deadline = time.monotonic() + 60
    while len(list(tmp_path.glob("ready-*"))) < len(names) and time.monotonic() < deadline:
        time.sleep(0.01)
    go.touch()
    results = sorted(proc.communicate(timeout=120)[0].strip() for proc in procs)
    assert results == ["refused"] * (len(names) - 1) + ["won"]


def test_every_event_records_who_really_called(world):
    root, ledger = world
    core.claim(ledger, actor(ledger, "main session 1"), "feat/x")
    event = ledger.store.read(ledger.repo_key).records[-1]
    assert event["caller"].startswith("none") and event["via"] == "as"


def test_a_release_racing_a_new_claim_does_not_take_a_live_owners_row(world, monkeypatch):
    root, ledger = world
    core.claim(ledger, actor(ledger, "main session 1"), "feat/x")
    monkeypatch.setattr(ledger, "rows", lambda: {})   # the unlocked pre-check saw no row yet
    with pytest.raises(MoveRefused, match="run it again"):
        core.release(ledger, actor(ledger, "orchestrator 1"), "feat/x", why="tidy")


DIRECT_FLOW = {"schema": 1, "trunk": {"branch": "main"}, "flow": {"mode": "direct"}}


def test_a_row_another_row_fulfilled_is_settled_by_it(tmp_path):
    from ledgerkit import git, repo_with_origin, shipped_direct
    root = repo_with_origin(tmp_path)
    ledger = make_ledger(root, tmp_path / "state", profile={**PROFILE, **DIRECT_FLOW})
    done = shipped_direct(root, ledger, "feat/x")
    git(root, "branch", "fix/y", "main")
    core.claim(ledger, actor(ledger, "main session 1"), "fix/y")
    row = core.release(ledger, actor(ledger, "main session 1"), "fix/y", settled_by="feat/x")
    assert row.state == "released"
    evidence = row.history[-1]["evidence"]
    assert evidence["settled_by"] == done.id and "settled by feat/x" in evidence["why"]


def test_settled_by_a_reused_branch_name_finds_the_delivered_row(tmp_path):
    from flotilla.ledger import judging
    from ledgerkit import git, repo_with_origin, shipped_direct
    root = repo_with_origin(tmp_path)
    ledger = make_ledger(root, tmp_path / "state", profile={**PROFILE, **DIRECT_FLOW})
    done = shipped_direct(root, ledger, "feat/x")
    judging.close(ledger, actor(ledger, "main session 1"), "feat/x")
    core.claim(ledger, actor(ledger, "main session 2"), "feat/x")   # the name is used again, for new work
    git(root, "branch", "fix/y", "main")
    core.claim(ledger, actor(ledger, "main session 1"), "fix/y")
    row = core.release(ledger, actor(ledger, "main session 1"), "fix/y", settled_by="feat/x")
    assert row.history[-1]["evidence"]["settled_by"] == done.id


def test_settled_by_an_undelivered_row_is_refused(tmp_path):
    from ledgerkit import drive, git, repo_with_origin
    root = repo_with_origin(tmp_path)
    ledger = make_ledger(root, tmp_path / "state", profile={**PROFILE, **DIRECT_FLOW})
    drive(root, ledger, "feat/x", to="claimed")
    git(root, "branch", "fix/y", "main")
    core.claim(ledger, actor(ledger, "main session 1"), "fix/y")
    with pytest.raises(MoveRefused, match="not delivered"):
        core.release(ledger, actor(ledger, "main session 1"), "fix/y", settled_by="feat/x")


def test_a_release_names_a_reason_or_the_row_that_settled_it(tmp_path):
    from ledgerkit import git, repo_with_origin
    root = repo_with_origin(tmp_path)
    ledger = make_ledger(root, tmp_path / "state")
    git(root, "branch", "fix/y", "main")
    core.claim(ledger, actor(ledger, "main session 1"), "fix/y")
    with pytest.raises(MoveRefused, match="--why.*--settled-by"):
        core.release(ledger, actor(ledger, "main session 1"), "fix/y")


def seat_tree_holding(root, tmp_path, branch_name="feat/held"):
    """A seat's worktree that switched to a work branch, as a fleet member does with `tree switch`."""
    branch(root, branch_name, "work")
    tree = Path(tmp_path) / "seat-tree"
    git(root, "worktree", "add", "-q", "-b", "fleet/minor-1", str(tree))
    git(tree, "switch", "-q", branch_name)
    return tree


def dead_seat(tmp_path):
    """A seat whose session is gone, released by the orchestrator, as after a retire wave (H36)."""
    root = repo_with_origin(tmp_path)
    live = {"minor session 1"}
    ledger = make_ledger(root, tmp_path / "state", census=lambda: [_sess(name) for name in live])
    tree = seat_tree_holding(root, tmp_path)
    core.reserve(ledger, actor(ledger, "minor session 1"), "fleet/minor-1", tree=str(tree))
    live.clear()
    live.add("orchestrator 1")
    return root, ledger, tree


def _sess(name):
    from flotilla.core.census import Session
    return Session(name=name, session_id=f"id-{name}", kind="background", pid=None, short_id=None, status="busy",
                   state="working", cwd="/", started_at_ms=None)


def test_releasing_a_seat_frees_the_branch_its_clean_tree_held(tmp_path):
    root, ledger, tree = dead_seat(tmp_path)
    row = core.release(ledger, actor(ledger, "orchestrator 1"), "fleet/minor-1", why="retired")
    assert git(tree, "rev-parse", "--abbrev-ref", "HEAD") == "HEAD"   # detached
    assert row.history[-1]["evidence"]["tree"] == "detached"
    other = Path(tmp_path) / "other"
    git(root, "worktree", "add", "-q", str(other), "feat/held")   # the branch is free now


def test_a_dirty_seat_tree_keeps_its_branch_and_the_release_says_so(tmp_path):
    root, ledger, tree = dead_seat(tmp_path)
    (tree / "wip.txt").write_text("unsaved\n", encoding="utf-8")
    row = core.release(ledger, actor(ledger, "orchestrator 1"), "fleet/minor-1", why="retired")
    assert git(tree, "rev-parse", "--abbrev-ref", "HEAD") == "feat/held"
    assert "uncommitted" in row.history[-1]["evidence"]["tree"]


def test_releasing_work_leaves_its_tree_alone(world, tmp_path):
    root, ledger = world
    tree = seat_tree_holding(root, tmp_path)
    core.claim(ledger, actor(ledger, "minor session 1"), "feat/held", tree=str(tree))
    core.release(ledger, actor(ledger, "minor session 1"), "feat/held", why="will not happen")
    assert git(tree, "rev-parse", "--abbrev-ref", "HEAD") == "feat/held"


def test_releasing_a_seat_never_detaches_the_main_checkout(world):
    root, ledger = world
    core.reserve(ledger, actor(ledger, "minor session 1"), "fleet/minor-1", tree=str(root))
    row = core.release(ledger, actor(ledger, "minor session 1"), "fleet/minor-1", why="retired")
    assert git(root, "rev-parse", "--abbrev-ref", "HEAD") == "main"
    assert "not freed" in row.history[-1]["evidence"]["tree"]


def test_a_live_owner_releasing_its_own_seat_keeps_its_tree(tmp_path):
    root = repo_with_origin(tmp_path)
    ledger = make_ledger(root, tmp_path / "state", live=("minor session 1",))
    tree = seat_tree_holding(root, tmp_path)
    core.reserve(ledger, actor(ledger, "minor session 1"), "fleet/minor-1", tree=str(tree))
    row = core.release(ledger, actor(ledger, "minor session 1"), "fleet/minor-1", why="leaving")
    assert git(tree, "rev-parse", "--abbrev-ref", "HEAD") == "feat/held"
    assert "alive" in row.history[-1]["evidence"]["tree"]
