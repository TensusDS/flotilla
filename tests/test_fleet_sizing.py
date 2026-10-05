import pytest

from flotilla.fleet.backlog import Backlog, Source
from flotilla.fleet.pace import Pace
from flotilla.fleet.sizing import Machine, recommend

PACE = Pace(1.0, 0.33, False, "authoring: default; review: default")
ROOMY = dict(free_mb=64_000, floor_mb=2000, seat_mb=800, live_seats=0, run_mb=1000, run_seconds=60, ceiling=200,
             lane_capacity=4, free_disk_mb=500_000, tree_mb=100)


def machine(**over):
    return Machine(**{**ROOMY, **over})


def work(main=20, minor=0):
    return Backlog([Source("named tasks", main, minor, "")])


@pytest.mark.parametrize("over, backlog, profile, binding, authors", [
    ({}, work(3), {}, "backlog", 3),
    # 9000 free - 2000 floor - 1000 run = 6000 -> 7 seats of 800; sender + reviewers(a) + a <= 7 -> a = 3 (r = 1? no:
    # ceil(3 x 1 x 0.33) = 1 -> 5 seats; a = 4 -> r = 2 -> 7 seats) -> 4
    ({"free_mb": 9000}, work(), {}, "memory", 4),
    # one run of 600 s at a time verifies 6 handovers an hour; authors at 1/h each -> 6
    ({"run_seconds": 600, "lane_capacity": 1}, work(), {}, "test runs", 6),
    # 0.5 x 2000 MB disk / 100 MB a tree = 10 seats; a + reviewers + sender <= 10 -> a = 7 (r = 3? ceil(7 x .33) = 3 ->
    # 11) -> a = 6 (r = 2 -> 9)
    ({"free_disk_mb": 2000}, work(), {}, "disk", 6),
    ({}, work(), {"fleet": {"sizing": {"max_seats": 5}}}, "max seats", 3),
])
def test_each_cap_binds_in_turn(over, backlog, profile, binding, authors):
    rec = recommend(machine(**over), backlog, PACE, profile)
    assert (rec.binding, rec.authors) == (binding, authors)
    assert f"limited by {binding}" in rec.lines[0]


def test_an_unknown_signal_is_left_out_and_named():
    rec = recommend(machine(free_disk_mb=None, run_seconds=None), work(5), PACE, {})
    assert "disk" not in rec.caps and "test runs" not in rec.caps
    text = "\n".join(rec.lines)
    assert "disk: unknown" in text and "test runs: unknown" in text


def test_an_unknown_backlog_source_is_named_and_the_rest_stands():
    b = Backlog([Source("GitHub issues", None, None, "gh is not installed"), Source("TODO files", 2, 0, "TODO.md")])
    rec = recommend(machine(), b, PACE, {})
    assert rec.caps["backlog"] == 2 and "GitHub issues: unknown (gh is not installed)" in "\n".join(rec.lines)


def test_a_fresh_project_stands_on_named_defaults():
    m = Machine(free_mb=None, floor_mb=2000, seat_mb=800, live_seats=0, run_mb=None, run_seconds=None,
                lane_capacity=1, free_disk_mb=None, tree_mb=None)
    rec = recommend(m, Backlog([Source("ledger", 0, 0, "")]), PACE, {})
    assert rec.authors == 1 and rec.counts["reviewer"] == 1 and rec.counts["main"] == 1
    text = "\n".join(rec.lines)
    for named in ("memory: unknown", "test runs: unknown", "disk: unknown", "pace", "default",
                  "code: not measured in this version", "money: not measured in this version"):
        assert named in text, named
    assert rec.raise_nothing == ""


def test_no_room_for_a_seat_says_raise_nothing():
    rec = recommend(machine(free_mb=2500), work(), PACE, {})
    assert "no room for one more seat" in rec.raise_nothing
    assert "2500" in rec.raise_nothing and "2000" in rec.raise_nothing and "800" in rec.raise_nothing


def test_the_smallest_fleet_not_fitting_says_raise_nothing():
    """Room for one seat but not for the smallest fleet (author, reviewer, sender) and one test run."""
    rec = recommend(machine(free_mb=4500), work(), PACE, {})
    assert rec.raise_nothing and "smallest" in rec.raise_nothing


def test_reviewers_grow_with_authors_and_pace():
    slow_review = Pace(1.5, 1.0, True, "measured")
    rec = recommend(machine(), work(4), slow_review, {})
    assert rec.authors == 4 and rec.counts["reviewer"] == 4     # ceil(4 x 1.5 x 1.0) = 6, never more than authors
    rec = recommend(machine(), work(1), PACE, {})
    assert rec.counts["reviewer"] == 1


