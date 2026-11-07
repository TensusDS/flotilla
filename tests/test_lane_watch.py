import io
"""The holder's watch: it freezes its command on an order, thaws it through the queue, lets a signal stop go, never
leaves its command frozen when it fails, and beats while it lives (lane shared machine design, 4.1-4.3)."""

import dataclasses
import datetime as dt
import os
import signal
import subprocess
import sys
import threading

import pytest

from flotilla.core.storage import LocalLogStore
from flotilla.lane import acquire, admission, book, freeze, run, watch
from flotilla.onboard import firstrun

T0 = dt.datetime(2026, 11, 6, 10, 0, tzinfo=dt.timezone.utc)


class Procs:
    def alive(self, pid, mark):
        return True


class FakeSlot:
    def __init__(self, kind=freeze.SCOPE):
        self.kind, self.calls, self._frozen, self.seconds, self.unit = kind, [], False, 0.0, ""
        self.watcher = None

    def freeze(self, reclaim_mb):
        self.calls.append(("freeze", reclaim_mb))
        self._frozen = True
        return {"freeze_kind": self.kind, "unit": "flotilla-lane-b1-11-1.scope" if self.kind == freeze.SCOPE else "", "group": 11,
                "held_mb": 5000}

    def thaw(self):
        self.calls.append(("thaw",))
        self._frozen = False

    def frozen(self):
        return self._frozen

    def frozen_seconds(self):
        return self.seconds

    done_freezing = False


def value(available=9000, tree=5000):
    return admission.Value(cpus=8, cpu_factor=1.5, light_reserve=0.2, immortal_s=900.0, max_runs=10,
                           freeze=freeze.SCOPE, available_mb=lambda: available, floor_mb=0,
                           swap_mb=lambda: (8000, 8000), tree_mb=lambda pid: tree if pid == 11 else 100,
                           beat_age_s=lambda booking: 0.0)


DAEMON = (4242, "m9")
KEY = b"s" * 32


