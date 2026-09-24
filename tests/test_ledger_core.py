import subprocess
import sys
import time
from pathlib import Path

import pytest

from flotilla.core.census import CensusUnavailable
from flotilla.ledger import core
from flotilla.ledger.errors import MoveRefused
from ledgerkit import actor, branch, git, make_ledger, repo_with_origin

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
