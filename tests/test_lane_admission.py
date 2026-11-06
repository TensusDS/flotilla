"""The grant by value: bookings to the policy's picture, freeze orders, thawing, the older-flotilla fallback, and a
frozen command that never stays frozen once its holder is dead or silent (lane shared machine design, 2-4 and 7)."""

import datetime as dt

import pytest

from flotilla.core.storage import LocalLogStore
from flotilla.lane import admission, book, freeze, policy

T0 = dt.datetime(2026, 11, 6, 10, 0, tzinfo=dt.timezone.utc)


class Procs:
    def __init__(self):
        self.dead = set()

    def alive(self, pid, mark):
        return pid is None or not mark or pid not in self.dead

    def start_mark(self, pid):
        return None if pid in self.dead else getattr(self, "marks", {}).get(pid, "m")


@pytest.fixture()
def lane(tmp_path):
    moment = {"at": T0}
    procs = Procs()
    lanes = book.Book(LocalLogStore(tmp_path), procs, clock=lambda: moment["at"])

    def later(seconds):
        moment["at"] += dt.timedelta(seconds=seconds)
    return lanes, procs, later


def value(tree=None, available=9000, swap=(8000, 8000), beat=lambda booking: 0.0, kind=freeze.SCOPE, **kw):
    return admission.Value(cpus=8, cpu_factor=1.5, light_reserve=0.2, immortal_s=900.0, max_runs=10, freeze=kind,
                           available_mb=lambda: available, floor_mb=0, swap_mb=lambda: swap,
                           tree_mb=lambda pid: (tree or {}).get(pid), beat_age_s=beat, **kw)


def holder(lanes, later, *, pid=11, tree=None, kind="", after=120):
    item = lanes.enqueue("main session 1", "suite", pid=pid, mark="m", kind=kind)
    held = lanes.grant(item.id, slots=1, value=value(tree=tree or {pid: 100}))
    assert held is not None and held.state == book.HELD
    later(after)
    return held


def raw(lanes, **record):
    with lanes.store.transaction(book.KEY) as tx:
        tx.append({"at": lanes._now(), **record})


def test_an_older_waiter_turns_the_lane_back_to_the_count(lane):   # spec 7
    lanes, procs, later = lane
    holder(lanes, later)
    raw(lanes, booking="b90", state=book.WAITING, who="old", pid=90, mark="m", rule=3)
    mine = lanes.enqueue("main session 2", "x", pid=12, mark="m")
    v = value(tree={11: 100})
    assert not admission.by_value(lanes.bookings(), lanes.live)
    assert lanes.grant(mine.id, slots=1, value=v) is None   # one slot, one holder: the count, as before
    lanes.leave("b90")
    held = lanes.grant(mine.id, slots=1, value=v)
    assert held is not None and held.path.startswith("value")


def test_a_run_is_admitted_by_free_memory_not_by_capacity(lane):
    lanes, _procs, later = lane
    holder(lanes, later, tree={11: 2000})
    mine = lanes.enqueue("main session 2", "x", pid=12, mark="m")
    held = lanes.grant(mine.id, slots=1, value=value(tree={11: 2000}, available=3000))
    assert held is not None and held.path.startswith("value") and held.started == lanes._now()


def test_a_freeze_is_ordered_and_the_waiter_waits_for_the_memory(lane):
    lanes, _procs, later = lane
    first = holder(lanes, later, tree={11: 5000})
    mine = lanes.enqueue("main session 2", "receipt", pid=12, mark="m", kind="push")
    assert lanes.grant(mine.id, slots=1, value=value(tree={11: 5000}, available=300)) is None
    victim = lanes.bookings()[first.id]
    assert (victim.state, victim.frozen, victim.order_by) == (book.HELD, "ordered", mine.id)
    assert victim.reclaim_mb == 1024 - 300 + policy.RECLAIM_MARGIN_MB
    assert "freezing" in lanes.waits_for


def test_an_unanswered_order_lapses_and_is_never_ordered_again(lane):
    lanes, _procs, later = lane
    first = holder(lanes, later, tree={11: 5000})
    mine = lanes.enqueue("main session 2", "receipt", pid=12, mark="m", kind="push")
    v = value(tree={11: 5000}, available=300)
    lanes.grant(mine.id, slots=1, value=v)
    later(admission.ANSWER_S + 1)
    assert lanes.grant(mine.id, slots=1, value=v) is None
    victim = lanes.bookings()[first.id]
    assert victim.frozen == "" and victim.unanswered
    later(5)
    lanes.grant(mine.id, slots=1, value=v)
    assert lanes.bookings()[first.id].frozen == ""


