import pytest

from flotilla.core.storage import LocalLogStore
from flotilla.lane import book


class Procs:
    def __init__(self):
        self.dead = set()

    def alive(self, pid, mark):
        return pid is None or not mark or pid not in self.dead


@pytest.fixture()
def lane(tmp_path):
    procs = Procs()
    return book.Book(LocalLogStore(tmp_path), procs), procs


def test_the_first_taker_holds_and_the_second_waits(lane):
    lanes, procs = lane
    first = lanes.enqueue("main session 1", "full suite", pid=11, mark="a")
    second = lanes.enqueue("review session 1", "tiers", pid=12, mark="b")
    assert lanes.grant(first.id, slots=1).state == book.HELD
    assert lanes.grant(second.id, slots=1) is None
    lanes.release(first.id)
    assert lanes.grant(second.id, slots=1).state == book.HELD


def test_capacity_two_holds_two(lane):
    lanes, procs = lane
    ids = [lanes.enqueue(f"main session {n}", "", pid=n, mark="m").id for n in (1, 2, 3)]
    assert [bool(lanes.grant(i, slots=2)) for i in ids] == [True, True, False]


def test_waiters_are_served_in_arrival_order(lane):
    lanes, procs = lane
    holder = lanes.enqueue("a", "", pid=1, mark="m")
    lanes.grant(holder.id, slots=1)
    early = lanes.enqueue("b", "", pid=2, mark="m")
    late = lanes.enqueue("c", "", pid=3, mark="m")
    lanes.release(holder.id)
    assert lanes.grant(late.id, slots=1) is None
    procs.dead.add(2)
    assert lanes.grant(late.id, slots=1).state == book.HELD
    assert [w.id for w in lanes.waiters()] == [early.id]


def test_a_dead_holder_does_not_block(lane):
    lanes, procs = lane
    holder = lanes.enqueue("a", "", pid=1, mark="m")
    lanes.grant(holder.id, slots=1)
    procs.dead.add(1)
    nxt = lanes.enqueue("b", "", pid=2, mark="m")
    assert lanes.grant(nxt.id, slots=1).state == book.HELD


def test_sweep_removes_only_bookings_with_no_process(lane):
    lanes, procs = lane
    by_hand = lanes.enqueue("a person", "measuring a race")
    lanes.grant(by_hand.id, slots=3)
    run = lanes.enqueue("main session 1", "", pid=5, mark="m")
    lanes.grant(run.id, slots=3)
    waiter = lanes.enqueue("main session 2", "", pid=6, mark="m")
    procs.dead.update({5, 6})
    swept = lanes.sweep()
    assert sorted((b.id, b.state) for b in swept) == sorted([(run.id, book.RELEASED), (waiter.id, book.LEFT)])
    assert [b.id for b in lanes.holders()] == [by_hand.id]


def test_an_expired_wait_keeps_its_reason(lane):
    lanes, procs = lane
    waiter = lanes.enqueue("a", "", pid=1, mark="m")
    lanes.expire(waiter.id, "waited 60 s; held by b")
    assert lanes.bookings()[waiter.id].state == book.EXPIRED
    assert lanes.bookings()[waiter.id].why == "waited 60 s; held by b"
