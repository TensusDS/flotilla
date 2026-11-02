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
    moved, refused = steering.adopt_from(ledger, actor(ledger, ORCH), "main session 1", to="main session 2")
    assert sorted(row.branch for row in moved) == ["feat/x", "feat/y"] and refused == []
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


def test_adopt_from_reports_what_moved_when_a_row_is_refused(world, monkeypatch):
    """Review of 0.7.17: a refusal on the third row printed only 'refused', so the orchestrator believed nothing
    moved and the heir never heard it owned two rows. Each row is answered for itself."""
    root, ledger = world
    for branch in ("feat/x", "feat/y", "feat/z"):
        drive(root, ledger, branch, to="claimed")
    real = steering.adopt

    def refusing(ledger_, actor_, branch, *, to):
        if branch == "feat/y":
            raise MoveRefused("the census could not be asked")
        return real(ledger_, actor_, branch, to=to)
    monkeypatch.setattr(steering, "adopt", refusing)
    moved, refused = steering.adopt_from(ledger, actor(ledger, ORCH), "main session 1", to="main session 2")
    assert sorted(row.branch for row in moved) == ["feat/x", "feat/z"]
    assert refused == [("feat/y", "the census could not be asked")]




def _seat_tree_holding(root, tmp_path, name="feat/held"):
    from pathlib import Path as P
    from ledgerkit import branch, git
    branch(root, name, "work")
    tree = P(tmp_path) / "seat-tree"
    git(root, "worktree", "add", "-q", "-b", "fleet/main-1", str(tree))
    git(tree, "switch", "-q", name)
    return tree


def test_adopt_frees_the_branch_a_gone_owners_clean_tree_held(world, tmp_path):
    """twosuns field report A7: after adopt, the heir's `tree switch` failed - the branch was still checked out in
    the gone seat's tree, and the orchestrator detached three trees by hand."""
    from flotilla.ledger import core
    from ledgerkit import git
    root, ledger = world
    tree = _seat_tree_holding(root, tmp_path)
    core.claim(ledger, actor(ledger, "main session 1"), "feat/held", tree=str(tree))
    row = steering.adopt(ledger, actor(ledger, ORCH), "feat/held", to="main session 2")
    assert git(tree, "rev-parse", "--abbrev-ref", "HEAD") == "HEAD"
    assert row.history[-1]["evidence"]["tree"] == "detached"


def test_adopt_keeps_a_dirty_tree_on_its_branch_and_says_so(world, tmp_path):
    from flotilla.ledger import core
    from ledgerkit import git
    root, ledger = world
    tree = _seat_tree_holding(root, tmp_path)
    core.claim(ledger, actor(ledger, "main session 1"), "feat/held", tree=str(tree))
    (tree / "wip.txt").write_text("unsaved\n", encoding="utf-8")
    row = steering.adopt(ledger, actor(ledger, ORCH), "feat/held", to="main session 2")
    assert git(tree, "rev-parse", "--abbrev-ref", "HEAD") == "feat/held"
    assert "uncommitted" in row.history[-1]["evidence"]["tree"]



def test_adopt_leaves_a_tree_that_no_longer_holds_the_branch(world, tmp_path):
    """Final review of 0.21.0, M2: the gone owner's tree may be on another branch by now, perhaps a later seat's."""
    from flotilla.ledger import core
    from ledgerkit import branch, git
    root, ledger = world
    tree = _seat_tree_holding(root, tmp_path)
    core.claim(ledger, actor(ledger, "main session 1"), "feat/held", tree=str(tree))
    branch(root, "feat/other", "other")
    git(tree, "switch", "-q", "feat/other")
    row = steering.adopt(ledger, actor(ledger, ORCH), "feat/held", to="main session 2")
    assert git(tree, "rev-parse", "--abbrev-ref", "HEAD") == "feat/other"
    assert "not freed" in row.history[-1]["evidence"]["tree"]


