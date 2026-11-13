"""The scheduler: one tick of the lane daemon - read, decide, apply (lane daemon design, 4.2)."""

import datetime as dt
from pathlib import Path

import pytest

from flotilla.core.storage import LocalLogStore
from flotilla.lane import admission, book, freeze, groups, policy, scheduler, tick


class Procs:
    def alive(self, pid, mark):
        return True

    def start_mark(self, pid):
        return "m"


class FakeGroups:
    def __init__(self):
        self.readings, self.found, self.writes = {}, {}, []

    def cgroup_of(self, unit, booking):
        return self.found.get((unit, booking))

    def read(self, path):
        return self.readings.get(path)

    def set_high(self, path, mb):
        self.writes.append((path, mb))
        r = self.readings[path]
        self.readings[path] = groups.Reading(r.current_mb, mb, r.high_events, r.pressure)
        return True


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def value_of(available=10000):
    box = {"available": available}

    def make(reading=None):
        return admission.Value(cpus=8, cpu_factor=1.5, light_reserve=0.2, immortal_s=900.0, max_runs=10,
                               freeze=freeze.SCOPE, available_mb=lambda: box["available"], floor_mb=2000,
                               swap_mb=lambda: (8000, 10000), tree_mb=lambda pid: 100,
                               beat_age_s=lambda booking: 0.0)
    make.box = box
    return make


@pytest.fixture()
def lane(tmp_path):
    lanes = book.Book(LocalLogStore(tmp_path), Procs())
    clock, fake, values = Clock(), FakeGroups(), value_of()
    sched = scheduler.Scheduler(lanes, values, signer=(4242, "m9"), groups_io=fake, clock=clock, default_floor=2000,
                                total_mb=lambda: 15603)
    return lanes, sched, clock, fake, values


def raw(lanes, **record):
    with lanes.store.transaction(book.KEY) as tx:
        tx.append({"at": lanes._now(), **record})


def reading(mb, events=0, pressure=1.0, high=None, refault=0):
    return groups.Reading(mb, high, events, pressure, refault)


def test_history_pressed_time_counts_only_while_at_the_ceiling_and_refaulting():
    """Pressed is hot: held at its ceiling and reading its own pages back from swap. The bench of 2026-11-07 under a
    1 GB ceiling: a hot run 43-49 MB/s, a cold one 0; pressure alone overlapped (hot 13-19 %, cold up to 17 %)."""
    h = scheduler.History()
    for step in range(7):
        got = h.run(step * 2.0, "b1", reading(1000, events=step, refault=step * 30), 1000)   # 15 MB/s
    assert got["pressed_s"] == 10.0 and got["at_ceiling"]
    assert h.run(14.0, "b1", reading(1000, events=7, refault=180 + 4), 1000)["pressed_s"] == 0.0   # 2 MB/s: cold
    assert h.run(16.0, "b1", reading(1000, events=7, refault=300), 1000)["pressed_s"] == 0.0   # not held there
    cold = scheduler.History()
    for step in range(7):
        got = cold.run(step * 2.0, "b2", reading(1000, events=step, pressure=80.0), 1000)   # pushing out, no refault
    assert got["pressed_s"] == 0.0


def test_history_growth_over_a_minute():
    h = scheduler.History()
    h.run(0.0, "b1", reading(500), None)
    assert h.run(30.0, "b1", reading(2500), None)["grew_mb"] == 2000
    assert h.run(60.0, "b1", reading(2500), None)["grew_mb"] == 2000
    assert h.run(95.0, "b1", reading(2600), None)["grew_mb"] == 100
    assert h.run(96.0, "b1", reading(2600), None)["peak_mb"] == 2600


def test_history_over_time_resets_above_the_floor():
    h = scheduler.History()
    assert h.machine(0.0, 1500, 2000) == 0.0
    assert h.machine(12.0, 1500, 2000) == 12.0
    assert h.machine(14.0, 2500, 2000) == 0.0
    assert h.machine(16.0, None, 2000) == 0.0


def test_step_admits_a_waiter_and_sets_its_ceiling(lane):
    lanes, sched, clock, fake, values = lane
    item = lanes.enqueue("w", "", pid=11, mark="m")
    actions, picture = sched.step()
    found = lanes.bookings()[item.id]
    start = policy.start_ceiling(admission.DEFAULT_MB)
    assert found.state == book.HELD and found.ceiling_mb == start
    unit = f"flotilla-lane-{item.id}-11-1.scope"
    lanes.scoped(item.id, unit)
    fake.found[(unit, item.id)] = Path("/g")
    fake.readings[Path("/g")] = reading(100)
    clock.t = 2.0
    sched.step()
    clock.t = 4.0
    sched.step()
    assert fake.writes == [(Path("/g"), start)]


