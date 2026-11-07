import pytest

from flotilla.core.storage import LocalLogStore
import datetime as dt

from flotilla.lane import book
from flotilla.lane import estimate as est

NOW = lambda: dt.datetime.now(dt.timezone.utc)  # noqa: E731


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
    holder = lanes.enqueue("a", "", pid=11, mark="m")   # 1 is init: no run's pid, folded away (0.30.1)
    lanes.grant(holder.id, slots=1)
    procs.dead.add(11)
    nxt = lanes.enqueue("b", "", pid=12, mark="m")
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
    """Alpha item 14: each poll folded the whole journal again, so a waiter's cost grew with the file."""
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
    """Gamma, 2026-10-31: 7-second runs waited a median 42.8 min behind another project's receipts."""
    lanes = _fair(tmp_path)
    first = _wait(lanes, "alpha main 1", "alpha")
    assert lanes.grant(first.id, slots=1, share="fair") is not None
    backlog = [_wait(lanes, f"alpha main {n}", "alpha") for n in range(2, 12)]
    single = _wait(lanes, "void main 7", "void")
    lanes.release(first.id)
    assert lanes.grant(backlog[0].id, slots=1, share="fair") is None
    assert lanes.grant(single.id, slots=1, share="fair") is not None
    assert _held_path(tmp_path, single.id).startswith("fair: void")


def test_fair_keeps_order_within_a_project(tmp_path):
    lanes = _fair(tmp_path)
    a, b, c = (_wait(lanes, f"main {n}", "alpha") for n in range(3))
    assert lanes.grant(b.id, slots=1, share="fair") is None
    assert lanes.grant(a.id, slots=1, share="fair") is not None


def test_fair_with_one_project_is_fifo(tmp_path):
    lanes = _fair(tmp_path)
    order = [_wait(lanes, f"main {n}", "alpha") for n in range(3)]
    for item in order:
        assert lanes.grant(item.id, slots=1, share="fair") is not None
        lanes.release(item.id)


def test_fair_counts_a_project_holding_a_slot_as_served_now(tmp_path):
    lanes = _fair(tmp_path)
    long_run = _wait(lanes, "alpha main 1", "alpha")
    assert lanes.grant(long_run.id, slots=2, share="fair") is not None
    short = _wait(lanes, "void main 1", "void")
    assert lanes.grant(short.id, slots=2, share="fair") is not None
    alpha_next = _wait(lanes, "alpha main 2", "alpha")
    void_next = _wait(lanes, "void main 2", "void")
    lanes.release(short.id)   # void was served after alpha, but alpha still holds a slot
    assert lanes.grant(alpha_next.id, slots=2, share="fair") is None
    assert lanes.grant(void_next.id, slots=2, share="fair") is not None


def test_a_rule_two_waiter_never_yields_to_an_older_one(tmp_path):
    lanes = _fair(tmp_path)
    served = _wait(lanes, "alpha main 1", "alpha")
    lanes.grant(served.id, slots=1, share="fair")
    lanes.release(served.id)
    mine = _wait(lanes, "alpha main 2", "alpha")
    older = _wait(lanes, "void main 1", "void", rule=1)   # never served, so the fair choice, but it cannot know
    assert lanes.grant(mine.id, slots=1, share="fair") is not None
    assert _held_path(tmp_path, mine.id).startswith("fifo")
    assert older.id in {item.id for item in lanes.waiters()}


def test_an_older_waiter_first_in_line_takes_the_slot_as_today(tmp_path):
    lanes = _fair(tmp_path)
    older = _wait(lanes, "void main 1", "void", rule=1)
    newer = _wait(lanes, "alpha main 1", "alpha")
    assert lanes.grant(newer.id, slots=1, share="fair") is None   # an older rule's waiter first in line goes first
    assert lanes.grant(older.id, slots=1) is not None             # it asks the old way


def test_a_dead_waiter_is_never_the_fair_choice(tmp_path):
    procs = Procs()
    lanes = _fair(tmp_path, procs)
    served = _wait(lanes, "alpha main 1", "alpha")
    lanes.grant(served.id, slots=1, share="fair")
    lanes.release(served.id)
    mine = _wait(lanes, "alpha main 2", "alpha")
    gone = lanes.enqueue("void main 1", "suite", pid=4242, mark="m", project="void")
    procs.dead.add(4242)
    assert lanes.grant(mine.id, slots=1, share="fair") is not None
    assert gone.id not in {item.id for item in lanes.holders()}


