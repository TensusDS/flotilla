import pytest

from flotilla.core.storage import LocalLogStore
from flotilla.lane import acquire, book
from flotilla.lane.machine import Answer, Reading
from flotilla.lane.procs import Proc


class Procs:
    def __init__(self):
        self.dead = set()

    def alive(self, pid, mark):
        return pid is None or not mark or pid not in self.dead

    def start_mark(self, pid):
        return "mark"


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


QUIET = Reading([Answer("foreign run", False, "no unbooked run"), Answer("ci", False, "no CI here")], [], [])


@pytest.fixture()
def lanes(tmp_path):
    return book.Book(LocalLogStore(tmp_path), Procs())


def take(lanes, reading=QUIET, *, capacity=1, wait=0.0, clock=None, sleep=None, who="main session 1"):
    clock = clock or Clock()
    return acquire.acquire(lanes, lambda: reading, who=who, note="suite", capacity=capacity, wait=wait,
                           clock=clock, sleep=sleep or clock.sleep, jitter=lambda: 0.0)


def test_a_free_lane_is_taken_at_once(lanes):
    grant = take(lanes)
    assert grant.booking.state == book.HELD and grant.why == ""


def test_a_held_lane_is_refused_naming_the_holder(lanes):
    held = take(lanes, who="review session 1")
    grant = take(lanes)
    assert grant.booking is None and "held by review session 1 (suite)" in grant.why
    assert [item.state for item in lanes.bookings().values()] == [book.HELD, book.EXPIRED]
    assert held.booking.id in lanes.bookings()


def test_waiting_ends_when_the_holder_releases(lanes):
    held = take(lanes, who="review session 1")
    clock = Clock()

    def sleep(seconds):
        clock.sleep(seconds)
        lanes.release(held.booking.id)
    grant = take(lanes, wait=60, clock=clock, sleep=sleep)
    assert grant.booking.state == book.HELD and clock.now == 1.0   # woken by the release, not by the next poll


def test_a_computing_foreign_run_uses_a_slot(lanes):
    busy = Reading([Answer("foreign run", True, "1 unbooked run(s) computing: pid 9 `pytest`"),
                    Answer("ci", False, "no CI here")], [Proc(9, 1, "pytest")], [])
    refused = take(lanes, busy)
    assert refused.booking is None and "foreign run: 1 unbooked run(s) computing" in refused.why
    assert take(lanes, busy, capacity=2).booking is not None


def test_ci_on_this_machine_blocks_whatever_the_capacity(lanes):
    ci = Reading([Answer("foreign run", False, "none"), Answer("ci", True, "a CI run is using this machine")], [], [])
    grant = take(lanes, ci, capacity=4)
    assert grant.booking is None and "ci: a CI run is using this machine" in grant.why


def test_a_question_that_could_not_be_asked_blocks(lanes):
    unknown = Reading([Answer("foreign run", None, "the process table could not be read"),
                       Answer("ci", False, "no CI here")], [], [])
    assert take(lanes, unknown, capacity=4).booking is None


def test_an_interrupted_wait_leaves_the_queue(lanes):
    take(lanes, who="review session 1")

    def interrupted(seconds):
        raise KeyboardInterrupt
    with pytest.raises(KeyboardInterrupt):
        take(lanes, wait=60, sleep=interrupted)
    assert lanes.waiters() == []


def test_held_releases_on_exit_even_after_an_error(lanes):
    with pytest.raises(RuntimeError):
        with acquire.held(lanes, lambda: QUIET, who="main session 1", note="suite", capacity=1, wait=0,
                          table=Procs()):
            assert len(lanes.holders()) == 1
            raise RuntimeError("the run broke")
    assert lanes.holders() == []
    take(lanes, who="review session 1")
    with pytest.raises(acquire.LaneRefused, match="held by review session 1"):
        with acquire.held(lanes, lambda: QUIET, who="main session 1", note="suite", capacity=1, wait=0,
                          table=Procs()):
            pass