def test_a_frozen_run_whose_waiter_left_thaws_through_the_queue(lane):   # review focus 2
    lanes, _procs, later = lane
    first = holder(lanes, later, tree={11: 5000})
    mine = lanes.enqueue("main session 2", "receipt", pid=12, mark="m", kind="push")
    lanes.grant(mine.id, slots=1, value=value(tree={11: 5000}, available=300))
    lanes.frozen(first.id, freeze_kind="scope", unit="flotilla-lane-b1-11-1.scope", group=11, held_mb=5000)
    lanes.leave(mine.id)
    later(30)
    thawed = lanes.thaw(first.id, value(tree={11: 4100}, available=2000))
    assert thawed is not None and thawed.frozen == "" and thawed.frozen_s == 30.0


def test_a_frozen_run_waits_to_thaw_while_its_room_is_taken(lane):
    lanes, _procs, later = lane
    first = holder(lanes, later, tree={11: 5000})
    mine = lanes.enqueue("main session 2", "receipt", pid=12, mark="m", kind="push")
    lanes.grant(mine.id, slots=1, value=value(tree={11: 5000}, available=300))
    lanes.frozen(first.id, freeze_kind="scope", unit="flotilla-lane-b1-11-1.scope", group=11, held_mb=5000)
    later(5)
    assert lanes.thaw(first.id, value(tree={11: 4150}, available=100)) is None
    assert lanes.bookings()[first.id].frozen == "frozen"


def test_frozen_run_time_does_not_count(lane):
    lanes, _procs, later = lane
    first = holder(lanes, later, after=0)                     # started 10:00
    later(300)
    lanes.frozen(first.id, freeze_kind="signal", unit="", group=11, held_mb=100)   # frozen 10:05
    later(900)
    lanes.thaw(first.id, value(), force=True)                 # thawed 10:20
    later(60)                                                 # now 10:21
    runs, _m = admission.view(lanes.bookings(), {}, value(tree={11: 100}), now=T0 + dt.timedelta(seconds=1260),
                              live=lanes.live, mine="")
    assert next(r for r in runs if r.id == first.id).ran_s == 360.0


def test_an_older_holder_is_read_with_since_and_never_frozen(lane):   # review focus 5
    lanes, _procs, _later = lane
    raw(lanes, booking="b5", state=book.HELD, who="old", pid=55, mark="m", rule=3)
    now = T0 + dt.timedelta(seconds=600)
    runs, _m = admission.view(lanes.bookings(), {}, value(tree={55: 3000}), now=now, live=lanes.live, mine="")
    found = next(r for r in runs if r.id == "b5")
    assert found.pinned and not found.freezable and found.mem_mb == 3000
    assert (found.waited_s, found.ran_s) == (600.0, 600.0)


def test_memory_unread_freezes_nothing(lane):   # review focus 4
    lanes, _procs, later = lane
    first = holder(lanes, later, tree={11: 5000})
    mine = lanes.enqueue("main session 2", "receipt", pid=12, mark="m", kind="push")
    held = lanes.grant(mine.id, slots=1, value=value(tree={11: 5000}, available=None))
    assert held is not None and lanes.bookings()[first.id].frozen == ""


def test_a_booking_by_hand_is_heavy_with_the_full_cores(lane):
    lanes, _procs, _later = lane
    raw(lanes, booking="b7", state=book.HELD, who="max", pid=None, mark="", rule=4)
    runs, machine = admission.view(lanes.bookings(), {}, value(), now=T0, live=lanes.live, mine="")
    found = next(r for r in runs if r.id == "b7")
    assert found.heavy and found.pinned and found.par == 8 and found.mem_mb == round(0.8 * machine.give_mb)


def test_a_frozen_run_needs_back_only_what_it_moved_to_swap(lane):
    lanes, _procs, later = lane
    first = holder(lanes, later, tree={11: 5000})
    mine = lanes.enqueue("main session 2", "receipt", pid=12, mark="m", kind="push")
    lanes.grant(mine.id, slots=1, value=value(tree={11: 5000}, available=300))
    lanes.frozen(first.id, freeze_kind="scope", unit="flotilla-lane-b1-11-1.scope", group=11, held_mb=5000)
    runs, _m = admission.view(lanes.bookings(), {}, value(tree={11: 4150}), now=T0, live=lanes.live, mine="")
    assert next(r for r in runs if r.id == first.id).mem_mb == 1024 - 300 + policy.RECLAIM_MARGIN_MB
    assert next(r for r in runs if r.id == mine.id).holds