def test_fair_never_grants_past_the_slots(tmp_path):
    seats = [_fair(tmp_path) for _ in range(4)]   # four processes on one journal
    waiting = [_wait(seats[n], f"{project} main {n}", project)
               for n, project in enumerate(["alpha", "void", "alpha", "void"])]
    for _ in range(3):
        for seat, item in zip(seats, waiting):
            seat.grant(item.id, slots=2, share="fair")
    assert len(seats[0].holders()) == 2


# -- light runs beside one heavy run (lane light runs design) --------------------------------------------------------

ROOM = book.Room(cpus=8, factor=1.5, memory_left=lambda: 8000)
RECEIPT, FILE, BIG, MID = ["exact:receipt"], ["exact:one-file"], ["exact:big-light"], ["exact:mid"]


def measured(lanes, ladder, seconds, cores, peak_mb, n=3):
    """Teach the journal what `ladder` takes: n released runs of it."""
    for _ in range(n):
        item = lanes.enqueue("seed", "", pid=900, mark="m", ladder=ladder, command="seed")
        assert lanes.grant(item.id, slots=99)
        lanes.release(item.id, measured={"seconds": seconds, "peak_mb": peak_mb, "cores": cores, "verdict": "green"})


def teach(lanes):
    measured(lanes, RECEIPT, 660.0, 7.0, 1700)
    measured(lanes, FILE, 4.0, 1.0, 300)
    measured(lanes, BIG, 20.0, 1.0, 5000)
    measured(lanes, MID, 30.0, 2.0, 400)


def booked(lanes, ladder, pid):
    return lanes.enqueue(f"seat {pid}", "", pid=pid, mark="m", ladder=ladder, command="x")


def test_a_light_run_starts_beside_a_heavy_holder_at_capacity_one(lane):
    lanes, _ = lane
    teach(lanes)
    heavy = booked(lanes, RECEIPT, 1)
    assert lanes.grant(heavy.id, slots=1, room=ROOM).path == "fifo"
    light = lanes.grant(booked(lanes, FILE, 2).id, slots=1, room=ROOM)
    assert light is not None and light.path.startswith("light: ")


def test_a_heavy_run_never_waits_for_light_holders(lane):
    lanes, _ = lane
    teach(lanes)
    assert lanes.grant(booked(lanes, FILE, 1).id, slots=1, room=ROOM)
    assert lanes.grant(booked(lanes, MID, 2).id, slots=1, room=ROOM)
    assert lanes.grant(booked(lanes, RECEIPT, 3).id, slots=1, room=ROOM).path == "fifo"


def test_a_second_heavy_run_still_waits_at_capacity_one(lane):
    lanes, _ = lane
    teach(lanes)
    assert lanes.grant(booked(lanes, RECEIPT, 1).id, slots=1, room=ROOM)
    assert lanes.grant(booked(lanes, RECEIPT, 2).id, slots=1, room=ROOM) is None


def test_a_light_run_waits_when_cores_do_not_fit(lane):
    lanes, _ = lane
    teach(lanes)
    measured(lanes, ["exact:wide"], 600.0, 11.0, 1000)
    assert lanes.grant(booked(lanes, ["exact:wide"], 1).id, slots=1, room=ROOM)
    mid = booked(lanes, MID, 2)
    assert lanes.grant(mid.id, slots=1, room=ROOM) is None
    found = lanes.bookings()
    assert book.light_verdict(found, found[mid.id], ROOM, est.history(found, now=NOW()), lanes.live) == \
        "needs 2 cores, 1 left"


def test_a_light_run_waits_when_memory_does_not_fit(lane):
    lanes, _ = lane
    teach(lanes)
    room = book.Room(cpus=8, factor=1.5, memory_left=lambda: 2000)
    assert lanes.grant(booked(lanes, RECEIPT, 1).id, slots=1, room=room)
    item = booked(lanes, MID, 2)
    assert lanes.grant(item.id, slots=1, room=room) is None
    found = lanes.bookings()
    assert book.light_verdict(found, found[item.id], room, est.history(found, now=NOW()), lanes.live) == \
        "needs 400 MB, 300 left"


