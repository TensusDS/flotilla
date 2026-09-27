import pytest

from flotilla.ledger import runs
from flotilla.ledger.errors import MoveRefused
from ledgerkit import actor, drive, make_ledger, repo_with_origin


@pytest.fixture()
def world(tmp_path):
    root = repo_with_origin(tmp_path)
    return root, make_ledger(root, tmp_path / "state")


def test_a_run_is_recorded_on_the_open_row(world):
    root, ledger = world
    row = drive(root, ledger, to="handed")
    recorded = runs.record_run(ledger, actor(ledger, "review session 1"), "feat/x", verdict="green",
                               summary="3 passed in 0.10s", revision=row.tip, evidence={"exit": 0})
    assert recorded.state == "handed"
    assert recorded.last_run.startswith(f"green: 3 passed in 0.10s over {row.tip[:7]} at ")
    assert recorded.history[-1]["evidence"] == {"exit": 0}


def test_any_identified_session_may_record_a_run(world):
    root, ledger = world
    drive(root, ledger, to="claimed")
    recorded = runs.record_run(ledger, actor(ledger, "acceptance judge 1"), "feat/x", verdict="killed",
                               summary="killed by signal 9 - no verdict", revision="", evidence={})
    assert recorded.last_run.startswith("killed: killed by signal 9 - no verdict over unknown")


def test_a_run_needs_an_open_row(world):
    root, ledger = world
    with pytest.raises(MoveRefused, match="no open ledger row"):
        runs.record_run(ledger, actor(ledger, "main session 1"), "feat/none", verdict="green", summary="1 passed",
                        revision="", evidence={})
