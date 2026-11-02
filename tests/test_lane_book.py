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


def test_a_setup_entry_keeps_its_kind_through_the_journal(lane):
    lanes, _ = lane
    item = lanes.enqueue("a", "", pid=1, mark="m")
    lanes.grant(item.id, slots=1)
    done = lanes.release(item.id, measured={"seconds": 3.0, "ran": [
        {"name": "setup", "status": "green", "seconds": 3.0, "kind": "setup"},
        {"name": "setup", "status": "green", "seconds": 1.0, "kind": "anything else"}]})
    assert [t.get("kind") for t in done.ran] == ["setup", None]


def _whole(tmp_path):
    return book.fold(LocalLogStore(tmp_path).read(book.KEY).records)


def test_a_cached_book_folds_only_new_lines(tmp_path, monkeypatch):
    """Twosuns item 14: each poll folded the whole journal again, so a waiter's cost grew with the file."""
    import json
    lanes = book.Book(LocalLogStore(tmp_path), Procs())
    for n in range(3):
        lanes.enqueue(f"main session {n}", "suite", pid=None)
    lanes.bookings()
    book.Book(LocalLogStore(tmp_path), Procs()).release("b2")   # another process writes
    parsed = []
    real = json.loads
    monkeypatch.setattr(json, "loads", lambda raw, *a, **k: parsed.append(1) or real(raw, *a, **k))
    found = lanes.bookings()
    assert len(parsed) == 1 and found == _whole(tmp_path)
    parsed.clear()
    assert lanes.bookings() == found and parsed == []   # nothing new: nothing parsed


def test_a_replaced_journal_is_read_again_whole(tmp_path):
    lanes = book.Book(LocalLogStore(tmp_path), Procs())
    for n in range(3):
        lanes.enqueue(f"main session {n}", "suite", pid=None)
    lanes.bookings()
    other = book.Book(LocalLogStore(tmp_path / "other"), Procs())
    other.enqueue("review session 9", "elsewhere", pid=None)
    (tmp_path / "other" / "lane.jsonl").replace(tmp_path / "lane.jsonl")
    assert lanes.bookings() == _whole(tmp_path) and [i.who for i in lanes.bookings().values()] == ["review session 9"]


def test_a_torn_line_is_never_folded_from_the_cache(tmp_path):
    lanes = book.Book(LocalLogStore(tmp_path), Procs())
    lanes.enqueue("main session 1", "suite", pid=None)
    lanes.bookings()
    with open(tmp_path / "lane.jsonl", "ab") as handle:
        handle.write(b'{"at": "2026-11-02T10:00:00+00:00", "booking": "b1", "state": "rele')
    assert lanes.bookings()["b1"].state == book.WAITING
    lanes.release("b1")   # the append sets the fragment aside, then writes a whole line
    assert lanes.bookings()["b1"].state == book.RELEASED and lanes.bookings() == _whole(tmp_path)


def test_a_booking_handed_out_does_not_change_when_the_cache_advances(tmp_path):
    lanes = book.Book(LocalLogStore(tmp_path), Procs())
    lanes.enqueue("main session 1", "suite", pid=None)
    before = lanes.bookings()
    held = before["b1"]
    lanes.release("b1")
    assert held.state == book.WAITING and before["b1"] is held
    assert lanes.bookings()["b1"].state == book.RELEASED


def test_an_in_place_edit_of_an_earlier_line_is_read_again(tmp_path):
    """Final review of 0.24.2, I2: an edit in place (same inode, same length, far from the tail) left a running
    waiter folding a stale prefix for good."""
    lanes = book.Book(LocalLogStore(tmp_path), Procs())
    for n in range(4):
        lanes.enqueue(f"main session {n}", "suite", pid=None)
    assert lanes.bookings()["b1"].state == book.WAITING
    path = tmp_path / "lane.jsonl"
    data = path.read_bytes()
    edited = data.replace(b'"state":"waiting"', b'"state":"expired"', 1)
    assert len(edited) == len(data) and edited != data
    with open(path, "r+b") as handle:   # in place: the inode stays
        handle.write(edited)
    assert lanes.bookings()["b1"].state == book.EXPIRED and lanes.bookings() == _whole(tmp_path)


class Tick:
    """A clock that moves a minute each time it is read, so every event has its own time."""

    def __init__(self):
        import datetime as dt
        self.now = dt.datetime(2026, 11, 2, 12, 0, tzinfo=dt.timezone.utc)

    def __call__(self):
        import datetime as dt
        self.now += dt.timedelta(minutes=1)
        return self.now


def _fair(tmp_path, procs=None):
    return book.Book(LocalLogStore(tmp_path), procs or Procs(), clock=Tick())