def test_light_runs_go_by_arrival_among_themselves(lane):
    lanes, _ = lane
    teach(lanes)
    room = book.Room(cpus=8, factor=1.5, memory_left=lambda: 4000)
    assert lanes.grant(booked(lanes, RECEIPT, 0).id, slots=1, room=room)   # the place is taken: no count to fall to
    big = booked(lanes, BIG, 1)
    small = booked(lanes, FILE, 2)
    assert lanes.grant(big.id, slots=1, room=room) is None   # 5000 MB of 2300 left
    assert lanes.grant(small.id, slots=1, room=room) is None
    found = lanes.bookings()
    assert book.light_verdict(found, found[small.id], room, est.history(found, now=NOW()), lanes.live) == \
        f"behind light {big.id}"


def test_a_light_waiter_is_not_ahead_of_a_heavy_one(lane):
    lanes, _ = lane
    teach(lanes)
    room = book.Room(cpus=8, factor=1.5, memory_left=lambda: 4000)
    holder = booked(lanes, RECEIPT, 0)
    assert lanes.grant(holder.id, slots=1, room=room)
    assert lanes.grant(booked(lanes, BIG, 1).id, slots=1, room=room) is None
    heavy = booked(lanes, RECEIPT, 2)
    lanes.release(holder.id)
    assert lanes.grant(heavy.id, slots=1, room=room).path == "fifo"   # the light one waiting first is not ahead


def test_no_light_admission_while_an_older_flotillas_waiter_is_live(lane, tmp_path):
    lanes, _ = lane
    teach(lanes)
    _line(lanes.store, booking="b900", state="waiting", who="old seat", note="", pid=77, mark="m", rule=2)
    item = booked(lanes, FILE, 2)
    assert lanes.grant(item.id, slots=1, room=ROOM) is None
    found = lanes.bookings()
    assert book.light_verdict(found, found[item.id], ROOM, est.history(found, now=NOW()), lanes.live) == \
        "a waiter of an older flotilla is live"


def test_unreadable_memory_admits_no_light_run(lane):
    lanes, _ = lane
    teach(lanes)
    room = book.Room(cpus=8, factor=1.5, memory_left=lambda: None)
    assert lanes.grant(booked(lanes, RECEIPT, 1).id, slots=1, room=room)
    assert lanes.grant(booked(lanes, FILE, 2).id, slots=1, room=room) is None


def test_without_a_room_the_grant_is_todays(lane):
    lanes, _ = lane
    teach(lanes)
    assert lanes.grant(booked(lanes, RECEIPT, 1).id, slots=1)
    assert lanes.grant(booked(lanes, FILE, 2).id, slots=1) is None


@pytest.mark.parametrize("foreign, fits", [(1, True), (2, False)])
def test_a_foreign_computing_run_takes_four_cores_of_the_budget(lane, foreign, fits):
    lanes, _ = lane
    teach(lanes)
    measured(lanes, ["exact:three"], 600.0, 3.0, 500)
    room = book.Room(cpus=8, factor=1.5, memory_left=lambda: 8000, foreign=foreign)
    assert lanes.grant(booked(lanes, ["exact:three"], 1).id, slots=1, room=room)
    assert bool(lanes.grant(booked(lanes, MID, 2).id, slots=1, room=room)) is fits


def test_an_unmeasured_run_is_heavy(lane):
    lanes, _ = lane
    teach(lanes)
    assert lanes.grant(booked(lanes, RECEIPT, 1).id, slots=1, room=ROOM)
    assert lanes.grant(booked(lanes, ["exact:never-seen"], 2).id, slots=1, room=ROOM) is None



# -- seniority survives an expired wait (lane light runs design, section 3) -------------------------------------------

def clocked(tmp_path):
    moment = {"at": dt.datetime(2026, 11, 4, 10, 0, tzinfo=dt.timezone.utc)}
    return book.Book(LocalLogStore(tmp_path / "clocked"), Procs(), clock=lambda: moment["at"]), moment


def test_an_expired_booking_made_again_keeps_its_place(tmp_path):
    lanes, moment = clocked(tmp_path)
    holder = lanes.enqueue("seat 0", "", pid=1, mark="m")
    assert lanes.grant(holder.id, slots=1)
    first = lanes.enqueue("seat 1", "", pid=2, mark="m", ladder=["exact:suite"])
    later = lanes.enqueue("seat 2", "", pid=3, mark="m", ladder=["exact:other"])
    lanes.expire(first.id, "waited 1800 s")
    moment["at"] += dt.timedelta(minutes=5)
    again = lanes.enqueue("seat 1", "", pid=2, mark="m", ladder=["exact:suite"])
    assert again.place == book.number(first.id)
    assert [item.id for item in lanes.waiters()] == [again.id, later.id]
    lanes.release(holder.id)
    assert lanes.grant(later.id, slots=1) is None and lanes.grant(again.id, slots=1)


