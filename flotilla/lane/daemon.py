"""The lane daemon: one process per machine that decides every start, ceiling, freeze and thaw of the lane (lane
daemon design, sections 4.1 and 4.7).

It holds `daemon.lock` in the lane's state directory for as long as it lives, and writes `daemon.json` - its pid,
start mark, copy, release, protocol and last tick, with each waiter's why and each run's group - every tick. Any lane
client raises it (`ensure`) when it is absent, stuck, or older than the newest copy installed; it leaves after ten
minutes with no booking, lifting every ceiling it set. A client that cannot raise it takes the fallback: a new run
starts only into an empty lane.
"""

from __future__ import annotations

import contextlib
import dataclasses
import fcntl
import hmac
import json
import os
import secrets
import signal
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path

from flotilla import __version__
from flotilla.core import claude_state
from flotilla.lane import book, groups

PROTOCOL = 1
KNOWN = frozenset({1})
#: Builder's numbers (lane daemon design, section 7); they await the field test.
TICK_S, IDLE_S, STALE_S, TERM_WAIT_S, START_WAIT_S = 2.0, 600.0, 10.0, 5.0, 10.0
#: The heartbeat is written by a thread of its own every BEAT_S: it says the process lives, so a wrapper never thaws
#: its run because one tick was slow. A daemon whose ticks have stopped for STUCK_S - longer than any call a tick
#: makes (systemctl answers within 10 s) - is stuck, and replaced (final review I3).
BEAT_S, STUCK_S = 2.0, 60.0
LOCK, BEAT, LOG, KEY = "daemon.lock", "daemon.json", "daemon.log", "daemon.key"
seal = book.seal
LOG_CAP = 1_000_000
ERRORS_IN_A_ROW = 3
FALLBACK = "lane daemon not running: "
#: `FLOTILLA_LANE_DAEMON=off` keeps clients from raising the daemon: they take the fallback, a new run only into an
#: empty lane. The test suite sets it, since a test's client would leave a real process behind.
OFF_ENV = "FLOTILLA_LANE_DAEMON"


@dataclasses.dataclass(frozen=True)
class Heartbeat:
    pid: int
    mark: str
    copy: str
    release: str
    protocol: int
    tick: float        # wall clock of its last finished tick
    beat: float        # wall clock of its last heartbeat: the process lives
    why: dict
    groups: dict
    over: dict