def test_step_rewrites_its_own_ceiling_the_file_lost(lane):
    lanes, sched, clock, fake, values = lane
    item = lanes.enqueue("w", "", pid=11, mark="m")
    sched.step()   # admitted, with the start ceiling this daemon decided
    unit = f"flotilla-lane-{item.id}-11-1.scope"
    lanes.scoped(item.id, unit)
    fake.found[(unit, item.id)] = Path("/g")
    fake.readings[Path("/g")] = reading(500, high=None)   # someone set the file back to max
    clock.t = 2.0
    sched.step()
    assert fake.writes == [(Path("/g"), policy.start_ceiling(admission.DEFAULT_MB))]


def test_step_never_writes_a_ceiling_it_did_not_decide(lane):   # final review C1: the journal is anyone's
    lanes, sched, clock, fake, values = lane
    item = lanes.enqueue("v", "", pid=11, mark="m")
    unit = f"flotilla-lane-{item.id}-11-1.scope"
    raw(lanes, booking=item.id, state=book.HELD, started=lanes._now(), unit=unit)
    fake.found[(unit, item.id)] = Path("/g")
    fake.readings[Path("/g")] = reading(3000, high=None)
    raw(lanes, booking=item.id, state=book.HELD, ceiling_mb=0)   # a line any session can append
    sched.step()
    assert all(mb is None or mb >= 256 for _path, mb in fake.writes) and (Path("/g"), 0) not in fake.writes
    assert fake.writes == [(Path("/g"), 3000 + policy.slack(3000))]   # its own decision: the run's use and slack


def test_a_restarted_daemon_takes_the_groups_ceiling_as_the_truth(lane):
    lanes, sched, clock, fake, values = lane
    item = lanes.enqueue("v", "", pid=11, mark="m")
    unit = f"flotilla-lane-{item.id}-11-1.scope"
    raw(lanes, booking=item.id, state=book.HELD, started=lanes._now(), unit=unit, ceiling_mb=300)
    fake.found[(unit, item.id)] = Path("/g")
    fake.readings[Path("/g")] = reading(900, high=1024)   # what the daemon before this one set
    actions, picture = sched.step()
    assert fake.writes == [] and picture["groups"][item.id]["ceiling_mb"] == 1024


def test_step_uses_the_largest_floor_among_live_bookings(lane):
    lanes, sched, clock, fake, values = lane
    lanes.enqueue("a", "", pid=11, mark="m", floor_mb=1000)
    lanes.enqueue("b", "", pid=12, mark="m", floor_mb=3000)
    sched.step()
    assert sched.last_machine.floor_mb == 3000


def test_step_skips_a_held_back_waiter(lane):
    lanes, sched, clock, fake, values = lane
    item = lanes.enqueue("w", "", pid=11, mark="m")
    lanes.hold_back(item.id, "ci")
    sched.step()
    assert lanes.bookings()[item.id].state == book.WAITING


def test_step_signs_its_orders(lane):
    lanes, sched, clock, fake, values = lane
    item = lanes.enqueue("v", "", pid=11, mark="m")
    raw(lanes, booking=item.id, state=book.HELD, started=lanes._now())
    values.box["available"] = 1000
    sched.step()
    clock.t = 12.0
    actions, picture = sched.step()
    found = lanes.bookings()[item.id]
    assert (found.frozen, found.order_cause, found.order_pid, found.order_mark) == ("ordered", "over", 4242, "m9")
    assert picture["over"]["mb"] == 1000


def test_slot_reports_its_scope(tmp_path):
    own = tmp_path / "cgroup"
    own.write_text("0::/user.slice/user-1000.slice/user@1000.service/app.slice/term.scope\n")
    seen = []
    slot = freeze.Slot(freeze.SCOPE, proc_self=own)
    slot.on_scope = seen.append
    slot.contain(["true"], booking="b1")
    assert seen == [slot.unit] and slot.unit.startswith("flotilla-lane-b1-")



def test_a_floor_out_of_reason_does_not_make_the_machine_over(lane):   # final review C2
    lanes, sched, clock, fake, values = lane
    victim = lanes.enqueue("v", "", pid=11, mark="m")
    raw(lanes, booking=victim.id, state=book.HELD, started=lanes._now())
    lanes.enqueue("w", "", pid=12, mark="m", floor_mb=9_000_000)
    values.box["available"] = 9000
    sched.step()
    clock.t = 12.0
    sched.step()
    assert sched.last_machine.floor_mb == 3900 and lanes.bookings()[victim.id].frozen == ""