def test_main_and_minor_follow_the_backlog_mix():
    rec = recommend(machine(), work(2, 2), PACE, {})
    assert (rec.counts["main"], rec.counts["minor"]) == (2, 2)
    rec = recommend(machine(), work(1, 9), PACE, {})
    assert rec.counts["main"] >= 1 and rec.counts["main"] + rec.counts["minor"] == rec.authors
    rec = recommend(machine(), work(0, 3), PACE, {})
    assert (rec.counts["main"], rec.counts["minor"]) == (0, 3)


def test_judge_when_required_or_deployed():
    assert recommend(machine(), work(2), PACE, {}).counts["judge"] == 0
    assert recommend(machine(), work(2), PACE, {"judge": {"required": True}}).counts["judge"] == 1
    assert recommend(machine(), work(2), PACE, {"deploy": {"command": "x"}}).counts["judge"] == 1


def test_the_counts_name_every_post():
    rec = recommend(machine(), work(2), PACE, {})
    assert set(rec.counts) == {"orchestrator", "sender", "reviewer", "judge", "main", "minor"}
    assert rec.counts["orchestrator"] == 1 and rec.counts["sender"] == 1


def test_max_seats_is_never_exceeded():
    for limit in range(3, 12):
        rec = recommend(machine(), work(), PACE, {"fleet": {"sizing": {"max_seats": limit}}})
        raised = sum(n for post, n in rec.counts.items() if post != "orchestrator")
        assert raised <= limit, (limit, rec.counts)


def test_the_runs_cap_divides_by_each_authors_pace():
    fast = Pace(2.0, 0.33, True, "measured")
    rec = recommend(machine(run_seconds=600, lane_capacity=1), work(), fast, {})
    assert (rec.binding, rec.authors) == ("test runs", 3)      # 6 verified an hour / 2 each


def test_memory_left_after_the_seats_limits_the_runs_that_fit():
    """Four lane slots, but after the seats only one 4 GB run fits: one run an hour verifies one author."""
    rec = recommend(machine(free_mb=12_000, run_mb=4000, run_seconds=3600, lane_capacity=4), work(), PACE, {})
    assert (rec.binding, rec.authors) == ("test runs", 1)


def test_max_seats_below_the_smallest_fleet_says_raise_nothing():
    rec = recommend(machine(), work(), PACE, {"fleet": {"sizing": {"max_seats": 2}}})
    assert "max_seats" in rec.raise_nothing


def test_one_main_item_keeps_a_main_author():
    rec = recommend(machine(), work(1, 30), PACE, {"fleet": {"sizing": {"max_seats": 4}}})
    assert rec.authors == 2 and (rec.counts["main"], rec.counts["minor"]) == (1, 1)


def test_a_wholly_unknown_backlog_is_no_cap():
    b = Backlog([Source("GitHub issues", None, None, "gh is not installed")])
    rec = recommend(machine(), b, PACE, {})
    assert "backlog" not in rec.caps and rec.binding != "backlog"


def test_a_seat_tree_is_measured_where_one_exists(tmp_path, monkeypatch):
    """A seat tree shares the objects; the main checkout carries them all. An existing seat tree is what a new one
    will cost, so it is measured first; the main checkout's tracked files only stand in when there is none."""
    from types import SimpleNamespace

    from flotilla.core import resources
    from flotilla.fleet import sizing
    main, seat = tmp_path / "app", tmp_path / "app-main-1"
    main.mkdir()
    seat.mkdir()
    asked = []
    monkeypatch.setattr(resources, "tree_mb", lambda path, **kw: asked.append(path) or 42)
    rows = {"r1": SimpleNamespace(tree=str(main)), "r2": SimpleNamespace(tree=str(seat))}
    ledger = SimpleNamespace(rows=lambda: rows, run=None)
    size, note = sizing._tree_size(ledger, main)
    assert size == 42 and asked == [seat] and str(seat) in note


