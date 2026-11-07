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


@pytest.fixture(autouse=True)
def daemon_on(monkeypatch):
    """The suite keeps clients from raising a daemon (conftest); these tests are about raising one."""
    monkeypatch.delenv(daemon.OFF_ENV, raising=False)


@pytest.fixture(autouse=True)
def no_signal_leaves_a_test(monkeypatch):
    """These tests name made-up pids (4242): no signal may reach a real process of that number."""
    sent = []

    def refused(pid, number):
        sent.append((pid, number))
    monkeypatch.setattr(daemon, "_signal_group", refused)
    return sent


class Procs:
    def __init__(self, dead=()):
        self.dead = set(dead)

    def alive(self, pid, mark):
        return pid is None or not mark or pid not in self.dead

    def start_mark(self, pid):
        return "m"


def write_beat(root, *, pid=4242, mark="m9", release=__version__, protocol=daemon.PROTOCOL, tick=None, beat=None,
               **kw):
    root.mkdir(parents=True, exist_ok=True)
    tick = time.time() if tick is None else tick
    data = {"pid": pid, "mark": mark, "copy": str(REPO), "release": release, "protocol": protocol,
            "tick": tick, "beat": tick if beat is None else beat, "why": {}, "groups": {}, "over": {}, **kw}
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


KEY = b"s" * 32


def write_key(root, key=KEY, mode=0o600):
    (root / daemon.KEY).write_bytes(key)
    os.chmod(root / daemon.KEY, mode)


def ordered(pid=4242, mark="m9", cause="over", by="", at="2026-11-07T10:00:00+00:00", key=KEY, booking="b5",
            reclaim=700):
    from types import SimpleNamespace
    return SimpleNamespace(id=booking, order_pid=pid, order_mark=mark, order_cause=cause, order_by=by, ordered_at=at,
                           reclaim_mb=reclaim, order_sig=daemon.seal(key, booking, cause, at, by, pid, mark, reclaim))


def test_signed_matches_only_the_live_daemon(tmp_path):   # review focus 1
    write_beat(tmp_path)
    write_key(tmp_path)
    held = Held(tmp_path)
    try:
        assert daemon.signed(tmp_path, Procs(), ordered())
        assert not daemon.signed(tmp_path, Procs(), ordered(mark="other"))
        assert not daemon.signed(tmp_path, Procs(), ordered(pid=4243))
        write_beat(tmp_path, tick=time.time() - 60)
        assert not daemon.signed(tmp_path, Procs(), ordered())
    finally:
        held.release()
    write_beat(tmp_path)
    assert not daemon.signed(tmp_path, Procs(), ordered())   # the lock is not held


def test_signed_needs_the_daemons_seal(tmp_path):   # security review of c0b73f9: a pid and a mark are public
    write_beat(tmp_path)
    write_key(tmp_path)
    held = Held(tmp_path)
    try:
        forged = ordered(key=b"x" * 32)   # the daemon's pid and mark, copied from its heartbeat
        assert not daemon.signed(tmp_path, Procs(), forged)
        moved = ordered()
        moved.order_cause = "pressed"     # a sealed order turned into another
        assert not daemon.signed(tmp_path, Procs(), moved)
        bigger = ordered()
        bigger.reclaim_mb = 9999          # final review I4: what it moves to swap is sealed too
        assert not daemon.signed(tmp_path, Procs(), bigger)
        write_key(tmp_path, mode=0o644)   # a key others may read is no key
        assert not daemon.signed(tmp_path, Procs(), ordered())
        (tmp_path / daemon.KEY).unlink()
        (tmp_path / "elsewhere").write_bytes(KEY)
        os.chmod(tmp_path / "elsewhere", 0o600)
        (tmp_path / daemon.KEY).symlink_to(tmp_path / "elsewhere")
        assert not daemon.signed(tmp_path, Procs(), ordered())
    finally:
        held.release()


