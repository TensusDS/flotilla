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


def value(available=9000, tree=5000):
    return admission.Value(cpus=8, cpu_factor=1.5, light_reserve=0.2, immortal_s=900.0, max_runs=10,
                           freeze=freeze.SCOPE, available_mb=lambda: available, floor_mb=0,
                           swap_mb=lambda: (8000, 8000), tree_mb=lambda pid: tree if pid == 11 else 100,
                           beat_age_s=lambda booking: 0.0)


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
    return lanes, first.id, waiter.id, later, tmp_path


def test_the_watch_freezes_its_command_on_an_order(lane):
    lanes, mine, _waiter, _later, _root = lane
    s = FakeSlot()
    w = watch.Watch(lanes, mine, s, lambda reading: value(available=300), swap=lambda: (8000, 8000))
    assert w.step() == "froze"
    found = lanes.bookings()[mine]
    assert s.calls == [("freeze", found.reclaim_mb)] and found.frozen == "frozen" and found.unit == "flotilla-lane-b1-11-1.scope"


def test_the_watch_thaws_when_the_policy_lets_it(lane):
    lanes, mine, waiter, later, _root = lane
    s = FakeSlot()
    w = watch.Watch(lanes, mine, s, lambda reading: value(available=300))
    w.step()
    assert w.step() == ""   # its room is still short
    lanes.leave(waiter)
    later(10)
    w.value_of = lambda reading: value(available=3000)
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
    monkeypatch.setattr(lanes, "thaw", broken)
    w.start()
    w.join(timeout=5)
    assert not w.alive() and not s.frozen()
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
