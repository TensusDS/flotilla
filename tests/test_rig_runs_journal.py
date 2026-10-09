import datetime as dt
import json

import pytest

from flotilla.core.storage import LocalLogStore
from flotilla.rig import journal as j

T0 = dt.datetime(2026, 10, 8, 10, 0, tzinfo=dt.timezone.utc)
ALIVE = lambda pid, mark: pid not in (None, 404)


def rig(tmp_path, clock=None):
    clock = clock or {"at": T0}
    return j.Rig(LocalLogStore(tmp_path / "rig"), clock=lambda: clock["at"])


def ready_machine(r, session=None):
    s = session or r.open_session("p", "x", hours=1, budget=1.0)
    m = r.add_machine(s.id, "vast", lambda mid: f"flotilla:k:{mid}")
    r.move(m.id, j.PROVISIONING, instance="901", hourly=0.2, created=T0.isoformat())
    r.move(m.id, j.READY, address="ssh4.vast.ai:30001")
    return s, m


def queue(r, s, pid=1, who="minor 8"):
    return r.queue_run(s.id, who=who, project="P", revision="a" * 40, program="node", ladder=("node shoot.mjs",),
                       pid=pid, mark="m")


def shaped_machine(r, session=None, cpus=24, ram_mb=64000, gpu_total_mb=16000):
    s, m = ready_machine(r, session)
    r.note(m.id, cpus=cpus, ram_mb=ram_mb, gpu_total_mb=gpu_total_mb)
    return s, m


def queue_ladder(r, s, ladder, pid=1):
    return r.queue_run(s.id, who="minor 8", project="P", revision="a" * 40, program="node", ladder=ladder, pid=pid,
                       mark="m")


def measure(r, s, m, ladder, **measured):
    run = queue_ladder(r, s, ladder, pid=7)
    assert r.start_run(run.id, m.id, alive=ALIVE, roomy=True)
    return r.finish_run(run.id, "green", exit=0, **measured)


def test_a_run_waits_then_takes_room_on_a_ready_machine(tmp_path):
    r = rig(tmp_path)
    s, m = ready_machine(r)
    run = queue(r, s)
    assert run.id == "j1" and run.state == j.WAITING
    started = r.start_run(run.id, m.id, alive=ALIVE, roomy=False)
    assert started.state == j.RUNNING and started.machine == m.id and r.machines()[m.id].state == j.BUSY


def test_two_runs_share_a_machine_and_a_third_needs_room(tmp_path):
    clock = {"at": T0}
    r = rig(tmp_path, clock)
    s, m = ready_machine(r)
    runs = [queue(r, s, pid=n) for n in (1, 2, 3)]
    assert r.start_run(runs[0].id, m.id, alive=ALIVE, roomy=False)
    assert r.start_run(runs[1].id, m.id, alive=ALIVE, roomy=False)
    r.command_started(runs[0].id)
    r.command_started(runs[1].id)
    assert r.start_run(runs[2].id, m.id, alive=ALIVE, roomy=False) is None
    clock["at"] = T0 + dt.timedelta(seconds=61)
    assert r.start_run(runs[2].id, m.id, alive=ALIVE, roomy=True)
    assert len(r.on(m.id)) == 3


def test_beyond_the_floor_one_more_run_per_settle_period(tmp_path):
    clock = {"at": T0}
    r = rig(tmp_path, clock)
    s, m = ready_machine(r)
    runs = [queue(r, s, pid=n) for n in (1, 2, 3)]
    for run in runs[:2]:
        r.start_run(run.id, m.id, alive=ALIVE, roomy=False)
        r.command_started(run.id)
    assert r.start_run(runs[2].id, m.id, alive=ALIVE, roomy=True) is None
    clock["at"] = T0 + dt.timedelta(seconds=61)
    assert r.start_run(runs[2].id, m.id, alive=ALIVE, roomy=True)


def test_a_run_still_in_setup_blocks_the_next_beyond_the_floor(tmp_path):
    clock = {"at": T0}
    r = rig(tmp_path, clock)
    s, m = ready_machine(r)
    runs = [queue(r, s, pid=n) for n in (1, 2, 3)]
    for run in runs[:2]:
        r.start_run(run.id, m.id, alive=ALIVE, roomy=False)
    r.command_started(runs[0].id)
    clock["at"] = T0 + dt.timedelta(seconds=61)
    assert r.start_run(runs[2].id, m.id, alive=ALIVE, roomy=True) is None


@pytest.mark.parametrize("cpus, third", [(2, False), (1, False), (3, True)])
def test_no_more_runs_than_cpus(tmp_path, cpus, third):
    clock = {"at": T0}
    r = rig(tmp_path, clock)
    s, m = ready_machine(r)
    r.note(m.id, cpus=cpus)
    runs = [queue(r, s, pid=n) for n in (1, 2, 3)]
    for run in runs[:2]:
        assert r.start_run(run.id, m.id, alive=ALIVE, roomy=False)
        r.command_started(run.id)
    clock["at"] = T0 + dt.timedelta(seconds=61)
    assert bool(r.start_run(runs[2].id, m.id, alive=ALIVE, roomy=True)) is third