def heartbeat(root: Path) -> Heartbeat | None:
    """The daemon's last word, or None when there is none or it does not read as one."""
    try:
        data = json.loads((Path(root) / BEAT).read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return None
    if not isinstance(data, dict):
        return None
    types = {"pid": int, "mark": str, "copy": str, "release": str, "protocol": int, "tick": (int, float),
             "beat": (int, float), "why": dict, "groups": dict, "over": dict}
    for key, kind in types.items():
        if not isinstance(data.get(key), kind) or isinstance(data.get(key), bool):
            return None
    if data["pid"] < 2:
        return None
    return Heartbeat(**{key: data[key] for key in types})


def _version(text) -> tuple:
    return claude_state._version(text) or (0, 0, 0)


def lock_held(root: Path) -> bool:
    """Whether a process holds the daemon's lock now: asked by taking it for an instant."""
    try:
        fd = os.open(Path(root) / LOCK, os.O_RDWR | os.O_CREAT, 0o600)
    except OSError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return True
    finally:
        os.close(fd)   # closing the descriptor drops a lock taken through it
    return False


def alive(root: Path, procs, *, now=time.time) -> Heartbeat | None:
    """The daemon that runs now: its heartbeat names a live process with its start mark, the lock is held, and it
    beat within STALE_S. None otherwise."""
    beat = heartbeat(root)
    if beat is None or not beat.mark or not lock_held(root) or not procs.alive(beat.pid, beat.mark):
        return None
    return beat if now() - beat.beat <= STALE_S else None


def key(root: Path) -> bytes | None:
    """The running daemon's key: a regular file of this user's, readable by nobody else, never a link; None
    otherwise."""
    path = Path(root) / KEY
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        return None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
            return None
        found = os.read(fd, 4096)
    finally:
        os.close(fd)
    return found if len(found) >= 32 else None


def _new_key(root: Path) -> bytes:
    """A key of this daemon's own, made when it takes the lock: an order sealed by an earlier daemon does not hold."""
    found = secrets.token_hex(32).encode("ascii")
    tmp = Path(root) / f".{KEY}.{os.getpid()}"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        os.write(fd, found)
    finally:
        os.close(fd)
    os.chmod(tmp, 0o600)
    os.replace(tmp, Path(root) / KEY)
    return found


def signed(root: Path, procs, item) -> bool:
    """Whether the order a booking carries is the live daemon's: its pid and start mark, and its seal over everything
    the order says. Any session writes the journal, and only the daemon decides (lane daemon design, 4.8)."""
    beat = alive(root, procs)
    if beat is None or not item.order_mark or item.order_pid != beat.pid or item.order_mark != beat.mark:
        return False
    found = key(root)
    if found is None or not item.order_sig:
        return False
    expected = seal(found, item.id, item.order_cause, item.ordered_at, item.order_by, item.order_pid, item.order_mark)
    return hmac.compare_digest(expected, item.order_sig)


def newest_copy(*, running: Path | None = None, installs=claude_state.installs) -> tuple[str, Path]:
    """The highest release among the copies installed on this machine that can run, and where it is; the running copy
    counts as one (the person's ruling 4: code and daemon stay matched through the update mechanism)."""
    running = Path(running or claude_state.running_root())
    found = []
    if (running / "bin" / "flotilla").is_file():
        found.append((_version(__version__), __version__, running))
    for _name, item in installs():
        where = Path(str(item.get("installPath") or ""))
        release = str(item.get("version") or "")
        if item.get("installPath") and claude_state._version(release) and (where / "bin" / "flotilla").is_file():
            found.append((_version(release), release, where))
    if not found:
        return __version__, running
    best = max(found, key=lambda entry: entry[0])
    return best[1], best[2]


def lift(root: Path, lanes, groups_io=groups) -> int:
    """Set every scope the journal gives a ceiling back to "max": no run outlives its scheduler under a cap. How many
    were lifted. The journal keeps its ceilings: a daemon raised later sets them again."""
    lifted = 0
    for item in lanes.bookings().values():
        if item.state != book.HELD or item.ceiling_mb is None or not item.unit:
            continue
        path = groups_io.cgroup_of(item.unit, item.id)
        if path is not None and groups_io.set_high(path, None):
            lifted += 1
    return lifted


def _spawn(where: Path, root: Path, handover: bool = False) -> None:
    """Start the daemon from copy `where`, detached: its own session, no terminal, output to `daemon.log`. Where this
    machine makes scopes, in a scope of its own - a client inside a lane run's scope would otherwise put the daemon
    in it, and freezing that run would freeze the daemon. Never under a booking: it holds none."""
    from flotilla.lane import acquire, freeze
    argv = [sys.executable, str(Path(where) / "bin" / "flotilla"), "lane", "daemon", "run",
            *(["--handover"] if handover else [])]
    if freeze.backend(setting=True)[0] == freeze.SCOPE:
        argv = ["systemd-run", "--user", "--scope", "--quiet", "--collect",
                f"--unit=flotilla-daemon-{os.getpid()}-{time.time_ns()}.scope", "--", *argv]
    env = {key: value for key, value in os.environ.items() if key != acquire.ENV}
    Path(root).mkdir(parents=True, exist_ok=True)
    log = Path(root) / LOG
    with contextlib.suppress(OSError):
        if log.stat().st_size > LOG_CAP:
            log.write_text("", encoding="utf-8")
    with open(log, "a", encoding="utf-8") as out:
        subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT, env=env,
                         cwd=str(root), start_new_session=True)


def _lock_holder(root: Path) -> int | None:
    """The process that holds the daemon's lock, as the kernel lists it (`/proc/locks`); None when nobody does or it
    cannot be read."""
    try:
        info = os.stat(Path(root) / LOCK)
        lines = Path("/proc/locks").read_text(encoding="ascii", errors="replace").splitlines()
    except OSError:
        return None
    wanted = f"{os.major(info.st_dev):02x}:{os.minor(info.st_dev):02x}:{info.st_ino}"
    for line in lines:
        parts = line.split()
        if "FLOCK" in parts and wanted in parts:
            at = parts.index(wanted)
            if at > 0 and parts[at - 1].isdigit():
                return int(parts[at - 1])
    return None