def test_a_hand_take_waits_as_its_process_and_holds_without_one(lanes):
    grant = acquire.acquire(lanes, lambda: QUIET, who="main session 1", note="race", capacity=1, wait=0, pid=77,
                            mark="m", by_hand=True, clock=Clock(), sleep=Clock().sleep)
    assert grant.booking.state == book.HELD and grant.booking.pid is None and grant.booking.mark == ""


def test_a_refusal_names_who_waits_ahead(lanes):
    take(lanes, who="review session 1")
    ahead = lanes.enqueue("main session 2", "tiers", pid=5, mark="m")
    lanes.release(next(iter(lanes.holders())).id)
    grant = take(lanes)
    assert grant.booking is None and f"waiting ahead: {ahead.id} main session 2 (tiers)" in grant.why


def test_waiting_is_said_out_loud_and_ends_at_the_deadline(lanes):
    take(lanes, who="review session 1")
    clock, said = Clock(), []
    grant = acquire.acquire(lanes, lambda: QUIET, who="main session 1", note="suite", capacity=1, wait=4,
                            clock=clock, sleep=clock.sleep, say=said.append)
    assert grant.booking is None and clock.now == 4
    assert said and said[0].startswith("waiting for the lane: held by review session 1")


def test_a_lasting_unknown_refuses_at_once(lanes):
    lasting = Reading([Answer("ci", None, "a gate command's queue cannot be asked; pass --no-lane", lasting=True)],
                      [], [])
    clock = Clock()
    grant = take(lanes, lasting, wait=1800, clock=clock)
    assert grant.booking is None and clock.now == 0 and "--no-lane" in grant.why


def test_a_booking_inside_a_booking_is_reused(lanes, monkeypatch):
    import os
    monkeypatch.delenv(acquire.ENV, raising=False)

    class Mine(Procs):
        def ancestors(self, pid):
            return [os.getpid()]
    with acquire.held(lanes, lambda: QUIET, who="main session 1", note="outer", capacity=1, wait=0, table=Mine()):
        with acquire.held(lanes, lambda: QUIET, who="main session 1", note="inner", capacity=1, wait=0,
                          table=Mine()) as inner:
            assert inner.why.startswith("inside booking")
            assert len(lanes.holders()) == 1
    assert lanes.holders() == [] and acquire.ENV not in os.environ


def test_low_memory_holds_the_lane_whatever_the_capacity(lanes):
    low = Reading([Answer("foreign run", False, "none"), Answer("ci", False, "no CI here"),
                   Answer("memory", True, "878 MB available, under the floor of 1500 MB")], [], [])
    grant = take(lanes, low, capacity=4)
    assert grant.booking is None and "memory: 878 MB available" in grant.why


def test_held_releases_with_what_the_caller_measured(lanes):
    class Table(Procs):
        def ancestors(self, pid):
            return []
    with acquire.held(lanes, lambda: QUIET, who="a", note="n", capacity=1, wait=0, table=Table(),
                      command="make", ladder=["exact:make"], project="p") as grant:
        grant.measured.update({"seconds": 2.0, "verdict": "green"})
    item = lanes.bookings()[grant.booking.id]
    assert (item.state, item.command, item.seconds, item.verdict) == (book.RELEASED, "make", 2.0, "green")


def test_a_nested_grant_is_released_and_measured_by_its_owner_only(lanes, monkeypatch):
    class Table(Procs):
        def ancestors(self, pid):
            return [1]
    outer = lanes.enqueue("a", "outer", pid=1, mark="mark")
    lanes.grant(outer.id, slots=1)
    monkeypatch.setenv(acquire.ENV, outer.id)
    with acquire.held(lanes, lambda: QUIET, who="a", note="inner", capacity=1, wait=0, table=Table()) as grant:
        grant.measured.update({"seconds": 9.0})
    item = lanes.bookings()[outer.id]
    assert item.state == book.HELD and item.seconds is None