def test_sweep_thaws_a_dead_holders_unit(lane, monkeypatch):   # review focus 3
    lanes, procs, later = lane
    first = holder(lanes, later)
    lanes.frozen(first.id, freeze_kind="scope", unit="flotilla-lane-b1-11-1.scope", group=11, held_mb=100)
    thawed = []
    monkeypatch.setattr(freeze, "thaw_unit", lambda unit, **kw: thawed.append(unit) or True)
    procs.dead.add(11)
    lanes.sweep()
    assert thawed == ["flotilla-lane-b1-11-1.scope"] and lanes.bookings()[first.id].state == book.RELEASED


def test_the_next_grant_thaws_a_dead_holders_frozen_command(lane, monkeypatch):   # review focus 3: SIGKILL
    lanes, procs, later = lane
    first = holder(lanes, later)
    lanes.frozen(first.id, freeze_kind="signal", unit="", group=13, held_mb=100)   # its command's group, alive
    thawed = []
    monkeypatch.setattr(freeze, "thaw_tree", lambda pid, **kw: thawed.append(pid) or True)
    monkeypatch.setattr(freeze, "carries", lambda pid, booking: booking == first.id)   # its environment says so
    procs.dead.add(11)   # the holder (the wrapper) died; its frozen command did not
    mine = lanes.enqueue("main session 2", "x", pid=12, mark="m")
    lanes.grant(mine.id, slots=1, value=value())
    gone = lanes.bookings()[first.id]
    assert thawed == [13] and gone.state == book.RELEASED and gone.cut


def test_a_frozen_booking_whose_watch_went_silent_is_thawed_and_never_frozen_again(lane, monkeypatch):
    lanes, _procs, later = lane
    first = holder(lanes, later, tree={11: 5000})
    lanes.frozen(first.id, freeze_kind="scope", unit="flotilla-lane-b1-11-1.scope", group=11, held_mb=5000)
    thawed = []
    monkeypatch.setattr(freeze, "thaw_unit", lambda unit, **kw: thawed.append(unit) or True)
    mine = lanes.enqueue("main session 2", "receipt", pid=12, mark="m", kind="push")
    silent = value(tree={11: 5000}, available=300, beat=lambda booking: admission.BEAT_STALE_S + 1)
    lanes.grant(mine.id, slots=1, value=silent)
    found = lanes.bookings()[first.id]
    assert thawed == ["flotilla-lane-b1-11-1.scope"] and found.frozen == "" and found.unanswered
    later(5)
    lanes.grant(mine.id, slots=1, value=value(tree={11: 5000}, available=300))
    assert lanes.bookings()[first.id].frozen == ""   # never ordered again


def test_a_failed_thaw_does_not_block_the_grant(lane, monkeypatch):
    lanes, procs, later = lane
    first = holder(lanes, later)
    lanes.frozen(first.id, freeze_kind="scope", unit="flotilla-lane-b1-11-1.scope", group=11, held_mb=100)
    monkeypatch.setattr(freeze, "thaw_unit", lambda unit, **kw: False)
    procs.dead.add(11)
    mine = lanes.enqueue("main session 2", "x", pid=12, mark="m")
    assert lanes.grant(mine.id, slots=1, value=value()) is not None
    assert lanes.bookings()[first.id].frozen == "frozen"   # kept, so the next grant tries again


def _measured(n, peak, ladder):
    return book.Booking(id=f"b{n}", state=book.RELEASED, ended=(T0 + dt.timedelta(seconds=n)).isoformat(),
                        ladder=ladder, peak_mb=peak, seconds=10.0)


def test_the_default_memory_is_the_75th_percentile_of_first_runs():
    found = {}
    for n, peak in enumerate(range(100, 2001, 100)):   # 20 commands, first runs 100..2000 MB
        found[f"b{n}"] = _measured(n, peak, [f"exact:c{n}", f"prog:c{n}", "project:p"])
        found[f"c{n}"] = _measured(100 + n, 9000, [f"exact:c{n}", f"prog:c{n}", "project:p"])   # later runs
    assert admission.default_mb(found) == 1600


def test_under_twenty_first_runs_the_default_is_1024():
    found = {f"b{n}": _measured(n, 5000, [f"prog:c{n}"]) for n in range(19)}
    assert admission.default_mb(found) == 1024