def _command(pid: int) -> list[str]:
    try:
        return [part.decode("utf-8", "replace") for part in
                Path(f"/proc/{int(pid)}/cmdline").read_bytes().split(b"\0") if part]
    except OSError:
        pass
    try:
        done = subprocess.run(["ps", "-ww", "-o", "command=", "-p", str(int(pid))], capture_output=True, text=True,
                              timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        return []
    return (done.stdout or "").split()


def _provably_daemon(beat: Heartbeat, root: Path) -> bool:
    """Whether the process a heartbeat names is the lane daemon: it holds the lock (where the kernel lists locks),
    leads its own process group, and runs `lane daemon run`. A heartbeat is a file any session can write, and the
    signal that follows reaches a whole process group (final review I2)."""
    if Path("/proc/locks").exists() and _lock_holder(root) != beat.pid:
        return False
    try:
        if os.getpgid(beat.pid) != beat.pid:
            return False
    except OSError:
        return False
    words = _command(beat.pid)
    return any(words[i:i + 3] == ["lane", "daemon", "run"] for i in range(len(words)))


def _signal_group(pid: int, number: int) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pid, number)


def _stop(beat: Heartbeat, root: Path, sleep) -> bool:
    """TERM to the daemon's process group, KILL after TERM_WAIT_S, until its lock is free. False, and no signal, when
    the process is not provably the daemon."""
    if not _provably_daemon(beat, root):
        return False
    for number, wait in ((signal.SIGTERM, TERM_WAIT_S), (signal.SIGKILL, 2.0)):
        _signal_group(beat.pid, number)
        for _ in range(int(wait / 0.1)):
            if not lock_held(root):
                return True
            sleep(0.1)
    return not lock_held(root)


def ensure(root: Path, procs, *, newest=newest_copy, spawn=None, now=time.time, sleep=time.sleep,
           lanes=None, env=os.environ) -> str:
    """Make sure the newest daemon runs. "" when one already does; else what was done, in one line; a line starting
    FALLBACK when no daemon can be had - the caller then takes the fallback (lane daemon design, section 6)."""
    spawn = spawn or _spawn   # looked up now, not when this was defined
    if env.get(OFF_ENV, "").strip().lower() == "off":
        return f"{FALLBACK}{OFF_ENV}=off"
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    release, where = newest()
    line = f"started the lane daemon ({release})"
    if lock_held(root):
        beat = alive(root, procs, now=lambda: 0.0)   # the process and the lock; the tick is judged below
        for _ in range(int(START_WAIT_S / 0.1)):
            if beat is not None:
                break
            sleep(0.1)   # a daemon just starting holds the lock before its first heartbeat
            beat = alive(root, procs, now=lambda: 0.0)
        if beat is None:
            return f"{FALLBACK}its lock is held by a process that does not answer"
        if beat.protocol not in KNOWN:
            return (f"{FALLBACK}the lane daemon speaks protocol {beat.protocol}, this copy {PROTOCOL}: restart this "
                    "session")
        silent, age = now() - beat.beat, now() - beat.tick
        if silent > STALE_S or age > STUCK_S:
            if _stop(beat, root, sleep) is False:
                return _fallback(root, lanes, "its lock is held by a process that is not provably the lane daemon")
            line = (f"replaced a stuck lane daemon (no heartbeat for {int(silent)} s)" if silent > STALE_S
                    else f"replaced a stuck lane daemon (no tick for {int(age)} s)")
        elif _version(release) > _version(beat.release):
            if _stop(beat, root, sleep) is False:
                return _fallback(root, lanes, "its lock is held by a process that is not provably the lane daemon")
            line = f"replaced the lane daemon {beat.release} with {release}"
        else:
            return ""
    try:
        spawn(where, root)
    except OSError as err:
        return _fallback(root, lanes, f"it could not be started: {err}")
    for _ in range(int(START_WAIT_S / 0.1)):
        if alive(root, procs, now=now) is not None:
            return line
        sleep(0.1)
    return _fallback(root, lanes, f"it did not start (see {root / LOG})")


def _fallback(root: Path, lanes, why: str) -> str:
    if lanes is not None:
        with contextlib.suppress(Exception):
            lift(root, lanes)
    return FALLBACK + why


def _write_beat(root: Path, pid: int, mark: str, picture: dict, tick: float) -> None:
    data = {"pid": pid, "mark": mark, "copy": str(claude_state.running_root()), "release": __version__,
            "protocol": PROTOCOL, "tick": tick, "beat": time.time(), "why": picture.get("why", {}),
            "groups": picture.get("groups", {}), "over": picture.get("over", {})}
    tmp = Path(root) / f".{BEAT}.{pid}"
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, Path(root) / BEAT)