def copy(tmp_path, name, with_bin=True, version=None):
    root = tmp_path / name
    (root / "bin").mkdir(parents=True)
    if with_bin:
        (root / "bin" / "flotilla").write_text("#!/usr/bin/env python3\n")
    if version:
        (root / "flotilla").mkdir()
        (root / "flotilla" / "__init__.py").write_text(f'__version__ = "{version}"\n')
    return root


def test_newest_copy_picks_the_highest_usable_release(tmp_path):
    old, running = copy(tmp_path, "old", version="0.30.1"), copy(tmp_path, "run")
    broken = copy(tmp_path, "broken", with_bin=False, version="9.9.9")
    installs = lambda: [("flotilla@x", {"installPath": str(old), "version": "0.30.1"}),   # noqa: E731
                        ("flotilla@x", {"installPath": str(broken), "version": "9.9.9"})]
    assert daemon.newest_copy(running=running, installs=installs) == (__version__, running)
    newer = copy(tmp_path, "newer", version="99.0.0")
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


def test_ensure_starts_when_nothing_runs(tmp_path, monkeypatch):
    spawn = Spawned(tmp_path, Procs())
    try:
        line = daemon.ensure(tmp_path, Procs(), newest=newest_is(__version__, REPO), spawn=spawn, sleep=lambda s: None)
        assert line == f"started the lane daemon ({__version__})" and spawn.calls == [(REPO, False)]
        assert daemon.ensure(tmp_path, Procs(), newest=newest_is(__version__, REPO), spawn=spawn,
                             sleep=lambda s: None) == ""
    finally:
        spawn.held.release()


def test_ensure_replaces_a_stuck_daemon(tmp_path, monkeypatch):
    write_beat(tmp_path, tick=time.time() - daemon.STUCK_S - 5, beat=time.time())   # it beats; its ticks stopped
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
    key = tmp_path / daemon.KEY
    assert len(key.read_bytes()) >= 32 and (key.stat().st_mode & 0o077) == 0   # a key of its own, private
    assert daemon.heartbeat(tmp_path).pid == os.getpid() and not daemon.lock_held(tmp_path)


def test_serve_hands_over_to_a_newer_copy(tmp_path, monkeypatch):
    spawned = []
    monkeypatch.setattr(daemon, "_spawn", lambda where, root, handover=False: spawned.append((where, handover)))
    monkeypatch.setattr(daemon, "lift", lambda *a, **kw: pytest.fail("a handover keeps the ceilings"))
    code = daemon.serve(tmp_path, clock=lambda: 0.0, sleep=lambda s: None, newest=newest_is("99.0.0", REPO))
    assert code == 0 and spawned == [(REPO, True)] and not daemon.lock_held(tmp_path)


# --- real processes ---

def env_for(tmp_path):
    env = {**os.environ, "FLOTILLA_STATE_DIR": str(tmp_path / "state"), "CLAUDE_CONFIG_DIR": str(tmp_path / "claude"),
           "PYTHONDONTWRITEBYTECODE": "1"}
    env.pop(daemon.OFF_ENV, None)   # these tests raise the daemon for real, in their own state directory
    return env


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