def _wait(lanes, who, project, rule=None):
    item = lanes.enqueue(who, "suite", pid=None, project=project)
    if rule is not None:   # a booking an older flotilla wrote
        with lanes.store.transaction(book.KEY) as tx:
            tx.append({"at": lanes._now(), "booking": item.id, "state": book.WAITING, "rule": rule})
    return item


def _held_path(tmp_path, booking_id):
    events = LocalLogStore(tmp_path).read(book.KEY).records
    return [e.get("path") for e in events if e.get("booking") == booking_id and e.get("state") == book.HELD][-1]


def test_fair_admits_the_other_projects_single_waiter_at_the_next_release(tmp_path):
    """Void-lanes-online, 2026-10-31: 7-second runs waited a median 42.8 min behind another project's receipts."""
    lanes = _fair(tmp_path)
    first = _wait(lanes, "twosuns main 1", "twosuns")
    assert lanes.grant(first.id, slots=1, share="fair") is not None
    backlog = [_wait(lanes, f"twosuns main {n}", "twosuns") for n in range(2, 12)]
    single = _wait(lanes, "void main 7", "void")
    lanes.release(first.id)
    assert lanes.grant(backlog[0].id, slots=1, share="fair") is None
    assert lanes.grant(single.id, slots=1, share="fair") is not None
    assert _held_path(tmp_path, single.id).startswith("fair: void")


def test_fair_keeps_order_within_a_project(tmp_path):
    lanes = _fair(tmp_path)
    a, b, c = (_wait(lanes, f"main {n}", "twosuns") for n in range(3))
    assert lanes.grant(b.id, slots=1, share="fair") is None
    assert lanes.grant(a.id, slots=1, share="fair") is not None


def test_fair_with_one_project_is_fifo(tmp_path):
    lanes = _fair(tmp_path)
    order = [_wait(lanes, f"main {n}", "twosuns") for n in range(3)]
    for item in order:
        assert lanes.grant(item.id, slots=1, share="fair") is not None
        lanes.release(item.id)


def test_fair_counts_a_project_holding_a_slot_as_served_now(tmp_path):
    lanes = _fair(tmp_path)
    long_run = _wait(lanes, "twosuns main 1", "twosuns")
    assert lanes.grant(long_run.id, slots=2, share="fair") is not None
    short = _wait(lanes, "void main 1", "void")
    assert lanes.grant(short.id, slots=2, share="fair") is not None
    twosuns_next = _wait(lanes, "twosuns main 2", "twosuns")
    void_next = _wait(lanes, "void main 2", "void")
    lanes.release(short.id)   # void was served after twosuns, but twosuns still holds a slot
    assert lanes.grant(twosuns_next.id, slots=2, share="fair") is None
    assert lanes.grant(void_next.id, slots=2, share="fair") is not None


def test_a_rule_two_waiter_never_yields_to_an_older_one(tmp_path):
    lanes = _fair(tmp_path)
    served = _wait(lanes, "twosuns main 1", "twosuns")
    lanes.grant(served.id, slots=1, share="fair")
    lanes.release(served.id)
    mine = _wait(lanes, "twosuns main 2", "twosuns")
    older = _wait(lanes, "void main 1", "void", rule=1)   # never served, so the fair choice, but it cannot know
    assert lanes.grant(mine.id, slots=1, share="fair") is not None
    assert _held_path(tmp_path, mine.id).startswith("fifo")
    assert older.id in {item.id for item in lanes.waiters()}


def test_an_older_waiter_first_in_line_takes_the_slot_as_today(tmp_path):
    lanes = _fair(tmp_path)
    older = _wait(lanes, "void main 1", "void", rule=1)
    newer = _wait(lanes, "twosuns main 1", "twosuns")
    assert lanes.grant(newer.id, slots=1, share="fair") is None   # an older rule's waiter first in line goes first
    assert lanes.grant(older.id, slots=1) is not None             # it asks the old way


def test_a_dead_waiter_is_never_the_fair_choice(tmp_path):
    procs = Procs()
    lanes = _fair(tmp_path, procs)
    served = _wait(lanes, "twosuns main 1", "twosuns")
    lanes.grant(served.id, slots=1, share="fair")
    lanes.release(served.id)
    mine = _wait(lanes, "twosuns main 2", "twosuns")
    gone = lanes.enqueue("void main 1", "suite", pid=4242, mark="m", project="void")
    procs.dead.add(4242)
    assert lanes.grant(mine.id, slots=1, share="fair") is not None
    assert gone.id not in {item.id for item in lanes.holders()}


def test_fair_never_grants_past_the_slots(tmp_path):
    seats = [_fair(tmp_path) for _ in range(4)]   # four processes on one journal
    waiting = [_wait(seats[n], f"{project} main {n}", project)
               for n, project in enumerate(["twosuns", "void", "twosuns", "void"])]
    for _ in range(3):
        for seat, item in zip(seats, waiting):
            seat.grant(item.id, slots=2, share="fair")
    assert len(seats[0].holders()) == 2
