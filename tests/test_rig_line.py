import datetime as dt

import pytest

from flotilla.core.storage import LocalLogStore
from flotilla.rig import journal as j
from flotilla.rig import line

T0 = dt.datetime(2026, 11, 3, 14, 0, tzinfo=dt.timezone.utc)
ALIVE = lambda pid, mark: pid != 404
MIN = dt.timedelta(minutes=1)


@pytest.fixture(autouse=True)
def utc(monkeypatch, request):
    import time
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    request.addfinalizer(time.tzset)


def world(tmp_path, hours=1.0, machines=1):
    clock = {"at": T0 - dt.timedelta(hours=1)}
    r = j.Rig(LocalLogStore(tmp_path / "rig"), clock=lambda: clock["at"])
    s = r.open_session("the person", "frames", hours=hours + 1, budget=5.0)
    made = []
    for _ in range(machines):
        m = r.add_machine(s.id, "vast", lambda mid: f"flotilla:k:{mid}")
        r.move(m.id, j.PROVISIONING, instance=f"9{m.id}", hourly=0.2, created=T0.isoformat())
        r.move(m.id, j.READY, address="ssh4.vast.ai:30001")
        made.append(m)
    return r, s, made, clock


def measured(r, s, m, ladder, minutes, clock):
    run = r.queue_run(s.id, who="judge 2", project="P", revision="a" * 40, program="node", ladder=ladder, pid=7,
                      mark="m")
    assert r.start_run(run.id, m.id, alive=ALIVE, roomy=True)
    r.finish_run(run.id, "green", exit=0, seconds=minutes * 60.0)


def queued(r, s, ladder, pid, ceiling=1800, exclusive=False):
    return r.queue_run(s.id, who="main 3", project="P", revision="a" * 40, program="node", ladder=ladder, pid=pid,
                       mark="m", ceiling=ceiling, exclusive=exclusive)


def running(r, m, run, clock, ago):
    assert r.start_run(run.id, m.id, alive=ALIVE, roomy=True)
    clock["at"] = T0 - ago
    r.command_started(run.id)
    clock["at"] = T0


def test_one_machine_serves_the_line_in_order(tmp_path):
    r, s, (m,), clock = world(tmp_path)
    for ladder, minutes in (("exact:a",), 10), (("exact:b",), 20), (("exact:c",), 5):
        measured(r, s, m, ladder, minutes, clock)
    first = queued(r, s, ("exact:a",), pid=1)
    running(r, m, first, clock, 4 * MIN)
    queued(r, s, ("exact:b",), pid=2)
    queued(r, s, ("exact:c",), pid=3)
    found = line.places(r, s.id, T0, ALIVE)
    assert [(p.run.id, p.number, p.starts, p.ends) for p in found] == [
        ("j5", 1, T0 + 6 * MIN, T0 + 26 * MIN), ("j6", 2, T0 + 26 * MIN, T0 + 31 * MIN)]
    assert all(p.unmeasured == 0 and p.fits is True for p in found)


def test_an_unmeasured_run_counts_at_its_ceiling_and_is_counted(tmp_path):
    r, s, (m,), clock = world(tmp_path)
    first = queued(r, s, ("exact:new",), pid=1, ceiling=900)
    running(r, m, first, clock, 5 * MIN)
    queued(r, s, ("exact:new2",), pid=2, ceiling=600)
    (place,) = line.places(r, s.id, T0, ALIVE)
    assert place.starts == T0 + 10 * MIN and place.ends == T0 + 20 * MIN and place.unmeasured == 2


def test_a_run_still_in_setup_counts_its_whole_estimate(tmp_path):
    r, s, (m,), clock = world(tmp_path)
    measured(r, s, m, ("exact:a",), 10, clock)
    first = queued(r, s, ("exact:a",), pid=1)
    assert r.start_run(first.id, m.id, alive=ALIVE, roomy=True)
    queued(r, s, ("exact:a",), pid=2)
    (place,) = line.places(r, s.id, T0, ALIVE)
    assert place.starts == T0 + 10 * MIN


def test_a_run_past_its_estimate_is_given_a_minute(tmp_path):
    r, s, (m,), clock = world(tmp_path)
    measured(r, s, m, ("exact:a",), 10, clock)
    first = queued(r, s, ("exact:a",), pid=1)
    running(r, m, first, clock, 30 * MIN)
    queued(r, s, ("exact:a",), pid=2)
    assert line.places(r, s.id, T0, ALIVE)[0].starts == T0 + MIN


def test_no_machine_up_gives_no_number(tmp_path):
    r, s, _, _ = world(tmp_path, machines=0)
    queued(r, s, ("exact:a",), pid=1)
    (place,) = line.places(r, s.id, T0, ALIVE)
    assert (place.starts, place.ends, place.fits) == (None, None, None)
    assert "a machine is raised first" in line.says(place, s)


def test_a_run_past_the_session_end_does_not_fit(tmp_path):
    r, s, (m,), clock = world(tmp_path, hours=0.25)
    measured(r, s, m, ("exact:a",), 10, clock)
    measured(r, s, m, ("exact:b",), 20, clock)
    first = queued(r, s, ("exact:a",), pid=1)
    running(r, m, first, clock, 4 * MIN)
    queued(r, s, ("exact:b",), pid=2)
    (place,) = line.places(r, s.id, T0, ALIVE)
    said = line.says(place, s)
    assert place.fits is False and f"after session {s.id} ends at 14:15" in said and "--max" in said


def test_two_machines_serve_the_line_side_by_side(tmp_path):
    r, s, (m1, m2), clock = world(tmp_path, machines=2)
    measured(r, s, m1, ("exact:a",), 10, clock)
    first = queued(r, s, ("exact:a",), pid=1)
    running(r, m1, first, clock, 0 * MIN)
    queued(r, s, ("exact:a",), pid=2, exclusive=True)
    queued(r, s, ("exact:a",), pid=3)
    found = line.places(r, s.id, T0, ALIVE)
    assert [(p.run.id, p.starts) for p in found] == [("j3", T0), ("j4", T0 + 10 * MIN)]


def test_dead_runs_are_not_in_the_line(tmp_path):
    r, s, _, _ = world(tmp_path)
    queued(r, s, ("exact:a",), pid=404)
    queued(r, s, ("exact:a",), pid=2)
    assert [(p.run.id, p.number) for p in line.places(r, s.id, T0, ALIVE)] == [("j2", 1)]


def test_says_names_place_runs_ahead_start_and_session_end(tmp_path):
    r, s, (m,), clock = world(tmp_path)
    for ladder, minutes in (("exact:a",), 10), (("exact:b",), 20):
        measured(r, s, m, ladder, minutes, clock)
    first = queued(r, s, ("exact:a",), pid=1)
    running(r, m, first, clock, 4 * MIN)
    queued(r, s, ("exact:b",), pid=2)
    queued(r, s, ("exact:a",), pid=3)
    one, two = line.places(r, s.id, T0, ALIVE)
    assert line.says(one, s) == (f"j4 is 1st in line; by estimates it starts around 14:06 or sooner and ends around "
                                 f"14:26; session {s.id} ends 15:00")
    assert line.says(two, s) == (f"j5 is 2nd in line, after j4; by estimates it starts around 14:26 or sooner and "
                                 f"ends around 14:36; session {s.id} ends 15:00")