@pytest.mark.parametrize("who, ladder, minutes", [("seat 9", ["exact:suite"], 5), ("seat 1", ["exact:else"], 5),
                                                  ("seat 1", ["exact:suite"], 11)])
def test_seniority_needs_the_same_session_signature_and_ten_minutes(tmp_path, who, ladder, minutes):
    lanes, moment = clocked(tmp_path)
    first = lanes.enqueue("seat 1", "", pid=2, mark="m", ladder=["exact:suite"])
    lanes.expire(first.id, "waited")
    moment["at"] += dt.timedelta(minutes=minutes)
    assert lanes.enqueue(who, "", pid=4, mark="m", ladder=ladder).place is None


def test_a_place_is_inherited_once(tmp_path):
    lanes, moment = clocked(tmp_path)
    first = lanes.enqueue("seat 1", "", pid=2, mark="m", ladder=["exact:suite"])
    lanes.expire(first.id, "waited")
    assert lanes.enqueue("seat 1", "", pid=2, mark="m", ladder=["exact:suite"]).place == book.number(first.id)
    assert lanes.enqueue("seat 1", "", pid=5, mark="m", ladder=["exact:suite"]).place is None


@pytest.mark.parametrize("value, kept", [(3, 3), (0, None), (-1, None), ("3", None), (True, None), (2.5, None)])
def test_a_place_folds_only_from_a_whole_number(tmp_path, value, kept):
    lanes, _ = clocked(tmp_path)
    _line(lanes.store, booking="b1", state="waiting", who="x", place=value)
    assert lanes.bookings()["b1"].place == kept



# -- the final review of 0.28.0 ------------------------------------------------------------------------------------

def test_with_an_older_waiter_live_a_light_run_goes_by_count_and_nobody_deadlocks(lane):
    lanes, _ = lane
    teach(lanes)
    room = book.Room(cpus=8, factor=1.5, memory_left=lambda: 4000)
    heavy = booked(lanes, RECEIPT, 1)
    assert lanes.grant(heavy.id, slots=1, room=room)
    light = booked(lanes, BIG, 2)                         # light, but 5000 MB does not fit 4000
    assert lanes.grant(light.id, slots=1, room=room) is None
    _line(lanes.store, booking="b900", state="waiting", who="old seat", note="", pid=77, mark="m", rule=2)
    later = booked(lanes, RECEIPT, 3)
    lanes.release(heavy.id)
    granted = lanes.grant(light.id, slots=1, room=room)   # first in line, counted as before
    assert granted is not None and granted.path == "fifo"
    assert lanes.grant(later.id, slots=2, room=room) is None   # the older waiter came first


def test_with_an_older_waiter_live_light_holders_count_again(lane):
    lanes, _ = lane
    teach(lanes)
    assert lanes.grant(booked(lanes, FILE, 1).id, slots=1, room=ROOM).path.startswith("light")
    first = booked(lanes, RECEIPT, 2)
    _line(lanes.store, booking="b900", state="waiting", who="old seat", note="", pid=77, mark="m", rule=2)
    assert lanes.grant(first.id, slots=1, room=ROOM) is None   # the old seat counts the light holder: so do we


def test_a_place_is_kept_across_a_chain_of_expired_waits(tmp_path):
    lanes, moment = clocked(tmp_path)
    first = lanes.enqueue("seat 1", "", pid=2, mark="m", ladder=["exact:suite"])
    other = lanes.enqueue("seat 2", "", pid=3, mark="m", ladder=["exact:other"])
    current = first
    for _ in range(4):
        lanes.expire(current.id, "waited")
        moment["at"] += dt.timedelta(minutes=1)
        current = lanes.enqueue("seat 1", "", pid=2, mark="m", ladder=["exact:suite"])
        assert current.place == book.number(first.id)
    assert [item.id for item in lanes.waiters()] == [current.id, other.id]