def _requires_world(root, ledger, *, built_on: bool):
    """feat/a claimed; feat/b claimed requiring it, cut from feat/a's tip when `built_on`, else from trunk."""
    from ledgerkit import git
    drive(root, ledger, "feat/a", to="claimed")
    if built_on:
        git(root, "checkout", "-q", "feat/a")
    row = drive(root, ledger, "feat/b", to="claimed", requires=["feat/a"])
    git(root, "checkout", "-q", "main")
    return row


def test_a_stale_requirement_is_dropped_on_the_record(world):
    """Twosuns, 2026-11-01, item 11: r955 required a row its tip never carried, nothing could lift it, and the way out
    was a re-cut - a new review and new lane time. The drop is a recorded move instead."""
    root, ledger = world
    before = _requires_world(root, ledger, built_on=False)
    row = steering.requires(ledger, actor(ledger, ORCH), "feat/b", drop=["feat/a"], why="an early plan's link")
    assert row.requires == [] and row.state == before.state == "claimed"
    entry = row.history[-1]
    assert entry["move"] == "requires" and entry["evidence"]["why"] == "an early plan's link"
    assert entry["evidence"]["dropped"] == ["feat/a"] and entry["evidence"]["added"] == []


def test_a_drop_the_tip_still_needs_is_refused(world):
    root, ledger = world
    _requires_world(root, ledger, built_on=True)
    with pytest.raises(MoveRefused, match=r"carries 1 commit\(s\) of `feat/a` that are not on trunk"):
        steering.requires(ledger, actor(ledger, ORCH), "feat/b", drop=["feat/a"], why="looks stale")


def test_a_drop_of_a_row_whose_work_is_on_trunk_is_allowed(world):
    from ledgerkit import git, merge
    root, ledger = world
    _requires_world(root, ledger, built_on=True)
    merge(root, "feat/a")
    git(root, "push", "-q", "origin", "main")
    row = steering.requires(ledger, actor(ledger, ORCH), "feat/b", drop=["feat/a"], why="feat/a is on trunk")
    assert row.requires == []


def test_an_unknown_row_is_refused_by_name(world):
    root, ledger = world
    _requires_world(root, ledger, built_on=False)
    with pytest.raises(MoveRefused, match="`feat/nope`"):
        steering.requires(ledger, actor(ledger, ORCH), "feat/b", drop=["feat/nope"], why="typo")
    with pytest.raises(MoveRefused, match="`feat/nope`"):
        steering.requires(ledger, actor(ledger, ORCH), "feat/b", add=["feat/nope"], why="typo")


def test_a_drop_not_required_is_refused(world):
    root, ledger = world
    _requires_world(root, ledger, built_on=False)
    drive(root, ledger, "feat/c", to="claimed")
    with pytest.raises(MoveRefused, match="`feat/b` does not require `feat/c`"):
        steering.requires(ledger, actor(ledger, ORCH), "feat/b", drop=["feat/c"], why="never was")
    with pytest.raises(MoveRefused, match="--why"):
        steering.requires(ledger, actor(ledger, ORCH), "feat/b", drop=["feat/a"], why=" ")
    with pytest.raises(MoveRefused, match="--add or --drop"):
        steering.requires(ledger, actor(ledger, ORCH), "feat/b", why="nothing named")


def test_add_restores_a_lost_requirement(world):
    from flotilla.ledger import delivery
    root, ledger = world
    first = drive(root, ledger, "feat/a", to="claimed")
    drive(root, ledger, "feat/c", to="accepted")
    row = steering.requires(ledger, actor(ledger, ORCH), "feat/c", add=["feat/a", "feat/a"], why="lost at claim")
    assert row.requires == [first.id] and row.state == "accepted"
    with pytest.raises(MoveRefused, match="requires `feat/a`"):
        delivery.queue(ledger, actor(ledger, "sender 1"), "feat/c", pr=7)


def test_only_the_owner_or_a_post_that_may(world):
    root, ledger = world
    _requires_world(root, ledger, built_on=False)
    with pytest.raises(MoveRefused, match="may not `requires`"):
        steering.requires(ledger, actor(ledger, "review session 1"), "feat/b", drop=["feat/a"], why="not mine")
    row = steering.requires(ledger, actor(ledger, "main session 1"), "feat/b", drop=["feat/a"], why="my own row")
    assert row.requires == []
