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


def test_a_waiting_booking_carries_its_command_and_ladder(lane):
    lanes, _ = lane
    item = lanes.enqueue("main session 1", "vitest", pid=1, mark="m", command="npx vitest run",
                         ladder=["exact:npx vitest run", "project:p"], will_run=[], project="p")
    assert (item.command, item.ladder, item.project, item.rule) == ("npx vitest run",
                                                                     ["exact:npx vitest run", "project:p"], "p",
                                                                     book.RULE)


def test_a_release_carries_the_measurement(lane):
    lanes, _ = lane
    item = lanes.enqueue("a", "", pid=1, mark="m")
    lanes.grant(item.id, slots=1)
    done = lanes.release(item.id, measured={"seconds": 41.5, "peak_mb": 1730, "cores": 6.8, "busy": 0.62,
                                            "verdict": "green", "ran": [{"name": "unit", "seconds": 40.0}]})
    assert (done.seconds, done.peak_mb, done.cores, done.busy, done.verdict) == (41.5, 1730, 6.8, 0.62, "green")
    assert [(t["name"], t["seconds"]) for t in done.ran] == [("unit", 40.0)] and not done.cut


def test_a_swept_holder_is_cut_and_carries_no_measurement(lane):
    lanes, procs = lane
    item = lanes.enqueue("a", "", pid=5, mark="m")
    lanes.grant(item.id, slots=1)
    procs.dead.add(5)
    [swept] = lanes.sweep()
    assert swept.state == book.RELEASED and swept.cut and swept.seconds is None


def test_text_another_session_wrote_is_made_visible(lane):
    lanes, _ = lane
    item = lanes.enqueue("a", "", pid=1, mark="m", command="echo \x1b[2Jboom", ladder=["exact:echo \x1b[2J"])
    assert "\x1b" not in item.command and all("\x1b" not in step for step in item.ladder)


def test_old_journal_lines_fold_and_estimate_nothing(tmp_path):
    store = LocalLogStore(tmp_path)
    with store.transaction(book.KEY) as tx:
        tx.append({"at": "2026-09-27T22:45:55+00:00", "booking": "b1", "mark": "x", "note": "handover receipt",
                   "pid": 1, "run_for": "", "state": "waiting", "who": "main session 1"})
        tx.append({"at": "2026-09-27T22:45:56+00:00", "booking": "b1", "state": "held"})
        tx.append({"at": "2026-09-27T22:46:30+00:00", "booking": "b1", "state": "released"})
    item = book.fold(store.read(book.KEY).records)["b1"]
    assert (item.command, item.ladder, item.seconds, item.rule, item.cut) == ("", [], None, None, False)


def _line(store, **event):
    with store.transaction(book.KEY) as tx:
        tx.append({"at": "2026-10-05T10:00:00+00:00", "booking": "b1", "state": "released", **event})


@pytest.mark.parametrize("event", [
    {"seconds": "2"}, {"seconds": float("nan")}, {"seconds": float("inf")}, {"seconds": True}, {"seconds": -5},
    {"cores": "6"}, {"busy": "x"}, {"busy": 7}, {"peak_mb": [1]}, {"ladder": 5}, {"ladder": [{"a": 1}]},
    {"ran": "unit"}, {"ran": [5]}, {"cut": "no"}, {"verdict": ["green"]}, {"will_run": "unit"},
])
def test_a_hand_written_line_of_the_wrong_type_is_dropped_not_fatal(tmp_path, event):
    """Review of stage 1 (security): every session writes the journal; one line of the wrong type crashed
    `flotilla lane` for all of them. Fields of the wrong type or out of range fold as unknown."""
    store = LocalLogStore(tmp_path)
    _line(store, **event)
    item = book.fold(store.read(book.KEY).records)["b1"]
    assert item.seconds is None and item.cores is None and item.busy is None and item.peak_mb is None
    assert isinstance(item.ladder, list) and all(isinstance(step, str) for step in item.ladder)
    assert isinstance(item.ran, list) and all(isinstance(t, dict) for t in item.ran)
    assert item.cut in (True, False) and isinstance(item.verdict, str)


def test_a_line_with_a_naive_or_unreadable_time_is_skipped(tmp_path):
    store = LocalLogStore(tmp_path)
    with store.transaction(book.KEY) as tx:
        tx.append({"at": "2026-10-05T00:00:00", "booking": "b1", "state": "released", "seconds": 3})
        tx.append({"at": 17, "booking": "b2", "state": "released", "seconds": 3})
        tx.append({"at": "2026-10-05T10:00:00+00:00", "booking": "b3", "state": "released", "seconds": 3})
    assert set(book.fold(store.read(book.KEY).records)) == {"b3"}


def test_a_commands_text_is_bounded(lane):
    lanes, _ = lane
    item = lanes.enqueue("a", "", pid=1, mark="m", command="x" * 100_000, ladder=["exact:" + "y" * 100_000])
    assert len(item.command) <= book.MAX_TEXT and all(len(step) <= book.MAX_TEXT for step in item.ladder)


def test_a_huge_command_is_cut_before_it_reaches_the_file(tmp_path):
    """Every session folds the whole journal under its lock: one megabyte written once is paid by every fold."""
    store = LocalLogStore(tmp_path)
    book.Book(store, Procs()).enqueue("a", "", pid=1, mark="m", command="x" * 1_000_000,
                                      ladder=["exact:" + "y" * 1_000_000])
    [record] = store.read(book.KEY).records
    assert len(record["command"]) <= book.MAX_TEXT and len(record["ladder"][0]) <= book.MAX_TEXT