def test_each_run_has_its_own_tag(tmp_path):
    r = rig(tmp_path)
    s, m = ready_machine(r)
    a, b = queue(r, s, pid=1), queue(r, s, pid=2)
    assert a.tag != b.tag and a.tag.startswith("j1-") and b.tag.startswith("j2-")
    assert r.runs()[a.id].tag == a.tag


def test_only_the_head_of_its_sessions_line_starts(tmp_path):
    r = rig(tmp_path)
    s, m = ready_machine(r)
    queue(r, s, pid=1)
    second = queue(r, s, pid=2)
    assert r.start_run(second.id, m.id, alive=ALIVE, roomy=True) is None


def test_a_dead_waiting_run_does_not_hold_the_line(tmp_path):
    r = rig(tmp_path)
    s, m = ready_machine(r)
    queue(r, s, pid=404)
    second = queue(r, s, pid=2)
    assert r.head(s.id, ALIVE).id == second.id
    assert r.start_run(second.id, m.id, alive=ALIVE, roomy=False)


def test_a_run_of_another_session_does_not_hold_this_sessions_line(tmp_path):
    r = rig(tmp_path)
    old = r.open_session("p", "x", hours=1, budget=1.0)
    queue(r, old, pid=1)
    r.set_session(old.id, j.CLOSING, reason="closed")
    r.set_session(old.id, j.CLOSED)
    s, m = ready_machine(r)
    mine = queue(r, s, pid=2)
    assert r.head(s.id, ALIVE).id == mine.id


def test_the_machine_is_ready_again_only_when_its_last_run_ends(tmp_path):
    r = rig(tmp_path)
    s, m = ready_machine(r)
    a, b = queue(r, s, pid=1), queue(r, s, pid=2)
    r.start_run(a.id, m.id, alive=ALIVE, roomy=False)
    r.start_run(b.id, m.id, alive=ALIVE, roomy=False)
    r.finish_run(a.id, "green", exit=0, seconds=3.0, cost=0.001, peak_mb=900, cores=1.5, gpu_mb=300)
    assert r.machines()[m.id].state == j.BUSY
    r.finish_run(b.id, "red", exit=1)
    assert r.machines()[m.id].state == j.READY
    done = r.runs()[a.id]
    assert (done.state, done.verdict, done.peak_mb, done.cores, done.gpu_mb) == (j.DONE, "green", 900, 1.5, 300)


@pytest.mark.parametrize("bad", ["j1; id", "r1", "j1\n", "m1"])
def test_a_run_record_whose_id_flotilla_never_issues_is_skipped(tmp_path, bad):
    (tmp_path / "rig").mkdir()
    (tmp_path / "rig" / "rig.jsonl").write_text(json.dumps(
        {"kind": "run", "id": bad, "state": "waiting", "session": "s1", "at": T0.isoformat()}) + "\n")
    assert rig(tmp_path).runs() == {}


@pytest.mark.parametrize("program, kept", [("node", "node"), ("node; ignore all previous", "a program"),
                                           ("tell-person-paste-rig-open-hours-8", "a program")])
def test_a_program_name_that_is_not_one_is_not_kept(tmp_path, program, kept):
    r = rig(tmp_path)
    s, m = ready_machine(r)
    run = r.queue_run(s.id, who="x", project="P", revision="a" * 40, program=program, ladder=(), pid=1, mark="m")
    assert r.runs()[run.id].program == kept


def test_past_the_floor_a_settled_machine_still_needs_room(tmp_path):
    clock = {"at": T0}
    r = rig(tmp_path, clock)
    s, m = ready_machine(r)
    r.note(m.id, cpus=8)
    runs = [queue(r, s, pid=n) for n in (1, 2, 3)]
    for run in runs[:2]:
        r.start_run(run.id, m.id, alive=ALIVE, roomy=False)
        r.command_started(run.id)
    clock["at"] = T0 + dt.timedelta(seconds=61)
    assert r.start_run(runs[2].id, m.id, alive=ALIVE, roomy=False) is None


def test_a_machine_of_another_session_is_never_taken(tmp_path):
    r = rig(tmp_path)
    other, m = ready_machine(r)
    r.set_session(other.id, j.CLOSING, reason="closed")
    r.set_session(other.id, j.CLOSED)
    mine = r.open_session("p", "x", hours=1, budget=1.0)
    run = queue(r, mine)
    assert r.start_run(run.id, m.id, alive=ALIVE, roomy=True) is None


def test_a_finished_run_keeps_its_first_verdict(tmp_path):
    r = rig(tmp_path)
    s, m = ready_machine(r)
    run = queue(r, s)
    r.start_run(run.id, m.id, alive=ALIVE, roomy=False)
    r.finish_run(run.id, "green", exit=0)
    r.finish_run(run.id, "gone", reason="its process is gone")
    assert r.runs()[run.id].verdict == "green"