def test_a_squeeze_ends_in_the_daemons_record_though_the_journal_says_it_ended(lane):   # review of b6173cd, minor 1
    # a line any session can append says "not squeezed" first: nothing is left to write, and the daemon's own record
    # of the squeeze must end all the same, or the run stays "squeezed" in the status and may hold starts back
    from pathlib import Path
    lanes, sched, clock, fake, values = lane
    item = lanes.enqueue("v", "", pid=11, mark="m")
    unit = f"flotilla-lane-{item.id}-11-1.scope"
    raw(lanes, booking=item.id, state=book.HELD, started=lanes._now(), unit=unit)
    group = Path("/g")
    fake.found[(unit, item.id)] = group
    fake.readings[group] = reading(3000, high=None)
    sched.step()                                   # held at its use and the slack
    values.box["available"] = 1500                 # below the floor long enough: squeezed
    for moment in (2.0, 14.0, 16.0):
        clock.t = moment
        sched.step()
        if item.id in sched.squeezed:
            break
    assert item.id in sched.squeezed
    ceiling = sched.ceilings[item.id]
    values.box["available"] = 10000
    fake.readings[group] = reading(ceiling // 2, high=ceiling)   # its use fell well under the ceiling it has
    raw(lanes, booking=item.id, state=book.HELD, unanswered=True)   # frozen no more
    raw(lanes, booking=item.id, state=book.HELD, squeezed=False)    # the forged line
    clock.t = 30.0
    actions, _picture = sched.step()
    assert item.id in actions.restored and item.id not in sched.squeezed



def test_a_run_whose_group_does_not_read_the_decided_ceiling_is_not_bound(lane, monkeypatch):   # third review of B
    from flotilla.lane import tick as tick_module
    lanes, sched, clock, fake, values = lane
    item = lanes.enqueue("w", "", pid=11, mark="m")
    sched.step()   # admitted, with the start ceiling this daemon decided
    unit = f"flotilla-lane-{item.id}-11-1.scope"
    lanes.scoped(item.id, unit)
    fake.found[(unit, item.id)] = Path("/g")
    seen = []
    real = tick_module.tick
    monkeypatch.setattr(tick_module, "tick", lambda runs, machine: seen.append(runs) or real(runs, machine))
    start = policy.start_ceiling(admission.DEFAULT_MB)
    fake.readings[Path("/g")] = reading(100, high=start)
    clock.t = 2.0
    sched.step()
    fake.readings[Path("/g")] = reading(100, high=None)   # the write did not hold: nothing bounds it now
    clock.t = 4.0
    sched.step()
    bound = [next(r for r in runs if r.id == item.id).bound for runs in seen]
    assert bound == [True, False]



def test_a_ceiling_set_back_to_another_number_or_written_clamped_is_read_right(lane, monkeypatch):   # review a93e2cc
    from flotilla.lane import tick as tick_module
    lanes, sched, clock, fake, values = lane
    item = lanes.enqueue("w", "", pid=11, mark="m")
    sched.step()
    unit = f"flotilla-lane-{item.id}-11-1.scope"
    lanes.scoped(item.id, unit)
    fake.found[(unit, item.id)] = Path("/g")
    seen = []
    real = tick_module.tick
    monkeypatch.setattr(tick_module, "tick", lambda runs, machine: seen.append(runs) or real(runs, machine))
    start = policy.start_ceiling(admission.DEFAULT_MB)
    fake.readings[Path("/g")] = reading(100, high=start + 500)   # a session set it to another number
    clock.t = 2.0
    sched.step()
    sched.ceilings[item.id] = 200   # a ceiling below the floor of ceilings, as a restart reads one: written as 256
    fake.readings[Path("/g")] = reading(100, high=policy.MIN_CEILING_MB)
    clock.t = 4.0
    sched.step()
    assert [next(r for r in runs if r.id == item.id).bound for runs in seen] == [False, True]


def test_a_reading_adopted_after_a_restart_is_taken_as_it_will_be_written(lane):   # review of 179cd95, L5
    lanes, sched, clock, fake, values = lane
    item = lanes.enqueue("w", "", pid=11, mark="m")
    sched.step()
    unit = f"flotilla-lane-{item.id}-11-1.scope"
    lanes.scoped(item.id, unit)
    fake.found[(unit, item.id)] = Path("/g")
    sched.ceilings.clear()   # a daemon that restarted: what it decided is gone, the group's reading is not
    fake.readings[Path("/g")] = reading(100, high=100)   # set below the floor of ceilings by someone else
    clock.t = 2.0
    sched.step()
    assert sched.ceilings[item.id] == policy.MIN_CEILING_MB


class Moment:
    """The journal's clock, moved by hand: a waiter's priority grows with the minutes it has waited."""

    def __init__(self):
        self.now = dt.datetime(2026, 11, 13, 10, 0, tzinfo=dt.timezone.utc)

    def __call__(self):
        return self.now

    def on(self, minutes):
        self.now += dt.timedelta(minutes=minutes)


def _one_run_lane(tmp_path, share, served=("alpha",), runs=1):
    """A lane that starts `runs` runs at a time, each project of `served` served in turn, a minute apart."""
    moment = Moment()
    lanes = book.Book(LocalLogStore(tmp_path), Procs(), clock=moment)
    values = value_of()

    def at_most(reading=None):
        import dataclasses
        return dataclasses.replace(values(reading), max_runs=runs)
    sched = scheduler.Scheduler(lanes, at_most, signer=(4242, "m9"), groups_io=FakeGroups(), clock=Clock(),
                                default_floor=2000, total_mb=lambda: 15603, share=share)
    for project in served:
        turn = lanes.enqueue(f"{project} 0", "", pid=11, mark="m", project=project)
        sched.step()
        assert lanes.bookings()[turn.id].state == book.HELD
        moment.on(1)
        lanes.release(turn.id)
    return lanes, sched, moment


@pytest.mark.parametrize("share, first", [("fair", "beta"), ("fifo", "alpha"), (lambda: "fair", "beta")])
def test_the_daemon_gives_an_even_turn_to_the_project_served_least_recently(tmp_path, share, first):
    """Two waiters of equal priority - both waited past the wait's saturation - and one start: under `fair` the
    project not served goes first, though it asked later; under `fifo` the older waiter does (lane shared machine
    design, 2.10). `share` is read each tick when it is a callable, as machine.toml is."""
    lanes, sched, moment = _one_run_lane(tmp_path, share)
    alpha = lanes.enqueue("alpha 1", "", pid=12, mark="m", project="alpha")
    moment.on(1)
    beta = lanes.enqueue("beta 1", "", pid=13, mark="m", project="beta")
    moment.on(20)
    sched.step()
    found = lanes.bookings()
    held = {item.project for item in (found[alpha.id], found[beta.id]) if item.state == book.HELD}
    assert held == {first}


def test_a_fair_share_never_passes_a_waiter_of_higher_priority(tmp_path):
    """Fair share is the tie-break, not a priority: a waiter that has waited ten minutes goes before one of a
    project not served that has waited one (lane shared machine design, 2.10)."""
    lanes, sched, moment = _one_run_lane(tmp_path, "fair")
    alpha = lanes.enqueue("alpha 1", "", pid=12, mark="m", project="alpha")
    moment.on(10)
    beta = lanes.enqueue("beta 1", "", pid=13, mark="m", project="beta")
    moment.on(1)
    sched.step()
    found = lanes.bookings()
    assert (found[alpha.id].state, found[beta.id].state) == (book.HELD, book.WAITING)


def test_two_slots_freed_in_one_tick_go_to_two_projects(tmp_path):   # review of 8dfcc24
    """alpha was served before beta; three waiters of equal priority, two of alpha's first in line, and room for
    two: alpha's first and beta's start - a turn taken counts before the next place is weighed."""
    lanes, sched, moment = _one_run_lane(tmp_path, "fair", served=("alpha", "beta"), runs=2)
    a1 = lanes.enqueue("alpha 1", "", pid=12, mark="m", project="alpha")
    a2 = lanes.enqueue("alpha 2", "", pid=13, mark="m", project="alpha")
    b1 = lanes.enqueue("beta 1", "", pid=14, mark="m", project="beta")
    moment.on(20)
    sched.step()
    found = lanes.bookings()
    assert [found[item.id].state for item in (a1, a2, b1)] == [book.HELD, book.WAITING, book.HELD]


def test_the_share_is_read_again_each_tick(tmp_path):   # review of 8dfcc24
    mode = {"now": "fifo"}
    lanes, sched, moment = _one_run_lane(tmp_path, lambda: mode["now"])
    alpha = lanes.enqueue("alpha 1", "", pid=12, mark="m", project="alpha")
    moment.on(1)
    beta = lanes.enqueue("beta 1", "", pid=13, mark="m", project="beta")
    moment.on(20)
    mode["now"] = "fair"   # the person changed machine.toml while the daemon ran
    sched.step()
    found = lanes.bookings()
    assert (found[alpha.id].state, found[beta.id].state) == (book.WAITING, book.HELD)
