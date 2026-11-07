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