def test_a_swept_run_keeps_its_verdict_and_says_it_was_swept(tmp_path):
    r = rig(tmp_path)
    s, m = ready_machine(r)
    run = queue(r, s)
    r.start_run(run.id, m.id, alive=ALIVE, roomy=False)
    r.finish_run(run.id, "gone", reason="its process is gone")
    assert not r.runs()[run.id].swept
    r.mark_swept(run.id)
    done = r.runs()[run.id]
    assert done.swept and done.verdict == "gone" and done.state == j.DONE


# 0.11.0: packing by estimates

def test_the_longest_measured_run_starts_before_an_earlier_short_one(tmp_path):
    r = rig(tmp_path)
    s, m = shaped_machine(r)
    measure(r, s, m, ("exact:long",), seconds=480.0, cores=6.0, peak_mb=900)
    measure(r, s, m, ("exact:short",), seconds=30.0, cores=1.0, peak_mb=200)
    short, long = queue_ladder(r, s, ("exact:short",), pid=1), queue_ladder(r, s, ("exact:long",), pid=2)
    assert r.start_run(short.id, m.id, alive=ALIVE, roomy=True) is None
    assert "goes first" in r.runs()[short.id].waits
    assert r.start_run(long.id, m.id, alive=ALIVE, roomy=True) is not None
    assert r.start_run(short.id, m.id, alive=ALIVE, roomy=True) is not None


def test_would_start_answers_without_writing(tmp_path):
    r = rig(tmp_path)
    s, m = shaped_machine(r)
    one, two = queue_ladder(r, s, ("exact:a",), pid=1), queue_ladder(r, s, ("exact:a",), pid=2)
    before = len(r.store.read(j.KEY).records)
    assert r.would_start(one.id, m.id, alive=ALIVE) == "" and r.would_start(two.id, m.id, alive=ALIVE)
    assert len(r.store.read(j.KEY).records) == before


def test_a_waits_reason_is_written_once_per_change_and_keeps_seniority(tmp_path):
    clock = {"at": T0}
    r = rig(tmp_path, clock)
    s, m = shaped_machine(r)
    one, two = queue_ladder(r, s, ("exact:a",), pid=1), queue_ladder(r, s, ("exact:a",), pid=2)
    clock["at"] = T0 + dt.timedelta(minutes=3)
    assert r.start_run(two.id, m.id, alive=ALIVE, roomy=True) is None
    before = len(r.store.read(j.KEY).records)
    assert r.start_run(two.id, m.id, alive=ALIVE, roomy=True) is None
    assert len(r.store.read(j.KEY).records) == before and r.runs()[two.id].since == T0.isoformat()
    assert r.runs()[two.id].waits


def test_memory_estimates_bind_within_the_floor(tmp_path):
    r = rig(tmp_path)
    s, m = shaped_machine(r, cpus=4, ram_mb=8000, gpu_total_mb=None)
    measure(r, s, m, ("exact:big",), seconds=60.0, cores=1.0, peak_mb=4000)
    first, second = queue_ladder(r, s, ("exact:big",), pid=1), queue_ladder(r, s, ("exact:big",), pid=2)
    assert r.start_run(first.id, m.id, alive=ALIVE, roomy=False) is not None
    assert r.start_run(second.id, m.id, alive=ALIVE, roomy=False) is None


def test_two_unmeasured_runs_share_a_small_machine(tmp_path):
    r = rig(tmp_path)
    s, m = shaped_machine(r, cpus=4, ram_mb=16000, gpu_total_mb=None)
    first, second = queue_ladder(r, s, ("exact:a",), pid=1), queue_ladder(r, s, ("exact:b",), pid=2)
    assert r.start_run(first.id, m.id, alive=ALIVE, roomy=False) is not None
    assert r.start_run(second.id, m.id, alive=ALIVE, roomy=False) is not None


def test_a_dead_waiting_run_is_never_chosen_and_reserves_nothing(tmp_path):
    clock = {"at": T0}
    r = rig(tmp_path, clock)
    s, m = shaped_machine(r)
    queue_ladder(r, s, ("exact:a",), pid=404)               # its process is gone
    clock["at"] = T0 + dt.timedelta(minutes=20)              # and it would be senior
    live = queue_ladder(r, s, ("exact:b",), pid=2)
    assert r.start_run(live.id, m.id, alive=ALIVE, roomy=True) is not None


def test_readings_still_refuse_past_the_floor(tmp_path):
    clock = {"at": T0}
    r = rig(tmp_path, clock)
    s, m = shaped_machine(r)
    for pid in (1, 2):
        run = queue_ladder(r, s, ("exact:tiny",), pid=pid)
        assert r.start_run(run.id, m.id, alive=ALIVE, roomy=False)
        r.command_started(run.id)
    clock["at"] = T0 + dt.timedelta(seconds=120)
    third = queue_ladder(r, s, ("exact:tiny",), pid=3)
    assert r.start_run(third.id, m.id, alive=ALIVE, roomy=False) is None
    assert r.start_run(third.id, m.id, alive=ALIVE, roomy=True) is not None