def test_a_take_by_hand_keeps_its_promised_memory_from_later_runs(lane):
    lanes, _procs, _later = lane
    raw(lanes, booking="b7", state=book.HELD, who="max", pid=None, mark="", rule=4)
    runs, machine = admission.view(lanes.bookings(), {}, value(available=9000), now=T0, live=lanes.live, mine="")
    # its real use is seen as foreign memory (spec 4.6): free memory is what is free; the heavy room is what it takes
    assert machine.free_mb == 9000 and next(r for r in runs if r.id == "b7").mem_mb == 7200


def test_a_waiting_take_by_hand_is_heavy_with_the_full_cores(lane):
    lanes, _procs, _later = lane
    mine = lanes.enqueue("max", "by hand", pid=12, mark="m")
    runs, _m = admission.view(lanes.bookings(), {}, value(), now=T0, live=lanes.live, mine=mine.id, by_hand=mine.id)
    found = next(r for r in runs if r.id == mine.id)
    assert found.heavy and found.par == 8 and found.pinned



def test_orders_not_yet_answered_are_pending_swap(lane):   # final review C1
    lanes, _procs, later = lane
    first = holder(lanes, later, tree={11: 5000})
    mine = lanes.enqueue("main session 2", "receipt", pid=12, mark="m", kind="push")
    lanes.grant(mine.id, slots=1, value=value(tree={11: 5000}, available=300))
    _runs, machine = admission.view(lanes.bookings(), {}, value(tree={11: 5000}), now=T0, live=lanes.live, mine="")
    assert machine.swap_pending_mb == lanes.bookings()[first.id].reclaim_mb


def test_a_new_command_of_a_measured_project_is_expected_at_the_projects_size(lane):   # spec 2.2, ruling 41 reversed
    from flotilla.lane import estimate
    lanes, _procs, _later = lane
    done = {f"r{n}": book.Booking(id=f"r{n}", state=book.RELEASED, ended=(T0 - dt.timedelta(hours=n + 1)).isoformat(),
                                  ladder=[f"exact:old{n}", "project:p"], project="p", peak_mb=3000, seconds=60.0,
                                  cores=2.0, busy=0.2, verdict="green") for n in range(3)}
    mine = lanes.enqueue("main session 2", "x", pid=12, mark="m", ladder=["exact:brand-new", "project:p"], project="p")
    found = {**done, **lanes.bookings()}
    runs, _m = admission.view(found, estimate.history(found, now=T0), value(), now=T0, live=lanes.live, mine="")
    assert next(r for r in runs if r.id == mine.id).mem_mb == 3000


def test_sweep_keeps_a_booking_whose_command_would_not_thaw(lane, monkeypatch):   # final review I5a
    lanes, procs, later = lane
    first = holder(lanes, later)
    lanes.frozen(first.id, freeze_kind="scope", unit="flotilla-lane-b1-11-1.scope", group=11, held_mb=100)
    monkeypatch.setattr(freeze, "thaw_unit", lambda unit, **kw: False)
    procs.dead.add(11)
    lanes.sweep()
    assert lanes.bookings()[first.id].state == book.HELD   # kept, so a later grant or sweep thaws it


def test_a_frozen_record_is_not_written_over_a_released_booking(lane):   # final review I5c
    lanes, _procs, later = lane
    first = holder(lanes, later)
    lanes.release(first.id)
    assert lanes.frozen(first.id, freeze_kind="signal", unit="", group=11, held_mb=100) is None
    assert lanes.bookings()[first.id].state == book.RELEASED


def test_the_count_fallback_still_thaws_a_dead_holders_command(lane, monkeypatch):   # final review I5d
    lanes, procs, later = lane
    first = holder(lanes, later)
    lanes.frozen(first.id, freeze_kind="scope", unit="flotilla-lane-b1-11-1.scope", group=11, held_mb=100)
    thawed = []
    monkeypatch.setattr(freeze, "thaw_unit", lambda unit, **kw: thawed.append(unit) or True)
    procs.dead.add(11)
    raw(lanes, booking="b90", state=book.WAITING, who="old", pid=90, mark="m", rule=3)
    mine = lanes.enqueue("main session 2", "x", pid=12, mark="m")
    lanes.grant(mine.id, slots=1, value=value())
    assert thawed == ["flotilla-lane-b1-11-1.scope"]