def _busy(lanes) -> bool:
    return any(item.state in (book.HELD, book.WAITING) and lanes.live(item) for item in lanes.bookings().values())


def serve(root: Path, *, handover: bool = False, idle_s: float = IDLE_S, ticks: int | None = None,
          clock=time.monotonic, sleep=time.sleep, newest=newest_copy, lanes=None, value_of=None, procs=None,
          groups_io=groups, make_scheduler=None) -> int:
    """`flotilla lane daemon run`: hold the lock and tick until idle, replaced, or stopped."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    fd = os.open(root / LOCK, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        for attempt in range(int(STALE_S / 0.1) if handover else 1):
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if attempt + 1 >= (int(STALE_S / 0.1) if handover else 1):
                    beat = heartbeat(root)
                    print(f"the lane daemon already runs: pid {beat.pid} ({beat.release})" if beat is not None
                          else "the lane daemon already runs")
                    return 0
                time.sleep(0.1)
        return _loop(root, idle_s=idle_s, ticks=ticks, clock=clock, sleep=sleep, newest=newest, lanes=lanes,
                     value_of=value_of, procs=procs, groups_io=groups_io, make_scheduler=make_scheduler)
    finally:
        os.close(fd)


def _loop(root: Path, *, idle_s, ticks, clock, sleep, newest, lanes, value_of, procs, groups_io,
          make_scheduler=None) -> int:
    from flotilla.core.storage import LocalLogStore
    from flotilla.lane import commands, machine
    from flotilla.lane.procs import ProcessTable
    from flotilla.lane.scheduler import Scheduler
    procs = procs or ProcessTable.for_machine()
    lanes = lanes or book.Book(LocalLogStore(root), procs)
    value_of = value_of or commands.value_for({})
    pid = os.getpid()
    mark = procs.start_mark(pid) or ""
    key = _new_key(root)
    sched = (make_scheduler() if make_scheduler is not None else
             Scheduler(lanes, value_of, signer=(pid, mark, key), groups_io=groups_io, clock=clock,
                       default_floor=machine.memory_floor_mb({})))
    state = {"picture": {}, "tick": time.time()}
    guard, stop = threading.Lock(), threading.Event()

    def beat_now():
        with guard:
            _write_beat(root, pid, mark, state["picture"], state["tick"])

    def beater():
        while not stop.wait(BEAT_S):
            try:
                beat_now()
            except OSError:
                pass
    beat_now()
    threading.Thread(target=beater, daemon=True, name="lane daemon heartbeat").start()
    try:
        return _ticks(root, idle_s=idle_s, ticks=ticks, clock=clock, sleep=sleep, newest=newest, lanes=lanes,
                      groups_io=groups_io, sched=sched, state=state, guard=guard, beat_now=beat_now)
    finally:
        stop.set()


def _ticks(root: Path, *, idle_s, ticks, clock, sleep, newest, lanes, groups_io, sched, state, guard,
           beat_now) -> int:

    def leave(number, _frame):
        raise SystemExit(0)
    if threading.current_thread() is threading.main_thread():   # a signal handler is set from the main thread only
        signal.signal(signal.SIGTERM, leave)
    idle_since, errors, done = None, 0, 0
    while True:
        try:
            _actions, picture = sched.step()
            with guard:
                state["picture"], state["tick"] = picture, time.time()
            errors = 0
        except Exception as err:  # noqa: BLE001 - one bad tick is written down; three in a row end the daemon
            errors += 1
            print(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} tick failed: {err!r}", flush=True)
            if errors >= ERRORS_IN_A_ROW:
                lift(root, lanes, groups_io)
                return 1
        beat_now()
        if _busy(lanes):
            idle_since = None
        else:
            idle_since = clock() if idle_since is None else idle_since
            if clock() - idle_since >= idle_s:
                lift(root, lanes, groups_io)
                return 0
        release, where = newest()
        if _version(release) > _version(__version__):
            _spawn(where, root, handover=True)   # it waits for this lock, and keeps the ceilings the journal holds
            return 0
        done += 1
        if ticks is not None and done >= ticks:
            return 0
        stamp, waited = lanes.stamp(), 0.0
        while waited < TICK_S:
            sleep(0.2)
            waited += 0.2
            if lanes.stamp() != stamp:
                break
