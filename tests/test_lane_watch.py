import io
"""The holder's watch: it freezes its command on an order, thaws it through the queue, lets a signal stop go, never
leaves its command frozen when it fails, and beats while it lives (lane shared machine design, 4.1-4.3)."""

import datetime as dt
import os
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
    w = watch.Watch(lanes, mine, s, lambda reading: value(available=300))
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

    def popen(*a, **kw):
        started.append(s.frozen())
        return real(*a, **kw)
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