def test_a_never_measured_heavy_holder_counts_as_the_whole_machine(lane):
    lanes, _ = lane
    teach(lanes)
    assert lanes.grant(booked(lanes, ["exact:never-seen"], 1).id, slots=1, room=ROOM)
    assert lanes.grant(booked(lanes, FILE, 2).id, slots=1, room=ROOM)          # 8 + 1 of 12
    assert lanes.grant(booked(lanes, MID, 3).id, slots=1, room=ROOM)           # 9 + 2 of 12
    assert lanes.grant(booked(lanes, MID, 4).id, slots=1, room=ROOM) is None   # 11 + 2 of 12


def test_without_a_room_light_holders_count_as_before(lane):
    lanes, _ = lane
    teach(lanes)
    assert lanes.grant(booked(lanes, FILE, 1).id, slots=1, room=ROOM).path.startswith("light")
    assert lanes.grant(booked(lanes, RECEIPT, 2).id, slots=1) is None


def test_the_fair_share_does_not_count_a_projects_light_holders(tmp_path):
    lanes = book.Book(LocalLogStore(tmp_path / "fair"), Procs())
    teach(lanes)
    a_light = lanes.enqueue("a 1", "", pid=1, mark="m", ladder=FILE, command="x", project="A")
    assert lanes.grant(a_light.id, slots=1, room=ROOM).path.startswith("light")
    a = lanes.enqueue("a 2", "", pid=3, mark="m", ladder=RECEIPT, command="x", project="A")
    lanes.enqueue("b 1", "", pid=2, mark="m", ladder=RECEIPT, command="x", project="B")
    assert book.fair_choice(lanes.bookings(), lanes.live, NOW()).id == a.id   # a light run is no turn


def test_heavy_starts_with_more_holders_than_slots_wait_for_enough_of_them_to_end():
    now = NOW()
    sample = est.Sample(600.0, 1000, 4.0, 0.5, "green", "")
    hist = {"exact:r": [sample] * 3}
    holders = [book.Booking(id=f"b{i}", state=book.HELD, ladder=["exact:r"], path="fifo", rule=3,
                            since=(now - dt.timedelta(minutes=minutes)).isoformat())
               for i, minutes in ((1, 8), (2, 4), (3, 1))]
    waiter = book.Booking(id="b4", state=book.WAITING, ladder=["exact:r"], rule=3)
    found = {item.id: item for item in (*holders, waiter)}
    starts = book.heavy_starts(found, hist, slots=1, now=now, live=lambda item: True, ceiling=1800)
    assert abs((starts["b4"] - now).total_seconds() - 9 * 60) < 2   # all three must end at capacity 1


# -- 0.28.1: a light run the room refuses still goes by count when a place is free ---------------------------------

@pytest.mark.parametrize("memory", [lambda: None, lambda: 250])
def test_a_light_run_on_an_empty_lane_goes_by_count_when_the_room_refuses(lane, memory):
    lanes, _ = lane
    teach(lanes)
    room = book.Room(cpus=8, factor=1.5, memory_left=memory)   # unreadable, or less than its 300 MB
    granted = lanes.grant(booked(lanes, FILE, 1).id, slots=1, room=room)
    assert granted is not None and granted.path == "fifo"


def test_a_light_run_behind_a_big_light_one_is_not_stranded_on_an_empty_lane(lane):
    lanes, _ = lane
    teach(lanes)
    room = book.Room(cpus=8, factor=1.5, memory_left=lambda: 4000)
    big, small = booked(lanes, BIG, 1), booked(lanes, FILE, 2)
    assert lanes.grant(small.id, slots=1, room=room) is None              # the big one came first
    assert lanes.grant(big.id, slots=1, room=room).path == "fifo"          # 5000 MB: by count, as a heavy run would
    assert lanes.grant(small.id, slots=1, room=room) is None              # 0 MB left beside it, place taken


def test_a_light_run_the_room_refuses_still_waits_when_the_place_is_taken(lane):
    lanes, _ = lane
    teach(lanes)
    room = book.Room(cpus=8, factor=1.5, memory_left=lambda: None)
    assert lanes.grant(booked(lanes, RECEIPT, 1).id, slots=1, room=room)
    assert lanes.grant(booked(lanes, FILE, 2).id, slots=1, room=room) is None


def test_a_new_booking_is_rule_5_and_records_when_it_first_asked(lane):
    lanes, _ = lane
    item = lanes.enqueue("main session 1", "suite", pid=1, mark="m", kind="push")
    assert item.rule == 5 and item.kind == "push" and book._when(item.asked) is not None


