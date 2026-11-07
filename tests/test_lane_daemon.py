"""The lane daemon's process: one per machine, raised by any client, replaced by a newer copy or when stuck, gone
when idle (lane daemon design, 4.1 and 4.7)."""

import fcntl
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from flotilla import __version__
from flotilla.core.storage import LocalLogStore
from flotilla.lane import book, daemon

REPO = Path(__file__).resolve().parents[1]


class Procs:
    def __init__(self, dead=()):
        self.dead = set(dead)

    def alive(self, pid, mark):
        return pid is None or not mark or pid not in self.dead

    def start_mark(self, pid):
        return "m"


def write_beat(root, *, pid=4242, mark="m9", release=__version__, protocol=daemon.PROTOCOL, tick=None, **kw):
    root.mkdir(parents=True, exist_ok=True)
    data = {"pid": pid, "mark": mark, "copy": str(REPO), "release": release, "protocol": protocol,
            "tick": time.time() if tick is None else tick, "why": {}, "groups": {}, "over": {}, **kw}
    (root / daemon.BEAT).write_text(json.dumps(data))


class Held:
    """The daemon's lock, held by this test as the daemon would hold it."""

    def __init__(self, root):
        root.mkdir(parents=True, exist_ok=True)
        self.fd = os.open(root / daemon.LOCK, os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(self.fd, fcntl.LOCK_EX)

    def release(self):
        os.close(self.fd)


def test_heartbeat_round_trip_and_torn_file(tmp_path):
    write_beat(tmp_path)
    beat = daemon.heartbeat(tmp_path)
    assert (beat.pid, beat.mark, beat.release, beat.protocol) == (4242, "m9", __version__, daemon.PROTOCOL)
    (tmp_path / daemon.BEAT).write_text('{"pid": "x"')
    assert daemon.heartbeat(tmp_path) is None
    write_beat(tmp_path, pid=True)
    assert daemon.heartbeat(tmp_path) is None


def test_alive_needs_the_lock_the_pid_and_a_fresh_tick(tmp_path):
    write_beat(tmp_path)
    assert daemon.alive(tmp_path, Procs()) is None          # the lock is free: nobody holds it
    held = Held(tmp_path)
    try:
        assert daemon.alive(tmp_path, Procs()).pid == 4242
        assert daemon.alive(tmp_path, Procs(dead={4242})) is None
        write_beat(tmp_path, tick=time.time() - daemon.STALE_S - 1)
        assert daemon.alive(tmp_path, Procs()) is None
        write_beat(tmp_path, mark="")
        assert daemon.alive(tmp_path, Procs()) is None
    finally:
        held.release()


def test_signed_matches_only_the_live_daemon(tmp_path):   # review focus 1
    write_beat(tmp_path)
    held = Held(tmp_path)
    try:
        assert daemon.signed(tmp_path, Procs(), 4242, "m9")
        assert not daemon.signed(tmp_path, Procs(), 4242, "other")
        assert not daemon.signed(tmp_path, Procs(), 4243, "m9")
        assert not daemon.signed(tmp_path, Procs(), None, "")
        write_beat(tmp_path, tick=time.time() - 60)
        assert not daemon.signed(tmp_path, Procs(), 4242, "m9")
    finally:
        held.release()
    write_beat(tmp_path)
    assert not daemon.signed(tmp_path, Procs(), 4242, "m9")   # the lock is not held


def copy(tmp_path, name, with_bin=True):
    root = tmp_path / name
    (root / "bin").mkdir(parents=True)
    if with_bin:
        (root / "bin" / "flotilla").write_text("#!/usr/bin/env python3\n")
    return root


def test_newest_copy_picks_the_highest_usable_release(tmp_path):
    old, broken, running = copy(tmp_path, "old"), copy(tmp_path, "broken", with_bin=False), copy(tmp_path, "run")
    installs = lambda: [("flotilla@x", {"installPath": str(old), "version": "0.30.1"}),   # noqa: E731
                        ("flotilla@x", {"installPath": str(broken), "version": "9.9.9"})]
    assert daemon.newest_copy(running=running, installs=installs) == (__version__, running)
    newer = copy(tmp_path, "newer")
    installs2 = lambda: [*installs(), ("flotilla@y", {"installPath": str(newer), "version": "99.0.0"})]  # noqa: E731
    assert daemon.newest_copy(running=running, installs=installs2) == ("99.0.0", newer)


class Spawned:
    def __init__(self, root, procs, beat=True):
        self.root, self.procs, self.beat, self.calls = root, procs, beat, []
        self.held = None

    def __call__(self, where, root, handover=False):
        self.calls.append((where, handover))
        if self.beat:
            self.held = Held(root)
            write_beat(root, pid=5151, mark="n1")


def newest_is(release, where):
    return lambda: (release, where)


def test_ensure_starts_when_nothing_runs(tmp_path):
    spawn = Spawned(tmp_path, Procs())
    try:
        line = daemon.ensure(tmp_path, Procs(), newest=newest_is(__version__, REPO), spawn=spawn, sleep=lambda s: None)
        assert line == f"started the lane daemon ({__version__})" and spawn.calls == [(REPO, False)]
        assert daemon.ensure(tmp_path, Procs(), newest=newest_is(__version__, REPO), spawn=spawn,
                             sleep=lambda s: None) == ""
    finally:
        spawn.held.release()


def test_ensure_replaces_a_stuck_daemon(tmp_path, monkeypatch):
    write_beat(tmp_path, tick=time.time() - 30)
    held = Held(tmp_path)
    stopped = []

    def stop(beat, root, sleep):
        stopped.append(beat.pid)
        held.release()
    monkeypatch.setattr(daemon, "_stop", stop)
    spawn = Spawned(tmp_path, Procs())
    try:
        line = daemon.ensure(tmp_path, Procs(), newest=newest_is(__version__, REPO), spawn=spawn, sleep=lambda s: None)
        assert stopped == [4242] and line.startswith("replaced a stuck lane daemon (no tick for")
    finally:
        spawn.held.release()


def test_ensure_replaces_an_older_release(tmp_path, monkeypatch):
    write_beat(tmp_path, release="0.0.1")
    held = Held(tmp_path)
    monkeypatch.setattr(daemon, "_stop", lambda beat, root, sleep: held.release())
    spawn = Spawned(tmp_path, Procs())
    try:
        line = daemon.ensure(tmp_path, Procs(), newest=newest_is(__version__, REPO), spawn=spawn, sleep=lambda s: None)
        assert line == f"replaced the lane daemon 0.0.1 with {__version__}"
    finally:
        spawn.held.release()


def test_ensure_works_with_a_newer_daemon_it_understands(tmp_path):
    write_beat(tmp_path, release="99.0.0")
    held = Held(tmp_path)
    try:
        assert daemon.ensure(tmp_path, Procs(), newest=newest_is(__version__, REPO), spawn=Spawned(tmp_path, Procs()),
                             sleep=lambda s: None) == ""
    finally:
        held.release()


def test_ensure_refuses_an_unknown_protocol(tmp_path):
    write_beat(tmp_path, protocol=99, release="99.0.0")
    held = Held(tmp_path)
    try:
        line = daemon.ensure(tmp_path, Procs(), newest=newest_is(__version__, REPO), spawn=Spawned(tmp_path, Procs()),
                             sleep=lambda s: None)
        assert line == (f"lane daemon not running: the lane daemon speaks protocol 99, this copy {daemon.PROTOCOL}: "
                        "restart this session")
    finally:
        held.release()


def test_ensure_lifts_ceilings_on_fallback(tmp_path, monkeypatch):
    lifted = []
    monkeypatch.setattr(daemon, "lift", lambda root, lanes, groups_io=None: lifted.append(root) or 0)
    line = daemon.ensure(tmp_path, Procs(), newest=newest_is(__version__, REPO), spawn=Spawned(tmp_path, Procs(),
                         beat=False), sleep=lambda s: None, lanes=object(), now=time.time)
    assert line.startswith("lane daemon not running: it did not start") and lifted == [tmp_path]


def test_lift_sets_every_journal_ceiling_to_max(tmp_path):
    lanes = book.Book(LocalLogStore(tmp_path), Procs())
    item = lanes.enqueue("a", "", pid=11, mark="m")
    with lanes.store.transaction(book.KEY) as tx:
        tx.append({"at": lanes._now(), "booking": item.id, "state": book.HELD, "ceiling_mb": 1000,
                   "unit": f"flotilla-lane-{item.id}-11-1.scope"})
    written = []

    class Groups:
        def cgroup_of(self, unit, booking):
            return Path("/g")

        def set_high(self, path, mb):
            written.append((path, mb))
            return True
    assert daemon.lift(tmp_path, lanes, Groups()) == 1 and written == [(Path("/g"), None)]


def test_serve_exits_idle_and_lifts(tmp_path, monkeypatch):
    lifted = []
    monkeypatch.setattr(daemon, "lift", lambda root, lanes, groups_io=None: lifted.append(root) or 0)
    now = [0.0]

    def sleep(seconds):
        now[0] += seconds
    code = daemon.serve(tmp_path, idle_s=5, clock=lambda: now[0], sleep=sleep, newest=newest_is(__version__, REPO))
    assert code == 0 and lifted == [tmp_path]
    assert daemon.heartbeat(tmp_path).pid == os.getpid() and not daemon.lock_held(tmp_path)


def test_serve_hands_over_to_a_newer_copy(tmp_path, monkeypatch):
    spawned = []
    monkeypatch.setattr(daemon, "_spawn", lambda where, root, handover=False: spawned.append((where, handover)))
    monkeypatch.setattr(daemon, "lift", lambda *a, **kw: pytest.fail("a handover keeps the ceilings"))
    code = daemon.serve(tmp_path, clock=lambda: 0.0, sleep=lambda s: None, newest=newest_is("99.0.0", REPO))
    assert code == 0 and spawned == [(REPO, True)] and not daemon.lock_held(tmp_path)


# --- real processes ---

def env_for(tmp_path):
    return {**os.environ, "FLOTILLA_STATE_DIR": str(tmp_path / "state"), "CLAUDE_CONFIG_DIR": str(tmp_path / "claude"),
            "PYTHONDONTWRITEBYTECODE": "1"}


def start(tmp_path, *extra):
    return subprocess.Popen([sys.executable, str(REPO / "bin" / "flotilla"), "lane", "daemon", "run", *extra],
                            env=env_for(tmp_path), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            start_new_session=True)


def wait_for(predicate, seconds=15.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.1)
    return False


def lane_root(tmp_path):
    return tmp_path / "state" / "lane"


def stop_all(*procs):
    for proc in procs:
        if proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait()


def test_two_starts_one_daemon(tmp_path):   # review focus 5
    first, second = start(tmp_path), start(tmp_path)
    try:
        assert wait_for(lambda: daemon.heartbeat(lane_root(tmp_path)) is not None)
        assert wait_for(lambda: first.poll() is not None or second.poll() is not None, 10)
        out = first if first.poll() is not None else second
        rest = second if out is first else first
        text, _ = out.communicate(timeout=10)
        assert out.returncode == 0 and "the lane daemon already runs" in text
        assert rest.poll() is None and daemon.heartbeat(lane_root(tmp_path)).pid == rest.pid
    finally:
        stop_all(first, second)


def test_a_killed_daemon_is_replaced_by_ensure(tmp_path):
    proc = start(tmp_path)
    try:
        assert wait_for(lambda: daemon.heartbeat(lane_root(tmp_path)) is not None)
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()
        done = subprocess.run([sys.executable, str(REPO / "bin" / "flotilla"), "lane", "daemon", "ensure"],
                              env=env_for(tmp_path), capture_output=True, text=True, timeout=60)
        assert done.returncode == 0, done.stdout + done.stderr
        assert "started the lane daemon" in done.stdout
        beat = daemon.heartbeat(lane_root(tmp_path))
        assert beat.pid != proc.pid
    finally:
        stop_all(proc)
        beat = daemon.heartbeat(lane_root(tmp_path))
        if beat is not None:
            try:
                os.killpg(beat.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass


def test_daemon_exits_idle(tmp_path):
    proc = start(tmp_path, "--idle", "2")
    try:
        text, _ = proc.communicate(timeout=30)
        assert proc.returncode == 0, text
        assert not daemon.lock_held(lane_root(tmp_path))
    finally:
        stop_all(proc)