def test_ensure_takes_the_spawn_named_at_call_time(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(daemon, "_spawn", lambda where, root, handover=False: calls.append(where))
    daemon.ensure(tmp_path, Procs(), newest=newest_is(__version__, REPO), sleep=lambda s: None)
    assert calls == [REPO]


def test_the_daemon_can_be_switched_off(tmp_path, monkeypatch):
    monkeypatch.setenv(daemon.OFF_ENV, "off")
    spawn = Spawned(tmp_path, Procs())
    assert daemon.ensure(tmp_path, Procs(), spawn=spawn) == f"{daemon.FALLBACK}{daemon.OFF_ENV}=off"
    assert spawn.calls == []



def test_alive_reads_the_beat_not_the_tick(tmp_path):   # final review I3: a long tick is not a dead daemon
    write_beat(tmp_path, tick=time.time() - 30, beat=time.time())
    held = Held(tmp_path)
    try:
        assert daemon.alive(tmp_path, Procs()) is not None
        write_beat(tmp_path, tick=time.time(), beat=time.time() - daemon.STALE_S - 1)
        assert daemon.alive(tmp_path, Procs()) is None
    finally:
        held.release()


def test_ensure_leaves_a_daemon_inside_a_long_tick(tmp_path):
    write_beat(tmp_path, tick=time.time() - 30, beat=time.time())   # one slow systemctl, under load
    held = Held(tmp_path)
    try:
        assert daemon.ensure(tmp_path, Procs(), newest=newest_is(__version__, REPO), spawn=Spawned(tmp_path, Procs()),
                             sleep=lambda s: None) == ""
    finally:
        held.release()


def test_the_daemon_beats_while_a_tick_takes_long(tmp_path, monkeypatch):
    import threading
    release = threading.Event()

    class Slow:
        def step(self):
            release.wait(6)
            return None, {}
    monkeypatch.setattr(daemon, "lift", lambda *a, **kw: 0)
    done = threading.Thread(target=daemon.serve, args=(tmp_path,), daemon=True,
                            kwargs={"ticks": 1, "newest": newest_is(__version__, REPO), "make_scheduler": lambda: Slow()})
    done.start()
    time.sleep(daemon.BEAT_S + 1.5)
    beat = daemon.heartbeat(tmp_path)
    release.set()
    done.join(10)
    assert beat is not None and time.time() - beat.beat < daemon.BEAT_S + 1 and beat.pid == os.getpid()



def test_stop_never_signals_a_process_it_cannot_prove_is_the_daemon(tmp_path, no_signal_leaves_a_test):
    """Final review I2: a daemon.json may name any process. The lock holder here is this test process, whose command
    is no `lane daemon run`; and 4242 holds no lock at all."""
    write_beat(tmp_path, pid=4242, tick=time.time() - daemon.STUCK_S - 5)
    held = Held(tmp_path)
    try:
        line = daemon.ensure(tmp_path, Procs(), newest=newest_is(__version__, REPO), spawn=Spawned(tmp_path, Procs()),
                             sleep=lambda s: None)
        assert no_signal_leaves_a_test == [] and line.startswith(daemon.FALLBACK)
        write_beat(tmp_path, pid=os.getpid(), tick=time.time() - daemon.STUCK_S - 5)   # the holder, not a daemon
        line = daemon.ensure(tmp_path, Procs(), newest=newest_is(__version__, REPO), spawn=Spawned(tmp_path, Procs()),
                             sleep=lambda s: None)
        assert no_signal_leaves_a_test == [] and line.startswith(daemon.FALLBACK)
    finally:
        held.release()


@pytest.mark.skipif(not Path("/proc/locks").exists(), reason="no /proc/locks here")
def test_the_lock_holder_is_read_from_the_kernel(tmp_path):
    held = Held(tmp_path)
    try:
        assert daemon._lock_holder(tmp_path) == os.getpid()
    finally:
        held.release()
    assert daemon._lock_holder(tmp_path) is None


def test_a_real_daemon_is_provably_the_daemon(tmp_path):
    proc = start(tmp_path)
    try:
        assert wait_for(lambda: daemon.heartbeat(lane_root(tmp_path)) is not None)
        beat = daemon.heartbeat(lane_root(tmp_path))
        assert daemon._provably_daemon(beat, lane_root(tmp_path))
        fake = daemon.Heartbeat(**{**beat.__dict__, "pid": os.getpid()})
        assert not daemon._provably_daemon(fake, lane_root(tmp_path))
    finally:
        stop_all(proc)



def test_ensure_lifts_ceilings_on_every_fallback(tmp_path, monkeypatch):   # final review I1
    lifted = []
    monkeypatch.setattr(daemon, "lift", lambda root, lanes, groups_io=None: lifted.append(root) or 0)
    held = Held(tmp_path)   # a lock held by a process that never beats
    try:
        line = daemon.ensure(tmp_path, Procs(), newest=newest_is(__version__, REPO), spawn=Spawned(tmp_path, Procs()),
                             sleep=lambda s: None, lanes=object())
        assert line.startswith(daemon.FALLBACK) and lifted == [tmp_path]
    finally:
        held.release()
    monkeypatch.setenv(daemon.OFF_ENV, "off")
    daemon.ensure(tmp_path, Procs(), lanes=object())
    assert lifted == [tmp_path, tmp_path]



ORIGINAL_SPAWN = daemon._spawn   # taken at import, before the suite's double replaces it


def test_a_lock_taken_an_instant_late_is_still_taken(tmp_path, monkeypatch):   # final review M2
    real, tries = daemon.fcntl.flock, []

    def busy_once(fd, how):
        tries.append(how)
        if len(tries) == 1 and how & daemon.fcntl.LOCK_NB:
            raise BlockingIOError("a client probing this very instant")
        return real(fd, how)
    monkeypatch.setattr(daemon.fcntl, "flock", busy_once)
    monkeypatch.setattr(daemon, "lift", lambda *a, **kw: 0)
    code = daemon.serve(tmp_path, ticks=1, newest=newest_is(__version__, REPO), sleep=lambda s: None)
    assert code == 0 and daemon.heartbeat(tmp_path).pid == os.getpid()


def test_newest_copy_trusts_the_copy_not_its_record(tmp_path):   # final review M6
    lying = copy(tmp_path, "lying")
    (lying / "flotilla").mkdir()
    (lying / "flotilla" / "__init__.py").write_text('__version__ = "0.30.1"\n')
    running = copy(tmp_path, "run")
    installs = lambda: [("flotilla@x", {"installPath": str(lying), "version": "99.0.0"})]   # noqa: E731
    assert daemon.newest_copy(running=running, installs=installs) == (__version__, running)


def test_the_heartbeat_follows_no_link(tmp_path):   # final review M7
    victim = tmp_path / "victim"
    victim.write_text("keep")
    (tmp_path / f".{daemon.BEAT}.{os.getpid()}").symlink_to(victim)
    daemon._write_beat(tmp_path, os.getpid(), "m", {}, time.time())
    assert victim.read_text() == "keep" and daemon.heartbeat(tmp_path).pid == os.getpid()


def test_the_log_follows_no_link(tmp_path, monkeypatch):   # final review M7
    victim = tmp_path / "victim"
    victim.write_text("keep")
    (tmp_path / daemon.LOG).symlink_to(victim)
    monkeypatch.setattr(daemon.subprocess, "Popen", lambda *a, **kw: pytest.fail("nothing starts"))
    with pytest.raises(OSError):
        ORIGINAL_SPAWN(REPO, tmp_path)
    assert victim.read_text() == "keep"


def test_the_daemon_is_started_detached_from_its_caller(tmp_path, monkeypatch):   # final review M8
    started = []

    class Done:
        def __init__(self, argv, **kw):
            started.append(argv)

        def wait(self, timeout=None):
            return 0
    monkeypatch.setattr(daemon.subprocess, "Popen", Done)
    ORIGINAL_SPAWN(REPO, tmp_path)
    argv = started[0]
    at = argv.index("-c")   # a launcher that starts the daemon and exits: the daemon is nobody's child
    assert argv[at - 1] == sys.executable and argv[-3:] == ["lane", "daemon", "run"] and "setsid" in argv[at + 1]


def test_the_daemon_reads_the_machine_settings_each_tick(monkeypatch):   # final review M10
    from flotilla.lane import commands
    calls = []
    monkeypatch.setattr(commands, "value_for", lambda profile: calls.append(profile) or (lambda reading=None: "v"))
    read = daemon._settings_now()
    read(None)
    read(None)
    assert len(calls) == 2



def test_a_wedged_tick_loop_stops_the_heartbeat(tmp_path, monkeypatch):   # fix review R1
    """A tick stuck on the journal's lock must not read as a live daemon: the beat stops once ticks have stopped for
    STUCK_S, so a wrapper thaws and lifts, as every reader of `alive` agrees with ensure."""
    import threading
    release = threading.Event()

    class Wedged:
        def step(self):
            release.wait(8)
            return None, {}
    monkeypatch.setattr(daemon, "STUCK_S", 1.0)
    monkeypatch.setattr(daemon, "lift", lambda *a, **kw: 0)
    done = threading.Thread(target=daemon.serve, args=(tmp_path,), daemon=True,
                            kwargs={"ticks": 1, "newest": newest_is(__version__, REPO),
                                    "make_scheduler": lambda: Wedged()})
    done.start()
    time.sleep(daemon.BEAT_S * 2 + 0.5)
    beat = daemon.heartbeat(tmp_path)
    release.set()
    done.join(10)
    assert beat is not None and time.time() - beat.beat > daemon.BEAT_S


def test_a_slow_launcher_is_no_crash(tmp_path, monkeypatch):   # fix review R3
    import subprocess as sp

    class Slow:
        def __init__(self, argv, **kw):
            pass

        def wait(self, timeout=None):
            raise sp.TimeoutExpired("launcher", timeout)
    monkeypatch.setattr(daemon.subprocess, "Popen", Slow)
    ORIGINAL_SPAWN(REPO, tmp_path)   # returns: the launcher finishes on its own


def test_ensure_takes_the_fallback_when_a_start_fails_any_way(tmp_path):   # fix review R3
    import subprocess as sp

    def broken(where, root, handover=False):
        raise sp.SubprocessError("no user bus")
    line = daemon.ensure(tmp_path, Procs(), newest=newest_is(__version__, REPO), spawn=broken, sleep=lambda s: None)
    assert line.startswith(daemon.FALLBACK + "it could not be started")


def test_only_the_daemons_own_command_is_the_daemon(monkeypatch):   # fix review R6
    words = {1: [sys.executable, "/x/bin/flotilla", "lane", "daemon", "run"],
             2: [sys.executable, "-m", "pytest", "-k", "lane", "daemon", "run"],
             3: ["sh", "-c", "echo lane daemon run"]}
    assert daemon._is_daemon_command(words[1])
    assert not daemon._is_daemon_command(words[2]) and not daemon._is_daemon_command(words[3])


def test_a_fifo_in_the_state_directory_hangs_nothing(tmp_path):   # fix review R7
    import os as _os
    for name in (daemon.BEAT, daemon.KEY, daemon.LOG):
        _os.mkfifo(tmp_path / name)
    began = time.monotonic()
    assert daemon.heartbeat(tmp_path) is None and daemon.key(tmp_path) is None
    with pytest.raises(OSError):
        ORIGINAL_SPAWN(REPO, tmp_path)
    assert time.monotonic() - began < 2


def test_the_heartbeat_thread_is_done_before_the_lock_is_let_go(tmp_path, monkeypatch):   # fix review R8
    order = []
    real = daemon._write_beat

    def slow_beat(*a, **kw):
        time.sleep(0.3)
        real(*a, **kw)
        order.append("beat")
    monkeypatch.setattr(daemon, "_write_beat", slow_beat)
    monkeypatch.setattr(daemon, "lift", lambda *a, **kw: 0)
    monkeypatch.setattr(daemon, "BEAT_S", 0.05)
    daemon.serve(tmp_path, ticks=2, newest=newest_is(__version__, REPO), sleep=lambda s: time.sleep(0.01))
    order.append("returned")
    time.sleep(0.6)
    assert order[-1] == "returned"   # no beat lands after serve let the lock go


def test_the_loop_reads_settings_and_floor_each_tick(tmp_path, monkeypatch):   # fix review R12
    from flotilla.lane import scheduler
    made = {}

    class Capture:
        def __init__(self, lanes, value_of, **kw):
            made.update(value_of=value_of, default_floor=kw["default_floor"])

        def step(self):
            return None, {}
    monkeypatch.setattr(scheduler, "Scheduler", Capture)
    monkeypatch.setattr(daemon, "_settings_now", lambda: "settings each tick")
    monkeypatch.setattr(daemon, "lift", lambda *a, **kw: 0)
    daemon.serve(tmp_path, ticks=1, newest=newest_is(__version__, REPO), sleep=lambda s: None)
    assert made["value_of"] == "settings each tick" and callable(made["default_floor"])