def test_asked_survives_the_held_record_that_resets_since(lane):
    lanes, _ = lane
    item = lanes.enqueue("a", "", pid=1, mark="m")
    held = lanes.grant(item.id, slots=1)
    assert held.state == book.HELD and held.asked == item.asked


def test_freeze_fields_fold_cleaned():
    found = book.fold([{"at": "2026-11-06T10:00:00+00:00", "booking": "b1", "state": "held", "frozen": "nonsense",
                        "ran_s": -5, "reclaim_mb": "x", "freezes": True, "heavy": "yes", "kind": "boss",
                        "order_by": "b2\x1b[2J", "peak_cores": 9999, "group": -1, "asked": "yesterday",
                        "freeze_kind": "kill", "pinned": 1, "unanswered": "true", "held_mb": -1,
                        "frozen_s": float("nan"), "unit": "u\x1b[2J"}])["b1"]
    assert (found.frozen, found.ran_s, found.reclaim_mb, found.freezes, found.heavy, found.kind) == \
        ("", None, None, 0, False, "run")
    assert "\x1b" not in found.order_by and found.peak_cores is None and found.group is None
    assert (found.asked, found.freeze_kind, found.pinned, found.unanswered, found.held_mb, found.frozen_s) == \
        ("", "", False, False, None, None)
    assert "\x1b" not in found.unit


def test_freeze_fields_fold_as_written():
    found = book.fold([{"at": "2026-11-06T10:00:00+00:00", "booking": "b1", "state": "held", "frozen": "frozen",
                        "ran_s": 300.5, "reclaim_mb": 1128, "freezes": 2, "heavy": True, "kind": "review",
                        "order_by": "b2", "peak_cores": 6.5, "group": 4242, "freeze_kind": "scope",
                        "unit": "flotilla-lane-b1-9-1.scope", "held_mb": 2048, "frozen_s": 40.0,
                        "started": "2026-11-06T09:55:00+00:00", "unanswered": True, "pinned": True}])["b1"]
    assert (found.frozen, found.ran_s, found.reclaim_mb, found.freezes, found.heavy, found.kind, found.order_by,
            found.peak_cores, found.group, found.freeze_kind, found.unit, found.held_mb, found.frozen_s,
            found.started, found.unanswered, found.pinned) == \
        ("frozen", 300.5, 1128, 2, True, "review", "b2", 6.5, 4242, "scope", "flotilla-lane-b1-9-1.scope", 2048, 40.0,
         "2026-11-06T09:55:00+00:00", True, True)


def test_a_frozen_booking_is_still_a_holder_to_an_older_fold():
    found = book.fold([{"at": "2026-11-06T10:00:00+00:00", "booking": "b1", "state": "held", "frozen": "frozen"}])
    assert [item.id for item in book.Book.holders(None, found)] == ["b1"]



def test_a_line_with_no_state_is_skipped_not_fatal():   # review: one crafted line must not stop every grant
    found = book.fold([{"at": "2026-11-06T10:00:00+00:00", "booking": "b1"},
                       {"at": "2026-11-06T10:00:01+00:00", "booking": "b1", "state": 7},
                       {"at": "2026-11-06T10:00:02+00:00", "booking": "b2", "state": "held"}])
    assert list(found) == ["b2"]


# --- rule 5: the lane daemon (lane daemon design, 2026-11-07) ---

from types import SimpleNamespace   # noqa: E402


def raw(lanes, **record):
    with lanes.store.transaction(book.KEY) as tx:
        tx.append({"at": lanes._now(), **record})


def actions(**kw):
    base = dict(starts=(), ceilings=(), squeezed=(), restored=(), orders=(), thaws=(), why=(), over_mb=0)
    return SimpleNamespace(**{**base, **kw})


def held(lanes, who="a", pid=11, **kw):
    item = lanes.enqueue(who, "", pid=pid, mark="m", **kw)
    raw(lanes, booking=item.id, state=book.HELD, started=lanes._now())
    return lanes.bookings()[item.id]