def test_no_room_holds_the_lane_whatever_the_capacity(lanes):
    full = Reading([Answer("foreign run", False, "none"), Answer("ci", False, "no CI here"),
                    Answer("memory", False, "plenty"), Answer("disk", True, "900 MB free on this disk")], [], [])
    grant = take(lanes, full, capacity=4)
    assert grant.booking is None and "disk: 900 MB free" in grant.why


def test_a_capacity_raised_while_waiting_admits_the_waiter(lanes):
    take(lanes, who="review session 1")
    clock = Clock()
    polls = []

    def capacity():
        polls.append(clock.now)
        return 1 if len(polls) == 1 else 2
    grant = take(lanes, capacity=capacity, wait=60, clock=clock)
    assert grant.booking is not None and grant.booking.state == book.HELD
    assert len(polls) == 2 and clock.now == acquire.POLL


def test_a_capacity_cut_stops_new_grants_only(lanes):
    first = take(lanes, who="review session 1", capacity=2)
    second = take(lanes, who="review session 2", capacity=2)
    third = take(lanes, who="main session 3", capacity=lambda: 1)
    assert third.booking is None and "held by review session 1" in third.why
    held = {item.id for item in lanes.holders()}
    assert held == {first.booking.id, second.booking.id}


def test_an_unreadable_capacity_waits_at_one(monkeypatch):
    from flotilla.lane import commands

    def torn(_):
        raise ValueError("machine.toml: expected '=' at line 2")
    monkeypatch.setattr(commands, "read_machine", torn)
    assert commands.capacity() == 1



def test_the_share_is_read_on_each_poll(lanes):
    take(lanes, who="review session 1")
    clock = Clock()
    asked = []

    def share():
        asked.append(clock.now)
        return "fair"
    acquire.acquire(lanes, lambda: QUIET, who="main session 2", note="suite", capacity=1, share=share, wait=30,
                    clock=clock, sleep=clock.sleep)
    assert len(asked) >= 2



def test_a_fair_choice_that_does_not_take_the_slot_is_passed_over(lanes):
    """Final review of 0.25.0, I1: a waiter its own project's checks hold back (CI on this machine for its profile, a
    memory floor of its own) never asks for the slot, and under fair it stayed the choice, so every other project
    waited with the slot free. After a grace it is passed over."""
    served = lanes.enqueue("a 1", "suite", pid=None, project="A")
    assert lanes.grant(served.id, slots=1, share="fair") is not None
    lanes.release(served.id)
    lanes.enqueue("b 1", "suite", pid=None, project="B")   # the fair choice, held back by its own checks: never asks
    clock = Clock()
    grant = acquire.acquire(lanes, lambda: QUIET, who="a 2", note="suite", capacity=1, share="fair", wait=600,
                            clock=clock, sleep=clock.sleep, project="A")
    assert grant.booking is not None and acquire.FAIR_GRACE <= clock.now < acquire.FAIR_GRACE + 2 * acquire.POLL


def test_the_room_built_from_the_reading_reaches_the_grant(lanes, monkeypatch):
    seen = []
    real = book.Book.grant

    def grant(self, booking_id, **kwargs):
        seen.append(kwargs.get("room"))
        return real(self, booking_id, **kwargs)
    monkeypatch.setattr(book.Book, "grant", grant)
    busy = Reading([Answer("foreign run", True, "1 unbooked run computing")], [Proc(5, 1, "vitest")], [])
    acquire.acquire(lanes, lambda: busy, who="main session 1", note="suite", capacity=2, wait=0.0,
                    clock=Clock(), sleep=lambda s: None,
                    room=lambda reading: book.Room(cpus=8, factor=1.5, memory_left=lambda: 8000,
                                                   foreign=len(reading.computing)))
    assert seen and seen[0] == book.Room(cpus=8, factor=1.5, memory_left=seen[0].memory_left, foreign=1)



# -- handoff without the 15-second poll (lane light runs design, section 4) ----------------------------------------

