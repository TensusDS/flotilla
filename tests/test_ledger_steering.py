import pytest

from flotilla.ledger import steering
from flotilla.ledger.errors import MoveRefused
from ledgerkit import actor, drive, make_ledger, repo_with_origin

ORCH = "orchestrator 1"
LIVE = ("main session 2", ORCH, "review session 1", "sender 1")


@pytest.fixture()
def world(tmp_path):
    root = repo_with_origin(tmp_path)
    return root, make_ledger(root, tmp_path / "state", live=LIVE)


def test_adopt_hands_orphaned_work_to_a_live_session(world):
    root, ledger = world
    drive(root, ledger, to="claimed")
    row = steering.adopt(ledger, actor(ledger, ORCH), "feat/x", to="main session 2")
    assert (row.owner, row.adopted_from, row.state) == ("main session 2", "main session 1", "claimed")


def test_adopt_never_takes_a_live_owners_work(tmp_path):
    root = repo_with_origin(tmp_path)
    ledger = make_ledger(root, tmp_path / "state")
    drive(root, ledger, to="claimed")
    with pytest.raises(MoveRefused, match="main session 1 is alive"):
        steering.adopt(ledger, actor(ledger, ORCH), "feat/x", to="minor session 1")


def test_adopt_needs_a_live_heir_whose_post_owns_work(world):
    root, ledger = world
    drive(root, ledger, to="claimed")
    with pytest.raises(MoveRefused, match="not alive"):
        steering.adopt(ledger, actor(ledger, ORCH), "feat/x", to="main session 3")
    with pytest.raises(MoveRefused, match="holds no post that owns"):
        steering.adopt(ledger, actor(ledger, ORCH), "feat/x", to="review session 1")


def test_a_hold_names_what_lifts_it_and_why(world):
    root, ledger = world
    drive(root, ledger, to="handed")
    with pytest.raises(MoveRefused, match="--until"):
        steering.hold(ledger, actor(ledger, ORCH), "feat/x", until="", why="the reader is busy")
    with pytest.raises(MoveRefused, match="--why"):
        steering.hold(ledger, actor(ledger, ORCH), "feat/x", until="review session 1", why="")


def test_a_hold_on_a_name_nobody_knows_is_refused(world):
    root, ledger = world
    drive(root, ledger, to="handed")
    with pytest.raises(MoveRefused, match="check the spelling"):
        steering.hold(ledger, actor(ledger, ORCH), "feat/x", until="reveiw session 1", why="busy")


def test_a_hold_is_recorded_and_lifted(world):
    root, ledger = world
    drive(root, ledger, to="handed")
    drive(root, ledger, "feat/y", to="claimed")
    held = steering.hold(ledger, actor(ledger, ORCH), "feat/x", until="feat/y", why="read it after feat/y")
    assert (held.held_by, held.held_until, held.held_why, held.state) == (ORCH, "feat/y", "read it after feat/y",
                                                                           "handed")
    lifted = steering.unhold(ledger, actor(ledger, ORCH), "feat/x")
    assert (lifted.held_by, lifted.held_until) == ("", "")
    with pytest.raises(MoveRefused, match="not held"):
        steering.unhold(ledger, actor(ledger, ORCH), "feat/x")


def test_only_the_reading_queue_and_what_waits_to_ship_are_held(world):
    root, ledger = world
    drive(root, ledger, to="claimed")
    with pytest.raises(MoveRefused, match="reading queue"):
        steering.hold(ledger, actor(ledger, ORCH), "feat/x", until="review session 1", why="busy")


def test_urgent_marks_a_row_and_can_be_cancelled(world):
    root, ledger = world
    drive(root, ledger)
    row = steering.urgent(ledger, actor(ledger, ORCH), "feat/x", why="a client is waiting")
    assert row.urgent_at and row.urgent_why == "a client is waiting" and row.state == "accepted"
    assert steering.urgent(ledger, actor(ledger, ORCH), "feat/x", cancel=True).urgent_at == ""


def test_the_census_switch_makes_liveness_unknown(world, monkeypatch):
    root, ledger = world
    ledger.census = None
    monkeypatch.setenv("FLOTILLA_NO_CENSUS", "1")
    with pytest.raises(MoveRefused, match="FLOTILLA_NO_CENSUS"):
        ledger.live_names()


def test_an_accepted_row_can_be_held_out_of_the_batch(world):
    """Twosuns orchestrator, 0.7.16: clouds, thunder and the sound's air were accepted and their release deliberately
    put off, but `hold` refused accepted rows, so every brief offered them and the person's yes had to list rows by
    name. A held accepted row stays out of the batch and its sender moves refuse it until it is unheld."""
    from flotilla.ledger import delivery, report
    root, ledger = world
    drive(root, ledger, to="accepted")
    steering.hold(ledger, actor(ledger, ORCH), "feat/x", until="the person", why="its release waits for the sound pass")
    text = "\n".join(report.brief(ledger))
    assert "nothing is ready to ship" in text and "held by orchestrator 1 until the person" in text
    with pytest.raises(MoveRefused, match="held"):
        delivery.queue(ledger, actor(ledger, "sender 1"), "feat/x", pr=7)
    steering.unhold(ledger, actor(ledger, ORCH), "feat/x")
    assert "1. `feat/x`" in "\n".join(report.brief(ledger))


def test_adopt_from_hands_every_row_of_a_gone_session_at_once(world):
    """Twosuns, 0.7.16: five sessions stopped in one night, and their rows could be handed over one command each."""
    root, ledger = world
    drive(root, ledger, to="claimed")
    drive(root, ledger, "feat/y", to="claimed")
    moved = steering.adopt_from(ledger, actor(ledger, ORCH), "main session 1", to="main session 2")
    assert sorted(row.branch for row in moved) == ["feat/x", "feat/y"]
    assert all(row.owner == "main session 2" for row in moved)
    with pytest.raises(MoveRefused, match="no open work"):
        steering.adopt_from(ledger, actor(ledger, ORCH), "main session 1", to="main session 2")


def test_a_hold_for_the_person_raises_no_deviation_of_its_own(world):
    """Held until the person: neither lifted nor unknown - or the watch would wake the orchestrator about it forever."""
    from flotilla.ledger import views
    root, ledger = world
    drive(root, ledger, to="accepted")
    steering.hold(ledger, actor(ledger, ORCH), "feat/x", until="the person", why="release put off")
    kinds = {found["kind"] for found in views.deviations(ledger.rows(), ledger.profile, live=set(LIVE))}
    assert not kinds & {"hold_lifted", "hold_unknown"}
