import datetime as dt

from flotilla.ledger import views
from flotilla.ledger.model import Row

PR = {"flow": {"mode": "pr"}, "review": {"depth": "every"}}
JUDGED = {**PR, "judge": {"required": True}}
NOW = dt.datetime(2026, 9, 26, 12, 0, tzinfo=dt.timezone.utc)


def row(id="r1", **fields):
    return Row(id=id, **{"branch": "feat/x", "owner": "main session 1", "state": "claimed", **fields})


def rows(*items):
    return {item.id: item for item in items}


def test_whose_move_follows_the_state_not_the_owner():
    assert views.who_moves(row(), PR) == "main session 1"
    assert views.who_moves(row(state="handed", reader="review session 1"), PR) == "review session 1"
    assert views.who_moves(row(state="handed"), PR) == ""
    assert views.who_moves(row(state="accepted"), PR) == views.SENDER
    assert views.who_moves(row(state="shipped"), PR) == "main session 1"
    assert views.who_moves(row(state="shipped"), JUDGED) == views.JUDGE
    assert views.who_moves(row(state="closed"), PR) == ""


def test_the_roster_computes_each_sessions_state():
    table = rows(
        row("r1", branch="a", owner="main session 1", state="claimed"),
        row("r2", branch="b", owner="main session 2", state="handed", reader="review session 1", taken=True),
        row("r3", branch="c", owner="main session 3", state="claimed", requires=["r1"]),
        row("r4", branch="d", owner="minor session 1", state="accepted"),
        row("r5", branch="e", owner="sender 1", state="reserved"),
        row("r6", branch="f", owner="minor session 9", state="claimed"),
    )
    live = {"main session 1", "main session 2", "main session 3", "review session 1", "minor session 1", "sender 1"}
    found = {entry["who"]: entry["state"] for entry in views.roster(table, PR, live)}
    assert found == {"main session 1": "working", "main session 2": "waiting", "main session 3": "blocked",
                     "review session 1": "reading", "minor session 1": "waiting", "sender 1": "idle",
                     "minor session 9": "orphaned"}


def test_without_the_census_nobody_is_called_orphaned():
    found = views.roster(rows(row(owner="gone session 1")), PR, None)
    assert found[0]["state"] == "working"


def test_stalled_is_measured_from_the_last_move():
    table = rows(row("r1", branch="old", updated_at="2026-09-26T06:00:00+00:00"),
                 row("r2", branch="new", updated_at="2026-09-26T11:30:00+00:00"),
                 row("r3", branch="post", state="reserved", updated_at="2026-09-20T00:00:00+00:00"),
                 row("r4", branch="done", state="closed", updated_at="2026-09-20T00:00:00+00:00"))
    assert [item.branch for item in views.stalled(table, 4, NOW)] == ["old"]


def test_a_hold_is_asked_of_the_world():
    waiting_on_branch = row("r1", state="handed", held_by="orchestrator 1", held_until="feat/y")
    assert views.hold_lifted(waiting_on_branch, rows(waiting_on_branch, row("r2", branch="feat/y", state="handed"))) is False
    assert views.hold_lifted(waiting_on_branch, rows(waiting_on_branch, row("r2", branch="feat/y", state="accepted"))) is True
    on_reader = row("r1", state="handed", held_by="orchestrator 1", held_until="review session 1")
    busy = row("r2", branch="feat/y", state="handed", reader="review session 1")
    assert views.hold_lifted(on_reader, rows(on_reader, busy), {"review session 1"}) is False
    assert views.hold_lifted(on_reader, rows(on_reader), {"review session 1"}) is True
    assert views.hold_lifted(on_reader, rows(on_reader), {"someone else"}) is None
    assert views.hold_lifted(row(), rows(row())) is None


def test_deviations_are_moves_that_cannot_happen_now():
    table = rows(
        row("r1", branch="nobody", state="handed"),
        row("r2", branch="held", state="handed", held_by="orchestrator 1", held_until="nobody-left", reader=""),
        row("r3", branch="gone", state="handed", reader="review session 7"),
        row("r4", branch="broken", state="shipped", broken="Settings"),
        row("r5", branch="done", state="claimed"),
    )
    found = {(item["branch"], item["kind"]) for item in views.deviations(
        table, PR, live={"main session 1"}, finished=lambda item: item.branch == "done")}
    assert found == {("nobody", "nobody_named"), ("held", "hold_unknown"), ("gone", "mover_gone"),
                     ("broken", "broken_unfixed"), ("done", "finished_not_handed")}


def test_without_the_census_no_mover_is_called_gone():
    table = rows(row("r1", state="handed", reader="review session 7"))
    assert views.deviations(table, PR, live=None) == []