def test_a_ledger_tree_outside_the_seat_trees_is_never_measured(tmp_path, monkeypatch):
    """Review of 0.7.14 (security): `row.tree` is written by sessions; `--tree /` cost a 20 s du per row."""
    from types import SimpleNamespace

    from flotilla.core import resources
    from flotilla.fleet import sizing
    main = tmp_path / "app"
    main.mkdir()
    asked = []
    monkeypatch.setattr(resources, "tree_mb", lambda path, **kw: asked.append(path) or 42)
    rows = {f"r{i}": SimpleNamespace(tree=t) for i, t in enumerate(["/", "/usr", str(tmp_path / "elsewhere")])}
    (tmp_path / "elsewhere").mkdir()
    sizing._tree_size(SimpleNamespace(rows=lambda: rows, run=lambda *a, **k: SimpleNamespace(returncode=1)), main)
    assert asked == []


def test_a_seat_tree_du_could_not_measure_is_named(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from flotilla.core import resources
    from flotilla.fleet import sizing
    main, seat = tmp_path / "app", tmp_path / "app-main-1"
    main.mkdir()
    seat.mkdir()
    monkeypatch.setattr(resources, "tree_mb", lambda path, **kw: None)
    rows = {"r1": SimpleNamespace(tree=str(seat))}
    size, note = sizing._tree_size(SimpleNamespace(rows=lambda: rows, run=lambda *a, **k: SimpleNamespace(
        returncode=0, stdout="")), main)
    assert "could not be measured" in note and "no seat tree yet" not in note


def test_live_seats_count_back_into_the_room_they_hold():
    """Review of 0.7.14, I1: MemAvailable already excludes the running seats, and `spawn --recommended` subtracts the
    posts they hold - so the recommendation, a total, must count them back or the fleet shrinks as it runs."""
    # 6000 free + 3 live x 800 - 2000 floor = 6400 room, 1000 a run -> 6 seats of 800 -> a = 4 (4 + 1? r = ceil(1.32)
    # = 2 -> 7 seats) no -> a = 3 (r = 1 -> 5 seats); with no seats counted back: 3000 -> 3 seats -> a = 1
    rec = recommend(machine(free_mb=6000, live_seats=3), work(), PACE, {})
    assert rec.authors == 3
    rec = recommend(machine(free_mb=6000, live_seats=0), work(), PACE, {})
    assert rec.authors == 1


def test_reviewers_never_outnumber_authors():
    """Review of 0.7.14 (security): three rows handed a minute after their claim and reviewed an hour later made one
    task call for 60 reviewers."""
    gamed = Pace(60.0, 1.0, True, "measured")
    rec = recommend(machine(), work(1), gamed, {})
    assert rec.counts["reviewer"] == 1
    rec = recommend(machine(), work(3), Pace(1.5, 1.0, True, "measured"), {})
    assert rec.counts["reviewer"] == 3


def test_a_ceiling_holds_when_every_machine_signal_is_unknown():
    blind = Machine(free_mb=None, floor_mb=2000, seat_mb=800, live_seats=0, run_mb=None, run_seconds=None,
                    lane_capacity=1, free_disk_mb=None, tree_mb=None)
    rec = recommend(blind, work(5000), PACE, {})
    raised = sum(n for post, n in rec.counts.items() if post != "orchestrator")
    assert raised <= 12 and rec.binding == "max seats"


def test_only_the_persons_machine_file_raises_the_ceiling():
    rec = recommend(machine(ceiling=30), work(100), PACE, {"fleet": {"sizing": {"max_seats": 99}}})
    assert sum(n for post, n in rec.counts.items() if post != "orchestrator") <= 30 and rec.binding == "max seats"
    rec = recommend(machine(ceiling=30), work(), PACE, {"fleet": {"sizing": {"max_seats": 5}}})
    assert sum(n for post, n in rec.counts.items() if post != "orchestrator") <= 5


def test_a_max_seats_that_is_not_a_number_is_named():
    rec = recommend(machine(), work(3), PACE, {"fleet": {"sizing": {"max_seats": "4"}}})
    assert "max_seats" in "\n".join(rec.lines) and "ignored" in "\n".join(rec.lines)


def test_raise_nothing_leads_the_rendering():
    from flotilla.fleet.sizing import render
    rec = recommend(machine(free_mb=2500), work(0), PACE, {})
    out = render(rec)
    assert out[0].startswith("raise nothing now") and not out[1].startswith("recommended:")
    assert rec.binding == "memory"   # a tie at 0 with an empty backlog names the cap that refuses


def test_unknown_measurements_file_is_named(tmp_path):
    from flotilla.fleet.sizing import handover_cost
    profile = {"tests": {"tier": [{"name": "unit", "required_for": ["handover"]}]}}
    _, _, note = handover_cost(profile, {}, {}, problem="the measurements file x could not be read")
    assert "could not be read" in note and "never run green" not in note