def test_rule_five_fields_fold_clean(lane):
    lanes, _ = lane
    raw(lanes, booking="b50", state=book.HELD, who="x", order_cause="nuke", order_pid=True, order_mark=5,
        ceiling_mb=-5, held_freezes=-1, squeezed="yes", held_back="w" * 3000, by_hand="yes", floor_mb="big")
    item = lanes.bookings()["b50"]
    assert (item.order_cause, item.order_pid, item.order_mark, item.ceiling_mb, item.held_freezes, item.squeezed,
            len(item.held_back), item.by_hand, item.floor_mb) == ("", None, "", None, 0, False, 200, False, None)
    raw(lanes, booking="b51", state=book.HELD, who="x", order_pid=1, ceiling_mb=1e12)
    assert (lanes.bookings()["b51"].order_pid, lanes.bookings()["b51"].ceiling_mb) == (None, None)
    raw(lanes, booking="b52", state=book.HELD, who="x", order_cause="over", order_pid=4242, order_mark="m9",
        ceiling_mb=1536, held_freezes=2, squeezed=True, held_back="ci", by_hand=True, floor_mb=2000)
    item = lanes.bookings()["b52"]
    assert (item.order_cause, item.order_pid, item.order_mark, item.ceiling_mb, item.held_freezes, item.squeezed,
            item.held_back, item.by_hand, item.floor_mb) == ("over", 4242, "m9", 1536, 2, True, "ci", True, 2000)


def test_a_0_30_record_folds_as_before(lane):
    lanes, _ = lane
    raw(lanes, booking="b60", state=book.HELD, who="old", pid=60, mark="m", rule=4, frozen="frozen",
        order_by="b61")
    item = lanes.bookings()["b60"]
    assert item.rule == 4 and item.frozen == "frozen" and item.order_by == "b61"
    assert (item.order_cause, item.order_pid, item.ceiling_mb, item.squeezed, item.held_freezes) == \
        ("", None, None, False, 0)


def test_enqueue_records_the_floor_and_by_hand(lane):
    lanes, _ = lane
    item = lanes.enqueue("a", "", pid=11, mark="m", floor_mb=2000, by_hand=True)
    assert (item.floor_mb, item.by_hand, item.rule) == (2000, True, 5)


def test_scoped_writes_the_unit_of_a_held_booking_only(lane):
    lanes, _ = lane
    item = held(lanes)
    unit = f"flotilla-lane-{item.id}-11-1.scope"
    assert lanes.scoped(item.id, unit).unit == unit
    assert lanes.scoped(item.id, "../../etc") is None and lanes.bookings()[item.id].unit == unit
    lanes.release(item.id)
    assert lanes.scoped(item.id, unit) is None


def test_hold_back_writes_only_a_change(lane, tmp_path):
    lanes, _ = lane
    item = lanes.enqueue("a", "", pid=11, mark="m")
    size = (tmp_path / "lane.jsonl").stat().st_size
    lanes.hold_back(item.id, "ci: a run on this machine")
    once = (tmp_path / "lane.jsonl").stat().st_size
    lanes.hold_back(item.id, "ci: a run on this machine")
    assert once > size and (tmp_path / "lane.jsonl").stat().st_size == once
    assert lanes.bookings()[item.id].held_back == "ci: a run on this machine"
    lanes.hold_back(item.id, "")
    assert lanes.bookings()[item.id].held_back == ""


def test_apply_starts_orders_thaws_and_ceilings_in_one_transaction(lane):
    lanes, _ = lane
    waiter = lanes.enqueue("w", "", pid=21, mark="m")
    victim = held(lanes, "v", pid=22)
    frozen = held(lanes, "f", pid=23)
    raw(lanes, booking=frozen.id, state=book.HELD, frozen="frozen", frozen_at=lanes._now())
    capped = held(lanes, "c", pid=24)
    done = lanes.apply(actions(starts=((waiter.id, True, 0.42),), orders=((victim.id, 700, "over", ""),),
                               thaws=(frozen.id,), ceilings=((capped.id, 1536),), squeezed=(capped.id,)),
                       (4242, "m9"))
    assert sorted(done) == sorted([waiter.id, victim.id, frozen.id, capped.id])
    found = lanes.bookings()
    w = found[waiter.id]
    assert (w.state, w.path, w.heavy, bool(w.started), w.pid) == (book.HELD, "value: P 0.42, heavy", True, True, 21)
    v = found[victim.id]
    assert (v.frozen, v.order_by, v.order_cause, v.order_pid, v.order_mark, v.reclaim_mb, bool(v.ordered_at)) == \
        ("ordered", "", "over", 4242, "m9", 700, True)
    assert found[frozen.id].frozen == "" and found[frozen.id].frozen_s is not None
    assert (found[capped.id].ceiling_mb, found[capped.id].squeezed) == (1536, True)
    lanes.apply(actions(ceilings=((capped.id, None),), restored=(capped.id,)), (4242, "m9"))
    assert (lanes.bookings()[capped.id].ceiling_mb, lanes.bookings()[capped.id].squeezed) == (None, False)