class Daemon:
    """The lane daemon as a wrapper sees it: its heartbeat, and its lock held (lane daemon design, 4.8)."""

    def __init__(self, root):
        import fcntl
        import json
        import time
        from flotilla.lane import daemon
        self.root = root
        (root / daemon.BEAT).write_text(json.dumps({
            "pid": DAEMON[0], "mark": DAEMON[1], "copy": "", "release": "0.31.0", "protocol": daemon.PROTOCOL,
            "tick": time.time(), "beat": time.time(), "why": {}, "groups": {}, "over": {}}))
        (root / daemon.KEY).write_bytes(KEY)
        os.chmod(root / daemon.KEY, 0o600)
        self.fd = os.open(root / daemon.LOCK, os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(self.fd, fcntl.LOCK_EX)

    def gone(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


def sign(lanes, booking, cause="waiter", by=DAEMON, key=KEY):
    """The daemon's signature and seal on the order a booking carries now."""
    from flotilla.lane import daemon
    item = lanes.bookings()[booking]
    seal = daemon.seal(key, booking, cause, item.ordered_at, item.order_by, by[0], by[1], item.reclaim_mb or 0)
    with lanes.store.transaction(book.KEY) as tx:
        tx.append({"at": lanes._now(), "booking": booking, "state": item.state, "order_cause": cause,
                   "order_pid": by[0], "order_mark": by[1], "order_sig": seal})


def order(lanes, booking, cause, by=DAEMON, waiter="", key=KEY):
    """An order as the daemon writes it (Book.apply)."""
    from flotilla.lane import daemon
    at = lanes._now()
    with lanes.store.transaction(book.KEY) as tx:
        tx.append({"at": at, "booking": booking, "state": book.HELD, "frozen": "ordered",
                   "order_by": waiter, "order_cause": cause, "order_pid": by[0], "order_mark": by[1],
                   "ordered_at": at, "reclaim_mb": 700,
                   "order_sig": daemon.seal(key, booking, cause, at, waiter, by[0], by[1], 700)})


@pytest.fixture()
def lane(tmp_path):
    moment = {"at": T0}
    lanes = book.Book(LocalLogStore(tmp_path), Procs(), clock=lambda: moment["at"])

    def later(seconds):
        moment["at"] += dt.timedelta(seconds=seconds)
    first = lanes.enqueue("main session 1", "suite", pid=11, mark="m")
    lanes.grant(first.id, slots=1, value=value())
    later(120)
    waiter = lanes.enqueue("main session 2", "receipt", pid=12, mark="m", kind="push")
    assert lanes.grant(waiter.id, slots=1, value=value(available=300)) is None   # orders first frozen
    assert lanes.bookings()[first.id].frozen == "ordered"
    sign(lanes, first.id)
    lanes.daemon_double = alive = Daemon(tmp_path)
    yield lanes, first.id, waiter.id, later, tmp_path
    alive.gone()


def daemon_thaws(lanes, booking):
    """The daemon's thaw, as Book.apply writes it."""
    with lanes.store.transaction(book.KEY) as tx:
        tx.append({"at": lanes._now(), "booking": booking, "state": book.HELD, "frozen": "", "started": lanes._now()})


def test_the_watch_freezes_its_command_on_an_order(lane):
    lanes, mine, _waiter, _later, _root = lane
    s = FakeSlot()
    w = watch.Watch(lanes, mine, s, lambda reading: value(available=300), swap=lambda: (8000, 8000))
    assert w.step() == "froze"
    found = lanes.bookings()[mine]
    assert s.calls == [("freeze", found.reclaim_mb)] and found.frozen == "frozen" and found.unit == "flotilla-lane-b1-11-1.scope"


def test_the_watch_thaws_when_the_daemon_lets_it(lane):   # rule 5: the daemon thaws, not the watch
    lanes, mine, waiter, later, _root = lane
    s = FakeSlot()
    w = watch.Watch(lanes, mine, s, lambda reading: value(available=300))
    w.step()
    assert w.step() == ""   # the daemon has not thawed it
    lanes.leave(waiter)
    later(10)
    daemon_thaws(lanes, mine)
    assert w.step() == "thawed" and s.calls[-1] == ("thaw",) and lanes.bookings()[mine].frozen == ""


def test_a_signal_stop_is_let_go_when_its_waiter_never_starts(lane):   # spec 4.2, macOS
    lanes, mine, waiter, later, _root = lane
    s = FakeSlot(freeze.SIGNAL)
    tick = {"now": 0.0}
    w = watch.Watch(lanes, mine, s, lambda reading: value(available=300), clock=lambda: tick["now"], let_go_s=120)
    assert w.step() == "froze"
    tick["now"] = 121.0
    assert w.step() == "let go" and not s.frozen()
    assert lanes.bookings()[mine].unanswered
    later(5)
    lanes.grant(waiter, slots=1, value=value(available=300))
    assert lanes.bookings()[mine].frozen == ""   # no stop-start loop: it is never ordered again


def test_a_watch_that_fails_thaws_its_command_and_freezes_no_more(lane, monkeypatch):   # review focus 3
    lanes, mine, _waiter, _later, _root = lane
    s = FakeSlot()
    w = watch.Watch(lanes, mine, s, lambda reading: value(available=300), sleep=lambda seconds: None)
    w.step()

    def broken(*a, **kw):
        raise RuntimeError("journal unreadable")
    monkeypatch.setattr(lanes, "bookings", broken)
    w.start()
    w.join(timeout=5)
    assert not w.alive() and not s.frozen()
    monkeypatch.undo()
    found = lanes.bookings()[mine]
    assert found.unanswered and found.frozen == ""


def test_the_watch_beats_on_every_look(lane):
    lanes, mine, _waiter, _later, root = lane
    watch.Watch(lanes, mine, FakeSlot(), lambda reading: value(available=300)).step()
    assert admission.beat_age(root, mine) < 1


def test_the_watch_sets_and_clears_the_slots_watcher(lane):
    lanes, mine, _waiter, _later, root = lane
    s = FakeSlot()
    w = watch.Watch(lanes, mine, s, lambda reading: value(available=300))
    w.start()
    assert s.watcher is w.thread
    w.stop()
    assert s.watcher is None and admission.beat_age(root, mine) is None


class TimedSlot(FakeSlot):
    """Frozen ten seconds, as a holder frozen for a while would report it."""

    def __init__(self):
        super().__init__(freeze.SIGNAL)
        self.reads = 0

    def contain(self, argv, *, booking):
        return list(argv)

    def attach(self, pid):
        pass

    def detach(self):
        pass

    def guard(self):
        return False

    def wait_thawed(self, sleep=None):
        pass

    def frozen_seconds(self):
        self.reads += 1
        return 0.0 if self.reads == 1 else 10.0


def test_the_ceiling_does_not_count_frozen_time(monkeypatch):
    s = TimedSlot()
    monkeypatch.setattr(freeze, "slot", lambda: s)
    monkeypatch.setenv(acquire.ENV, "b1")
    result = run.execute([sys.executable, "-c", "import time; time.sleep(1.5)"], ceiling=1.0, out=open(os.devnull, "w"))
    assert not result.at_ceiling and result.verdict == "green" and result.frozen_s == 10.0


def test_a_run_outside_a_booking_has_no_slot(monkeypatch):
    monkeypatch.delenv(acquire.ENV, raising=False)
    monkeypatch.setattr(freeze, "slot", lambda: pytest.fail("no booking, no slot"))
    assert run.execute([sys.executable, "-c", "pass"], out=open(os.devnull, "w")).verdict == "green"


def test_a_tier_does_not_start_while_its_holder_is_frozen(monkeypatch, tmp_path):
    s = freeze.Slot(freeze.SIGNAL)
    s.freeze(0)
    monkeypatch.setattr(freeze, "slot", lambda: s)
    monkeypatch.setenv(acquire.ENV, "b1")
    started = []
    real = subprocess.Popen

    def popen(argv, *a, **kw):
        if argv[:1] == ["/bin/sh"]:   # the tier itself, not a `ps` the memory sampler runs where there is no /proc
            started.append(s.frozen())
        return real(argv, *a, **kw)
    monkeypatch.setattr(firstrun.subprocess, "Popen", popen)
    threading.Timer(0.3, s.thaw).start()
    tier = firstrun.run_tier("unit", "true", tmp_path, timeout=30)
    assert tier.status == "green" and started == [False]


def test_a_tier_timeout_does_not_count_frozen_time(monkeypatch, tmp_path):
    s = TimedSlot()
    monkeypatch.setattr(freeze, "slot", lambda: s)
    monkeypatch.setenv(acquire.ENV, "b1")
    tier = firstrun.run_tier("unit", f"{sys.executable} -c 'import time; time.sleep(1.5)'", tmp_path, timeout=1.0)
    assert tier.status == "green" and tier.frozen_s == 10.0


def test_a_red_run_after_a_freeze_says_so():
    from flotilla.lane import commands
    red = run.RunResult(1, "red", "2 failed", None, frozen_s=180.0, freezes=1)
    assert commands.verdict_line(red) == "lane run: red - 2 failed (red after a freeze of 3 min)"
    assert commands.verdict_line(run.RunResult(0, "green", "ok", None, frozen_s=180.0)) == "lane run: green - ok"
    assert commands.verdict_line(run.RunResult(1, "red", "x", None)) == "lane run: red - x"


def test_held_by_value_starts_a_watch_and_stops_it(lane, monkeypatch):
    lanes, _mine, _waiter, _later, root = lane
    started = []

    class Seen(watch.Watch):
        def start(self):
            started.append(self.booking_id)
            super().start()
    monkeypatch.setattr(watch, "Watch", Seen)
    monkeypatch.setattr(freeze, "slot", lambda: FakeSlot())

    class Table:
        def start_mark(self, pid):
            return "m"

        def ancestors(self, pid):
            return []
    from flotilla.lane.machine import Answer, Reading
    quiet = lambda: Reading([Answer("foreign run", False, "none"), Answer("ci", False, "none")], [], [])  # noqa: E731
    with acquire.held(lanes, quiet, who="main session 3", note="x", capacity=1, wait=0, table=Table(),
                      value=lambda r: value(available=9000, tree=100)) as grant:
        assert grant.booking.path.startswith("value") and started == [grant.booking.id]
    assert admission.beat_age(root, grant.booking.id) is None



class BrokenScope(freeze.Slot):
    """A slot whose scope cannot be made: systemd-run says so and exits 1 without running the command."""

    def __init__(self):
        super().__init__(freeze.SCOPE)

    def contain(self, argv, *, booking):
        if self.kind != freeze.SCOPE:
            return list(argv)
        self.unit = "flotilla-lane-b1-1-1.scope"
        return ["/bin/sh", "-c", "echo 'Failed to start transient scope unit: Unit flotilla-lane-b1-1-1.scope was "
                "already loaded or has a fragment file.' >&2; exit 1"]


def test_a_run_whose_scope_cannot_be_made_runs_without_one(monkeypatch):   # review focus 1
    s = BrokenScope()
    monkeypatch.setattr(freeze, "slot", lambda: s)
    monkeypatch.setenv(acquire.ENV, "b1")
    out = io.StringIO()
    result = run.execute([sys.executable, "-c", "print('ran')"], out=out)
    assert result.verdict == "green" and "ran" in out.getvalue() and s.kind == freeze.SIGNAL
    assert "without a scope" in out.getvalue()


def test_a_tier_whose_scope_cannot_be_made_runs_without_one(monkeypatch, tmp_path):
    s = BrokenScope()
    monkeypatch.setattr(freeze, "slot", lambda: s)
    monkeypatch.setenv(acquire.ENV, "b1")
    tier = firstrun.run_tier("unit", "echo 1 passed", tmp_path, timeout=30)
    assert tier.status == "green" and s.kind == freeze.SIGNAL


def test_a_command_that_fails_on_its_own_is_not_run_twice(monkeypatch, tmp_path):
    s = freeze.Slot(freeze.SIGNAL)
    monkeypatch.setattr(freeze, "slot", lambda: s)
    monkeypatch.setenv(acquire.ENV, "b1")
    marks = tmp_path / "marks"
    tier = firstrun.run_tier("unit", f"echo x >> {marks}; echo 'Failed to start transient scope unit' >&2; exit 1",
                             tmp_path, timeout=30)
    assert tier.status == "red" and marks.read_text().count("x") == 1   # no scope was asked for: nothing to retry



class MadeScope(freeze.Slot):
    """A slot whose scope is made fine: the command runs as asked, under a unit name."""

    def __init__(self):
        super().__init__(freeze.SCOPE)

    def contain(self, argv, *, booking):
        self.unit = "flotilla-lane-b1-1-1.scope"
        return list(argv)


def test_a_command_saying_failed_to_connect_is_not_run_twice(monkeypatch, tmp_path):   # final review I3
    s = MadeScope()
    monkeypatch.setattr(freeze, "slot", lambda: s)
    monkeypatch.setenv(acquire.ENV, "b1")
    marks = tmp_path / "marks"
    tier = firstrun.run_tier("db", f"echo x >> {marks}; echo 'Failed to connect to the database' >&2; exit 1",
                             tmp_path, timeout=30)
    assert tier.status == "red" and marks.read_text().count("x") == 1
    result = run.execute(["/bin/sh", "-c", f"echo y >> {marks}; echo 'Failed to connect to the database'; exit 1"],
                         out=io.StringIO())
    assert result.verdict == "red" and marks.read_text().count("y") == 1


def test_a_tier_stopped_while_frozen_is_thawed_before_it_is_killed(monkeypatch, tmp_path):   # final review I6
    s = freeze.Slot(freeze.SIGNAL)
    monkeypatch.setattr(freeze, "slot", lambda: s)
    monkeypatch.setenv(acquire.ENV, "b1")
    order = []
    real_kill = firstrun.os.killpg

    def killpg(pid, number):
        if number == signal.SIGKILL:   # the stop itself, not the freeze's own SIGSTOP
            order.append(("kill", s.frozen()))
        real_kill(pid, number)
    monkeypatch.setattr(firstrun.os, "killpg", killpg)
    real_communicate = subprocess.Popen.communicate

    def communicate(self, input=None, timeout=None):
        if self.args[:1] != ["/bin/sh"]:   # a `ps` the sampler runs (macOS): left alone
            return real_communicate(self, input, timeout=timeout)
        s.freeze(0)   # frozen meanwhile, then the session stops
        raise KeyboardInterrupt
    monkeypatch.setattr(subprocess.Popen, "communicate", communicate)
    with pytest.raises(KeyboardInterrupt):
        firstrun.run_tier("slow", "sleep 30", tmp_path, timeout=60)
    monkeypatch.setattr(subprocess.Popen, "communicate", real_communicate)
    assert order == [("kill", False)]


def test_an_order_is_clamped_to_the_swap_budget(lane):   # final review I7: the holder trusts no figure blindly
    lanes, mine, _waiter, _later, _root = lane
    with lanes.store.transaction(book.KEY) as tx:
        tx.append({"at": lanes._now(), "booking": mine, "state": book.HELD, "reclaim_mb": 9_000_000})
    sign(lanes, mine)   # the daemon's own order asking too much: the reclaim is sealed now, and still clamped
    s = FakeSlot()
    w = watch.Watch(lanes, mine, s, lambda reading: value(available=300), swap=lambda: (8000, 8000))
    assert w.step() == "froze" and s.calls == [("freeze", 6400)]   # 80 % of 8000, none used


def test_a_watch_thaws_when_its_frozen_record_is_refused(lane, monkeypatch):
    lanes, mine, _waiter, _later, _root = lane
    s = FakeSlot()
    monkeypatch.setattr(lanes, "frozen", lambda *a, **kw: None)   # released meanwhile
    w = watch.Watch(lanes, mine, s, lambda reading: value(available=300))
    assert w.step() == "" and not s.frozen()



def _order(lanes, mine, **fields):
    with lanes.store.transaction(book.KEY) as tx:
        tx.append({"at": lanes._now(), "booking": mine, "state": book.HELD, "frozen": "ordered", **fields})


def test_an_order_from_no_waiting_run_is_refused(lane):   # security review, 0.30.1: a crafted order
    lanes, mine, waiter, _later, _root = lane
    lanes.leave(waiter)
    s = FakeSlot()
    w = watch.Watch(lanes, mine, s, lambda reading: value(available=300))
    assert w.step() == "refused" and s.calls == [] and lanes.bookings()[mine].frozen == ""


def test_an_order_naming_a_stranger_is_refused(lane):
    lanes, mine, _waiter, _later, _root = lane
    _order(lanes, mine, order_by="b999", ordered_at=lanes._now())
    s = FakeSlot()
    assert watch.Watch(lanes, mine, s, lambda reading: value(available=300)).step() == "refused" and s.calls == []


def test_an_order_from_a_lower_waiter_is_refused(lane):
    lanes, mine, _waiter, _later, _root = lane
    low = lanes.enqueue("main session 3", "x", pid=13, mark="m", kind="run")
    _order(lanes, mine, order_by=low.id, ordered_at=lanes._now())
    s = FakeSlot()
    assert watch.Watch(lanes, mine, s, lambda reading: value(available=300)).step() == "refused" and s.calls == []


def test_an_order_to_an_immortal_run_is_refused(lane):
    lanes, mine, waiter, later, _root = lane
    later(900)   # it has run fifteen minutes since: immortal
    _order(lanes, mine, order_by=waiter, ordered_at=lanes._now())   # a fresh order all the same
    s = FakeSlot()
    assert watch.Watch(lanes, mine, s, lambda reading: value(available=300)).step() == "refused" and s.calls == []


def test_a_stale_order_is_refused(lane):
    lanes, mine, _waiter, later, _root = lane
    later(admission.ANSWER_S + 1)
    s = FakeSlot()
    assert watch.Watch(lanes, mine, s, lambda reading: value(available=300)).step() == "refused" and s.calls == []



def test_an_order_from_a_waiter_with_no_process_is_refused(lane):   # review of 0.30.1, I1: a forged waiter
    lanes, mine, _waiter, _later, _root = lane
    with lanes.store.transaction(book.KEY) as tx:
        tx.append({"at": lanes._now(), "booking": "b9999", "state": book.WAITING, "rule": 4, "kind": "push",
                   "asked": "2026-11-06T09:00:00+00:00"})
    _order(lanes, mine, order_by="b9999", ordered_at=lanes._now())
    s = FakeSlot()
    assert watch.Watch(lanes, mine, s, lambda reading: value(available=300)).step() == "refused" and s.calls == []


def test_a_true_order_is_obeyed_whatever_the_holders_own_reading_of_memory(lane):   # review of ed148e3, 1
    # the holder reads the machine with its own project's floor, so its reading of the waiter's room can differ for
    # good; judging the order by it refused true orders for ever while waiter and holder traded journal lines
    lanes, mine, _waiter, _later, _root = lane
    s = FakeSlot()
    w = watch.Watch(lanes, mine, s, lambda reading: value(available=9000), swap=lambda: (8000, 8000))
    assert w.step() == "froze" and s.calls


def test_a_watch_whose_booking_ended_thaws_its_slot(lane):   # review of 0.30.1, minor: a slot never stays frozen
    lanes, mine, _waiter, _later, _root = lane
    s = FakeSlot()
    w = watch.Watch(lanes, mine, s, lambda reading: value(available=300), swap=lambda: (8000, 8000))
    assert w.step() == "froze"
    lanes.release(mine)
    assert w.step() == "thawed" and not s.frozen()


def test_a_scope_freeze_is_let_go_when_its_waiter_never_starts(lane):   # review of ed148e3, 5: never frozen for good
    lanes, mine, _waiter, _later, _root = lane
    s = FakeSlot(freeze.SCOPE)
    tick = {"now": 0.0}
    w = watch.Watch(lanes, mine, s, lambda reading: value(available=300), clock=lambda: tick["now"], let_go_s=120,
                    swap=lambda: (8000, 8000))
    assert w.step() == "froze"
    tick["now"] = 121.0   # its memory moved to swap two minutes ago, and the waiter still has not started
    assert w.step() == "let go" and not s.frozen() and lanes.bookings()[mine].unanswered



def floored(available=300, floor=0, tree=5000):
    return admission.Value(cpus=8, cpu_factor=1.5, light_reserve=0.2, immortal_s=900.0, max_runs=10,
                           freeze=freeze.SCOPE, available_mb=lambda: available, floor_mb=floor,
                           swap_mb=lambda: (8000, 8000), tree_mb=lambda pid: tree if pid == 11 else 100,
                           beat_age_s=lambda booking: 0.0)


def test_a_true_order_is_obeyed_whatever_the_holders_own_floor(lane):   # review of the follow-up, I1
    # the holder's project keeps a larger floor: by its reading the waiter is heavy, as this run is; the waiter's
    # reading said light. Heaviness is the waiter's to judge - the holder checks only what no reading changes
    lanes, mine, _waiter, _later, _root = lane
    s = FakeSlot()
    w = watch.Watch(lanes, mine, s, lambda reading: floored(available=1800, floor=1700), swap=lambda: (8000, 8000))
    assert w.step() == "froze" and s.calls


def test_the_let_go_does_not_force_a_thaw_beside_a_waiter_that_started(lane, monkeypatch):   # minor 1
    lanes, mine, waiter, _later, _root = lane
    s = FakeSlot(freeze.SIGNAL)
    tick = {"now": 0.0}
    w = watch.Watch(lanes, mine, s, lambda reading: value(available=300), clock=lambda: tick["now"], let_go_s=120)
    assert w.step() == "froze"
    real = lanes.thaw

    def thaw(booking_id, value_, **kw):   # the waiter's grant takes the lock first and starts it
        with lanes.store.transaction(book.KEY) as tx:
            tx.append({"at": lanes._now(), "booking": waiter, "state": book.HELD})
        return real(booking_id, value_, **kw)
    monkeypatch.setattr(lanes, "thaw", thaw)
    tick["now"] = 121.0
    assert w.step() != "let go" and s.frozen()   # back through the queue, not forced in beside the run it froze for



def test_no_freeze_outlasts_its_ceiling_whoever_holds_its_room(lane):   # the person's ruling, 2026-11-06
    # its waiter turned into a take by hand right after ordering (as a forged one can): the let-go never fires, the
    # queue never gives the room back - the ceiling thaws it all the same, and it is never frozen again
    lanes, mine, waiter, _later, _root = lane
    s = FakeSlot()
    tick = {"now": 0.0}
    ceiling = lambda reading: dataclasses.replace(value(available=300), freeze_ceiling_s=600.0)  # noqa: E731
    w = watch.Watch(lanes, mine, s, ceiling, clock=lambda: tick["now"], let_go_s=120, swap=lambda: (8000, 8000))
    assert w.step() == "froze"
    with lanes.store.transaction(book.KEY) as tx:
        tx.append({"at": lanes._now(), "booking": waiter, "state": book.HELD, "pid": None, "mark": ""})
    tick["now"] = 300.0
    assert w.step() == "" and s.frozen()   # not yet: within the ceiling, and its waiter no longer waits
    tick["now"] = 601.0
    assert w.step() == "let go" and not s.frozen() and lanes.bookings()[mine].unanswered



def test_the_ceiling_holds_when_a_crafted_line_turns_the_record_back_to_ordered(lane):   # review, I1
    lanes, mine, waiter, _later, _root = lane
    s = FakeSlot()
    tick = {"now": 0.0}
    ceiling = lambda reading: dataclasses.replace(value(available=300), freeze_ceiling_s=600.0)  # noqa: E731
    w = watch.Watch(lanes, mine, s, ceiling, clock=lambda: tick["now"], let_go_s=120, swap=lambda: (8000, 8000))
    assert w.step() == "froze"
    tick["now"] = 601.0
    _order(lanes, mine, order_by=waiter, ordered_at=lanes._now())   # a crafted line: the record says "ordered" again
    assert w.step() == "let go" and not s.frozen()
    assert lanes.bookings()[mine].unanswered   # and it does not freeze for that order once thawed
    assert w.step() != "froze" and not s.frozen()



def test_the_ceiling_thaws_before_it_writes_the_journal(lane, monkeypatch):   # review of f32d91c, I-A
    lanes, mine, _waiter, _later, _root = lane
    s = FakeSlot()
    tick = {"now": 0.0}
    ceiling = lambda reading: dataclasses.replace(value(available=300), freeze_ceiling_s=600.0)  # noqa: E731
    w = watch.Watch(lanes, mine, s, ceiling, clock=lambda: tick["now"], let_go_s=120, swap=lambda: (8000, 8000))
    assert w.step() == "froze"
    tick["now"] = 601.0

    def locked(*a, **kw):
        raise TimeoutError("the journal's lock is held")
    monkeypatch.setattr(lanes, "thaw", locked)
    monkeypatch.setattr(lanes, "unfreezable", locked)
    with pytest.raises(TimeoutError):
        w.step()
    assert not s.frozen()   # thawed first: the journal could not be written, the command runs all the same


def test_a_run_let_go_is_never_frozen_again_whatever_the_journal_says(lane):   # review of f32d91c, M-1
    lanes, mine, waiter, _later, _root = lane
    s = FakeSlot()
    tick = {"now": 0.0}
    ceiling = lambda reading: dataclasses.replace(value(available=300), freeze_ceiling_s=600.0)  # noqa: E731
    w = watch.Watch(lanes, mine, s, ceiling, clock=lambda: tick["now"], let_go_s=120, swap=lambda: (8000, 8000))
    assert w.step() == "froze"
    tick["now"] = 601.0
    assert w.step() == "let go"
    long_ago = (lanes.clock() - dt.timedelta(seconds=70)).isoformat(timespec="seconds")
    # crafted: the mark lifted, the first minute and the run time set to pass the rules, and a fresh order
    _order(lanes, mine, order_by=waiter, ordered_at=lanes._now(), unanswered=False, started=long_ago, ran_s=10)
    assert w.step() in ("refused", "let go") and not s.frozen()   # the order lapses either way
    assert w.step() != "froze" and [call for call in s.calls if call[0] == "freeze"] == [("freeze", 852)]



def test_the_journal_follows_a_ceiling_the_slot_kept_by_itself(lane):   # review of d4c28c5, I1; security finding
    # the command's own loop reached the ceiling first and thawed with no journal: the record must not go on saying
    # "frozen" while the run computes - other sessions would count its room as free
    lanes, mine, _waiter, _later, _root = lane
    s = FakeSlot()
    w = watch.Watch(lanes, mine, s, lambda reading: value(available=300), swap=lambda: (8000, 8000))
    assert w.step() == "froze"
    s.done_freezing = True   # what Slot.guard does at the ceiling
    s.thaw()
    assert w.step() == "let go"
    found = lanes.bookings()[mine]
    assert found.frozen == "" and found.unanswered


def test_a_new_watch_on_the_same_slot_may_freeze_again(lane):   # review of d4c28c5, minor 2
    lanes, mine, _waiter, _later, _root = lane
    s = FakeSlot()
    s.done_freezing = True   # a booking before this one, in the same process, was let go
    w = watch.Watch(lanes, mine, s, lambda reading: value(available=300), swap=lambda: (8000, 8000))
    w.start()
    try:
        assert s.done_freezing is False
    finally:
        w.stop()



def test_the_watch_marks_its_journal_calls_and_not_its_freeze(lane):   # review of 9d9d244, I1
    lanes, mine, _waiter, _later, _root = lane
    marks = []

    class Marked(FakeSlot):
        journal_since = None

        def freeze(self, reclaim_mb):
            marks.append(self.journal_since)   # inside the freeze: not in a journal call
            return super().freeze(reclaim_mb)
    s = Marked()
    w = watch.Watch(lanes, mine, s, lambda reading: value(available=300), swap=lambda: (8000, 8000))
    w._look()
    assert marks == [None] and s.journal_since is None and s.frozen()


def test_a_record_thawed_through_the_queue_after_a_let_go_is_marked_once(lane):   # review of 9d9d244, minor 1
    lanes, mine, _waiter, _later, _root = lane
    s = FakeSlot()
    s.done_freezing = True   # the slot thawed by itself; the queue then thawed the record without the mark
    w = watch.Watch(lanes, mine, s, lambda reading: value(available=300), swap=lambda: (8000, 8000))
    lanes.refuse(mine)   # the record: no order, no mark
    assert not lanes.bookings()[mine].unanswered
    assert w.step() == "let go" and lanes.bookings()[mine].unanswered
    assert w.step() == ""   # once



def test_a_watch_blocked_in_the_journal_after_its_freeze_is_read_as_stuck(lane, monkeypatch):   # review of 059a5a9
    # the freeze has just been made, and the record write blocks on the journal's lock - held, say, by a process
    # inside the frozen command: the watch must be marked as inside a journal call, so the slot thaws in 15 s
    lanes, mine, _waiter, _later, _root = lane
    tick = {"now": 100.0}
    s = freeze.Slot(freeze.SIGNAL, clock=lambda: tick["now"])
    s.ceiling_s = 600.0
    blocked, release = threading.Event(), threading.Event()
    real = lanes.frozen

    def held_lock(*a, **kw):
        blocked.set()
        release.wait(5)
        return real(*a, **kw)
    monkeypatch.setattr(lanes, "frozen", held_lock)
    w = watch.Watch(lanes, mine, s, lambda reading: value(available=300), swap=lambda: (8000, 8000))
    looking = threading.Thread(target=w._look)
    s.watcher = looking
    looking.start()
    try:
        assert blocked.wait(5)
        assert s.frozen() and s.journal_since == 100.0   # marked, and marked after the freeze began
        tick["now"] = 100.0 + freeze.WATCH_STALE_S + 1
        assert s.guard() and not s.frozen() and s.done_freezing
    finally:
        release.set()
        looking.join(5)


def test_a_mark_from_before_the_freeze_never_counts():   # review of 059a5a9, M1
    tick = {"now": 0.0}
    s = freeze.Slot(freeze.SIGNAL, clock=lambda: tick["now"])
    s.ceiling_s = 600.0
    s.journal_since = 0.0   # a slow look before the freeze left its mark
    tick["now"] = 20.0
    s.freeze(0)             # the freeze in force began after it
    alive = threading.Event()
    watcher = threading.Thread(target=alive.wait)
    watcher.start()
    s.watcher = watcher
    try:
        assert not s.guard() and s.frozen()
    finally:
        alive.set()


# --- rule 5: a wrapper obeys only the live daemon (lane daemon design, 4.8) ---

def unsigned(lanes, booking):
    with lanes.store.transaction(book.KEY) as tx:
        tx.append({"at": lanes._now(), "booking": booking, "state": book.HELD, "order_cause": "",
                   "order_pid": None, "order_mark": ""})


def test_an_unsigned_order_is_refused(lane):   # review focus 1
    lanes, mine, _waiter, _later, _root = lane
    unsigned(lanes, mine)
    s = FakeSlot()
    assert watch.Watch(lanes, mine, s, lambda reading: value(available=300)).step() == "refused"
    assert s.calls == [] and lanes.bookings()[mine].frozen == ""


def test_an_order_from_a_dead_daemon_is_refused(lane):
    lanes, mine, _waiter, _later, _root = lane
    sign(lanes, mine, by=(4243, "m9"))   # not the pid the lock holder's heartbeat names
    s = FakeSlot()
    assert watch.Watch(lanes, mine, s, lambda reading: value(available=300)).step() == "refused"
    assert s.calls == []


def test_an_order_signed_while_no_daemon_holds_the_lock_is_refused(lane, tmp_path):
    lanes, mine, _waiter, _later, _root = lane
    s = FakeSlot()
    w = watch.Watch(lanes, mine, s, lambda reading: value(available=300))
    lanes.daemon_double.gone()   # its heartbeat stays; its lock is free
    assert w.step() == "refused" and s.calls == []


def test_a_0_30_waiters_order_is_refused(lane):
    lanes, mine, waiter, _later, _root = lane
    unsigned(lanes, mine)   # a rule-4 waiter writes an order with no signature
    with lanes.store.transaction(book.KEY) as tx:
        tx.append({"at": lanes._now(), "booking": waiter, "state": book.WAITING, "rule": 4})
    s = FakeSlot()
    assert watch.Watch(lanes, mine, s, lambda reading: value(available=300)).step() == "refused"


def young(lanes, later):
    item = lanes.enqueue("main session 3", "suite", pid=11, mark="m")
    with lanes.store.transaction(book.KEY) as tx:
        tx.append({"at": lanes._now(), "booking": item.id, "state": book.HELD, "started": lanes._now(),
                   "path": "value: P 0.06, light"})
    later(10)
    return item.id


def test_an_order_with_the_daemons_pid_and_mark_but_a_wrong_seal_is_refused(lane):   # security review of c0b73f9
    lanes, _mine, _waiter, later, _root = lane
    run_id = young(lanes, later)
    order(lanes, run_id, "over", key=b"x" * 32)   # pid and mark copied from the heartbeat; the key not known
    s = FakeSlot()
    assert watch.Watch(lanes, run_id, s, lambda reading: value(available=300)).step() == "refused" and s.calls == []


def test_an_overload_order_is_refused_when_the_machine_is_not_over(lane):   # security review of c0b73f9
    lanes, _mine, _waiter, later, _root = lane
    run_id = young(lanes, later)
    order(lanes, run_id, "over")
    s = FakeSlot()
    assert watch.Watch(lanes, run_id, s, lambda reading: value(available=9000)).step() == "refused"


class Pressures:
    """A run's group as the wrapper reads it, for a pressure order: each read a second on, `refault_s` MB read back
    from swap and `events_s` hits of its ceiling a second."""

    def __init__(self, pressure, refault_s=0, events_s=0):
        self.pressure, self.refault_s, self.events_s, self.reads = pressure, refault_s, events_s, 0

    def cgroup_of(self, unit, booking):
        from pathlib import Path
        return Path("/g")

    def read(self, path):
        from flotilla.lane import groups
        self.reads += 1
        return groups.Reading(2000, 2000, 9 + self.events_s * self.reads, self.pressure, self.refault_s * self.reads)


def test_a_pressure_order_needs_its_run_to_read_pressed(lane):   # security review of c0b73f9
    lanes, _mine, _waiter, later, _root = lane
    run_id = young(lanes, later)
    pressed = young(lanes, later)
    with lanes.store.transaction(book.KEY) as tx:
        tx.append({"at": lanes._now(), "booking": pressed, "state": book.HELD,
                   "unit": f"flotilla-lane-{pressed}-11-1.scope"})
    order(lanes, run_id, "pressed", waiter=pressed)
    calm = watch.Watch(lanes, run_id, FakeSlot(), lambda reading: value(available=300), groups_io=Pressures(2.0),
                       sleep=lambda s: None)
    assert calm.step() == "refused"
    order(lanes, run_id, "pressed", waiter=pressed)
    # the second background review: pressure alone is weaker than the daemon's rule - a cold run pushing its pages
    # out reads 17 % - so the wrapper asks the daemon's own question: held at its ceiling and reading pages back
    pushing = watch.Watch(lanes, run_id, FakeSlot(), lambda reading: value(available=300),
                          groups_io=Pressures(17.0, refault_s=0, events_s=50), sleep=lambda s: None)
    assert pushing.step() == "refused"
    order(lanes, run_id, "pressed", waiter=pressed)
    hot = watch.Watch(lanes, run_id, FakeSlot(), lambda reading: value(available=300),
                      groups_io=Pressures(9.5, refault_s=45, events_s=100), sleep=lambda s: None)
    assert hot.step() == "froze"


def test_an_overload_order_freezes_a_young_run(lane):   # the person's ruling 5
    lanes, _mine, _waiter, later, _root = lane
    run_id = young(lanes, later)
    order(lanes, run_id, "over")
    s = FakeSlot()
    assert watch.Watch(lanes, run_id, s, lambda reading: value(available=300)).step() == "froze"


def test_an_overload_order_never_freezes_an_immortal_or_pinned_run(lane):
    lanes, _mine, _waiter, later, _root = lane
    run_id = young(lanes, later)
    with lanes.store.transaction(book.KEY) as tx:
        tx.append({"at": lanes._now(), "booking": run_id, "state": book.HELD, "pinned": True})
    order(lanes, run_id, "over")
    assert watch.Watch(lanes, run_id, FakeSlot(), lambda reading: value(available=300)).step() == "refused"
    old = young(lanes, later)
    later(1000)   # past the immortality mark of 900 s
    order(lanes, old, "pressed")
    assert watch.Watch(lanes, old, FakeSlot(), lambda reading: value(available=300)).step() == "refused"


def test_a_waiter_order_still_honours_the_grace(lane):
    lanes, _mine, waiter, later, _root = lane
    run_id = young(lanes, later)
    order(lanes, run_id, "waiter", waiter=waiter)
    assert watch.Watch(lanes, run_id, FakeSlot(), lambda reading: value(available=300)).step() == "refused"


def test_a_daemon_frozen_run_waits_for_the_daemons_thaw(lane, monkeypatch):
    lanes, mine, waiter, later, _root = lane
    s = FakeSlot()
    w = watch.Watch(lanes, mine, s, lambda reading: value(available=300))
    assert w.step() == "froze"
    asked = []
    real = lanes.thaw
    monkeypatch.setattr(lanes, "thaw", lambda *a, **kw: asked.append(kw) or real(*a, **kw))
    lanes.leave(waiter)
    later(10)
    w.value_of = lambda reading: value(available=30000)
    assert w.step() == "" and s.frozen() and asked == []
    daemon_thaws(lanes, mine)
    assert w.step() == "thawed" and not s.frozen()


def test_a_frozen_run_thaws_when_the_daemon_dies(lane):   # review focus 2
    lanes, mine, _waiter, _later, root = lane
    s = FakeSlot()
    w = watch.Watch(lanes, mine, s, lambda reading: value(available=300))
    assert w.step() == "froze"
    lanes.daemon_double.gone()
    assert w.step() == "thawed" and not s.frozen() and lanes.bookings()[mine].frozen == ""



def test_an_overload_order_is_judged_against_a_floor_in_reason(lane, monkeypatch):   # final review C2
    from flotilla.lane import machine
    monkeypatch.setattr(machine, "_memtotal", lambda: 15603 * 1024)
    lanes, _mine, _waiter, later, _root = lane
    run_id = young(lanes, later)
    lanes.enqueue("main session 9", "huge floor", pid=13, mark="m", floor_mb=9_000_000)
    order(lanes, run_id, "over")
    s = FakeSlot()
    assert watch.Watch(lanes, run_id, s, lambda reading: value(available=9000)).step() == "refused"



def test_an_order_replayed_after_a_thaw_is_refused(lane):   # final review I4: a sealed order is no ticket for ever
    lanes, _mine, _waiter, later, _root = lane
    run_id = young(lanes, later)
    order(lanes, run_id, "over")
    old = lanes.bookings()[run_id]
    later(5)
    daemon_thaws(lanes, run_id)   # the daemon thawed it since; the same order line written again
    with lanes.store.transaction(book.KEY) as tx:
        tx.append({"at": lanes._now(), "booking": run_id, "state": book.HELD, "frozen": "ordered",
                   "order_by": old.order_by, "order_cause": old.order_cause, "order_pid": old.order_pid,
                   "order_mark": old.order_mark, "ordered_at": old.ordered_at, "reclaim_mb": old.reclaim_mb,
                   "order_sig": old.order_sig})
    assert watch.Watch(lanes, run_id, FakeSlot(), lambda reading: value(available=300)).step() == "refused"