def test_an_ended_booking_still_marked_frozen_is_thawed(lane, monkeypatch):   # final review I5e: an older sweep
    lanes, _procs, later = lane
    first = holder(lanes, later)
    lanes.frozen(first.id, freeze_kind="scope", unit="flotilla-lane-b1-11-1.scope", group=11, held_mb=100)
    raw(lanes, booking=first.id, state=book.RELEASED, why="no process behind it")   # what a 0.29 sweep writes
    thawed = []
    monkeypatch.setattr(freeze, "thaw_unit", lambda unit, **kw: thawed.append(unit) or True)
    mine = lanes.enqueue("main session 2", "x", pid=12, mark="m")
    lanes.grant(mine.id, slots=1, value=value())
    assert thawed == ["flotilla-lane-b1-11-1.scope"] and lanes.bookings()[first.id].frozen == ""


def test_a_crafted_record_thaws_nothing(lane, monkeypatch):   # final review I7
    lanes, _procs, _later = lane
    raw(lanes, booking="b66", state=book.HELD, who="x", pid=None, rule=4, frozen="frozen", group=4242,
        unit="--user ssh-agent.service")
    calls = []
    monkeypatch.setattr(freeze, "thaw_unit", lambda unit, **kw: calls.append(unit) or True)
    monkeypatch.setattr(freeze, "thaw_tree", lambda pid, **kw: calls.append(pid) or True)
    assert lanes.bookings()["b66"].unit == ""   # not a lane scope's name: folded away
    mine = lanes.enqueue("main session 2", "x", pid=12, mark="m")
    lanes.grant(mine.id, slots=1, value=value())
    assert calls == []


def test_a_group_whose_process_changed_is_not_signalled(lane, monkeypatch):   # final review I7
    lanes, procs, later = lane
    first = holder(lanes, later)
    lanes.frozen(first.id, freeze_kind="signal", unit="", group=11, held_mb=100)
    calls = []
    monkeypatch.setattr(freeze, "thaw_tree", lambda pid, **kw: calls.append(pid) or True)
    procs.dead.add(11)   # the holder and its group leader are gone; a new process may have its number
    mine = lanes.enqueue("main session 2", "x", pid=12, mark="m")
    lanes.grant(mine.id, slots=1, value=value())
    assert calls == [] and lanes.bookings()[first.id].state == book.RELEASED



def test_a_reused_group_number_is_not_signalled(lane, monkeypatch):   # final review I7
    lanes, procs, later = lane
    first = holder(lanes, later)
    lanes.frozen(first.id, freeze_kind="signal", unit="", group=13, held_mb=100)   # leader 13 started at mark "m"
    calls = []
    monkeypatch.setattr(freeze, "thaw_tree", lambda pid, **kw: calls.append(pid) or True)
    procs.dead.add(11)
    procs.marks = {13: "a later process"}   # 13 is another process now
    mine = lanes.enqueue("main session 2", "x", pid=12, mark="m")
    lanes.grant(mine.id, slots=1, value=value())
    assert calls == [] and lanes.bookings()[first.id].state == book.RELEASED



def test_a_process_group_is_thawed_only_when_it_carries_the_booking(lane, monkeypatch):   # security review, 0.30.1
    lanes, procs, later = lane
    first = holder(lanes, later)
    lanes.frozen(first.id, freeze_kind="signal", unit="", group=13, held_mb=100)
    calls = []
    monkeypatch.setattr(freeze, "thaw_tree", lambda pid, **kw: calls.append(pid) or True)
    monkeypatch.setattr(freeze, "carries", lambda pid, booking: False)   # mark matches, but not the lane's process
    procs.dead.add(11)
    mine = lanes.enqueue("main session 2", "x", pid=12, mark="m")
    lanes.grant(mine.id, slots=1, value=value())
    assert calls == [] and lanes.bookings()[first.id].state == book.RELEASED


def test_a_scope_of_another_booking_is_never_thawed_for_this_one(lane, monkeypatch):   # security review, 0.30.1
    lanes, procs, later = lane
    first = holder(lanes, later)
    lanes.frozen(first.id, freeze_kind="scope", unit="flotilla-lane-b77-11-1.scope", group=11, held_mb=100)
    calls = []
    monkeypatch.setattr(freeze, "thaw_unit", lambda unit, **kw: calls.append(unit) or True)
    procs.dead.add(11)
    mine = lanes.enqueue("main session 2", "x", pid=12, mark="m")
    lanes.grant(mine.id, slots=1, value=value())
    assert calls == []