def test_apply_skips_what_no_longer_applies(lane):
    lanes, _ = lane
    gone = lanes.enqueue("g", "", pid=31, mark="m")
    lanes.leave(gone.id)
    frozen = held(lanes, "f", pid=32)
    raw(lanes, booking=frozen.id, state=book.HELD, frozen="frozen", frozen_at=lanes._now())
    raw(lanes, booking="b99", state=book.WAITING, who="old", pid=33, mark="m", rule=4)
    gated = lanes.enqueue("h", "", pid=34, mark="m")
    lanes.hold_back(gated.id, "ci")
    done = lanes.apply(actions(starts=((gone.id, False, 0.1), ("b99", False, 0.1), (gated.id, False, 0.1)),
                               orders=((frozen.id, 0, "over", ""),)), (4242, "m9"))
    assert done == []
    found = lanes.bookings()
    assert found[gone.id].state == book.LEFT and found["b99"].state == book.WAITING
    assert found[gated.id].state == book.WAITING and found[frozen.id].frozen == "frozen"


def test_apply_admits_by_hand_as_nobodys(lane):
    lanes, _ = lane
    item = lanes.enqueue("person", "", pid=41, mark="m", by_hand=True)
    lanes.apply(actions(starts=((item.id, True, 0.3),)), (4242, "m9"))
    found = lanes.bookings()[item.id]
    assert (found.state, found.pid, found.mark) == (book.HELD, None, "")


def test_fallback_admits_only_into_an_empty_lane(lane):
    lanes, _ = lane
    holder = held(lanes, "h", pid=51)
    first = lanes.enqueue("a", "", pid=52, mark="m")
    second = lanes.enqueue("b", "", pid=53, mark="m")
    assert lanes.grant(first.id, slots=5, fallback=True) is None
    lanes.release(holder.id)
    assert lanes.grant(second.id, slots=5, fallback=True) is None
    got = lanes.grant(first.id, slots=5, fallback=True)
    assert got.state == book.HELD and got.path == "fallback: empty lane"
    assert lanes.grant(second.id, slots=5, fallback=True) is None


def test_frozen_counts_daemon_freezes(lane):
    lanes, _ = lane
    over = held(lanes, "o", pid=61)
    raw(lanes, booking=over.id, state=book.HELD, frozen="ordered", order_cause="over", ordered_at=lanes._now())
    item = lanes.frozen(over.id, freeze_kind="scope", unit="", group=None, held_mb=100)
    assert (item.held_freezes, item.freezes) == (1, 1)
    waits = held(lanes, "w", pid=62)
    raw(lanes, booking=waits.id, state=book.HELD, frozen="ordered", order_cause="waiter", ordered_at=lanes._now())
    item = lanes.frozen(waits.id, freeze_kind="scope", unit="", group=None, held_mb=100)
    assert (item.held_freezes, item.freezes) == (0, 1)


def test_apply_gives_a_run_it_starts_its_ceiling(lane):
    lanes, _ = lane
    waiter = lanes.enqueue("w", "", pid=21, mark="m")
    lanes.apply(actions(starts=((waiter.id, False, 0.3),), ceilings=((waiter.id, 1280),)), (4242, "m9"))
    found = lanes.bookings()[waiter.id]
    assert (found.state, found.ceiling_mb) == (book.HELD, 1280)



def test_apply_seals_its_orders(lane):   # security review of c0b73f9: a pid and a mark are no secret
    from flotilla.lane import daemon
    lanes, _ = lane
    victim = held(lanes, "v", pid=22)
    key = b"k" * 32
    lanes.apply(actions(orders=((victim.id, 700, "over", ""),)), (4242, "m9", key))
    item = lanes.bookings()[victim.id]
    assert item.order_sig == daemon.seal(key, item.id, "over", item.ordered_at, "", 4242, "m9", 700)
    assert len(item.order_sig) == 64


def test_a_seal_folds_clean(lane):
    lanes, _ = lane
    raw(lanes, booking="b70", state=book.HELD, who="x", order_sig="not hex")
    raw(lanes, booking="b71", state=book.HELD, who="x", order_sig="ab" * 32)
    assert lanes.bookings()["b70"].order_sig == "" and lanes.bookings()["b71"].order_sig == "ab" * 32
