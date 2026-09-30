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
                           clock=clock, sleep=sleep or clock.sleep)


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
    assert grant.booking.state == book.HELD and clock.now == acquire.POLL


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