def test_a_waiter_wakes_within_a_second_of_a_journal_change(lanes):
    holder = take(lanes, who="review session 1")
    clock, reads = Clock(), []

    def sleep(seconds):
        clock.sleep(seconds)
        if clock.now >= 3 and lanes.holders():
            lanes.release(holder.booking.id)   # another process releases the lane at second 3

    def read():
        reads.append(clock.now)
        return QUIET
    grant = acquire.acquire(lanes, read, who="main session 1", note="suite", capacity=1, wait=60.0, clock=clock,
                            sleep=sleep, jitter=lambda: 0.0)
    assert grant.booking is not None and clock.now <= 4.0 and reads == [0.0]   # the release, not the machine


def test_without_a_change_the_machine_is_read_once_per_poll(lanes):
    take(lanes, who="review session 1")
    clock, reads, naps = Clock(), [], []

    def sleep(seconds):
        naps.append(seconds)
        clock.sleep(seconds)

    acquire.acquire(lanes, lambda: reads.append(clock.now) or QUIET, who="main session 1", note="suite",
                    capacity=1, wait=31.0, clock=clock, sleep=sleep, jitter=lambda: 0.0)
    assert reads == [0.0, 15.0, 30.0] and max(naps) <= 1.0   # the last second reuses the look before it



def test_a_light_waiter_is_told_its_own_reason(lanes, monkeypatch):
    monkeypatch.setattr(book.Book, "grant", lambda self, booking_id, **k: setattr(self, "waits_for",
                                                                                  "needs 400 MB, 300 left"))
    said = []
    grant = acquire.acquire(lanes, lambda: QUIET, who="main session 1", note="suite", capacity=1, wait=0.0,
                            clock=Clock(), sleep=lambda s: None, say=said.append, jitter=lambda: 0.0)
    assert grant.booking is None and grant.why.endswith("needs 400 MB, 300 left")


def test_a_waiter_woken_by_every_write_still_reads_the_machine_once_per_poll(lanes):
    take(lanes, who="review session 1")
    clock, reads = Clock(), []

    def sleep(seconds):
        clock.sleep(seconds)
        lanes.enqueue("main session 9", "noise", pid=None)   # the journal changes every second

    acquire.acquire(lanes, lambda: reads.append(clock.now) or QUIET, who="main session 1", note="suite",
                    capacity=1, wait=31.0, clock=clock, sleep=sleep, jitter=lambda: 0.0)
    assert reads == [0.0, 15.0, 30.0]


def test_a_heavy_waiter_is_told_only_what_it_waits_for(tmp_path):
    """0.28.1, the regression review: a heavy waiter's reason named light holders and light waiters the grant does
    not make it wait for."""
    from test_lane_book import BIG, FILE, RECEIPT, booked, teach
    lanes = book.Book(LocalLogStore(tmp_path / "why"), Procs())
    teach(lanes)
    room = book.Room(cpus=8, factor=1.5, memory_left=lambda: 4000)
    heavy = booked(lanes, RECEIPT, 1)
    assert lanes.grant(heavy.id, slots=1, room=room)
    assert lanes.grant(booked(lanes, FILE, 2).id, slots=1, room=room).path.startswith("light")
    assert lanes.grant(booked(lanes, BIG, 3).id, slots=1, room=room) is None
    mine = booked(lanes, RECEIPT, 4)
    why = acquire.in_the_way(lanes, QUIET, 1, mine.id)
    assert "seat 1" in why and "seat 2" not in why and "seat 3" not in why, why


def test_a_wake_between_looks_does_not_push_the_next_look_back(lanes):
    """The final review of 0.28.1, M2: after a wake that reused the last look, the next nap was a whole poll again, so
    the machine was looked at again two polls after the last look, not one."""
    take(lanes, who="review session 1")
    clock, reads = Clock(), []

    def sleep(seconds):
        clock.sleep(seconds)
        if clock.now == 14.0:
            lanes.enqueue("main session 9", "noise", pid=None)   # one write between two looks

    acquire.acquire(lanes, lambda: reads.append(clock.now) or QUIET, who="main session 1", note="suite",
                    capacity=1, wait=31.0, clock=clock, sleep=sleep, jitter=lambda: 0.0)
    assert reads == [0.0, 15.0, 30.0]
