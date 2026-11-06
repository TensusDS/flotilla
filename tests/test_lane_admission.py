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
    lanes.frozen(first.id, freeze_kind="scope", unit="u1", group=11, held_mb=5000)
    lanes.leave(mine.id)
    later(30)
    thawed = lanes.thaw(first.id, value(tree={11: 4100}, available=2000))
    assert thawed is not None and thawed.frozen == "" and thawed.frozen_s == 30.0


def test_a_frozen_run_waits_to_thaw_while_its_room_is_taken(lane):
    lanes, _procs, later = lane
    first = holder(lanes, later, tree={11: 5000})
    mine = lanes.enqueue("main session 2", "receipt", pid=12, mark="m", kind="push")
    lanes.grant(mine.id, slots=1, value=value(tree={11: 5000}, available=300))
    lanes.frozen(first.id, freeze_kind="scope", unit="u1", group=11, held_mb=5000)
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
    lanes.frozen(first.id, freeze_kind="scope", unit="u1", group=11, held_mb=5000)
    runs, _m = admission.view(lanes.bookings(), {}, value(tree={11: 4150}), now=T0, live=lanes.live, mine="")
    assert next(r for r in runs if r.id == first.id).mem_mb == 1024 - 300 + policy.RECLAIM_MARGIN_MB
    assert next(r for r in runs if r.id == mine.id).holds


def test_sweep_thaws_a_dead_holders_unit(lane, monkeypatch):   # review focus 3
    lanes, procs, later = lane
    first = holder(lanes, later)
    lanes.frozen(first.id, freeze_kind="scope", unit="u1", group=11, held_mb=100)
    thawed = []
    monkeypatch.setattr(freeze, "thaw_unit", lambda unit, **kw: thawed.append(unit) or True)
    procs.dead.add(11)
    lanes.sweep()
    assert thawed == ["u1"] and lanes.bookings()[first.id].state == book.RELEASED


def test_the_next_grant_thaws_a_dead_holders_frozen_command(lane, monkeypatch):   # review focus 3: SIGKILL
    lanes, procs, later = lane
    first = holder(lanes, later)
    lanes.frozen(first.id, freeze_kind="signal", unit="", group=11, held_mb=100)
    thawed = []
    monkeypatch.setattr(freeze, "thaw_tree", lambda pid, **kw: thawed.append(pid) or True)
    procs.dead.add(11)
    mine = lanes.enqueue("main session 2", "x", pid=12, mark="m")
    lanes.grant(mine.id, slots=1, value=value())
    gone = lanes.bookings()[first.id]
    assert thawed == [11] and gone.state == book.RELEASED and gone.cut


def test_a_frozen_booking_whose_watch_went_silent_is_thawed_and_never_frozen_again(lane, monkeypatch):
    lanes, _procs, later = lane
    first = holder(lanes, later, tree={11: 5000})
    lanes.frozen(first.id, freeze_kind="scope", unit="u1", group=11, held_mb=5000)
    thawed = []
    monkeypatch.setattr(freeze, "thaw_unit", lambda unit, **kw: thawed.append(unit) or True)
    mine = lanes.enqueue("main session 2", "receipt", pid=12, mark="m", kind="push")
    silent = value(tree={11: 5000}, available=300, beat=lambda booking: admission.BEAT_STALE_S + 1)
    lanes.grant(mine.id, slots=1, value=silent)
    found = lanes.bookings()[first.id]
    assert thawed == ["u1"] and found.frozen == "" and found.unanswered
    later(5)
    lanes.grant(mine.id, slots=1, value=value(tree={11: 5000}, available=300))
    assert lanes.bookings()[first.id].frozen == ""   # never ordered again


def test_a_failed_thaw_does_not_block_the_grant(lane, monkeypatch):
    lanes, procs, later = lane
    first = holder(lanes, later)
    lanes.frozen(first.id, freeze_kind="scope", unit="u1", group=11, held_mb=100)
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
