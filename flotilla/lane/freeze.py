"""Freezing a run's command so a higher one can have its memory - never killing it (lane shared machine design,
section 4.2).

On Linux with a user systemd the holder starts its command in a transient scope of its own; a freeze writes the
scope's `cgroup.freeze`, then asks `memory.reclaim` for what the waiter needs - not the whole run, since every
gigabyte moved out costs about 13 s on the way back. Elsewhere (macOS, Linux without a user manager) a freeze is a
stop by signal to every process of the command's tree; the memory then leaves only when the system chooses, and the
waiter is admitted only when it shows free.

The holder process has one running command at a time: `slot()` is it. A freeze order acts on it; the command's own
loop asks it whether it is frozen, for how long it has been, and whether the watch that obeys orders is still alive.
"""

from __future__ import annotations

import itertools
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from flotilla.lane import peak

SCOPE, SIGNAL, OFF = "scope", "signal", ""
UNIT_PREFIX = "flotilla-lane-"
CGROUP_ROOT = Path("/sys/fs/cgroup")
PROC_SELF = Path("/proc/self/cgroup")
FROZEN_WAIT_S = 5.0   # how long a freeze waits for the kernel to say the scope is frozen
#: A watch inside one journal call this long while its command is frozen is read as stuck - on the journal's lock,
#: held maybe by a process inside that very command - and the command is thawed (reviews of d4c28c5 and 9d9d244).
#: Only journal calls count: the freeze itself, its reclaim and systemctl never do.
WATCH_STALE_S = 15.0
_probed: dict[tuple, tuple[str, str]] = {}
_scopes = itertools.count(1)   # per process, not per slot: a scope not yet collected keeps its name a while


def _own_cgroup(proc_self: Path) -> str:
    try:
        for line in proc_self.read_text(encoding="utf-8").splitlines():
            if line.startswith("0::"):
                return line[3:].strip()
    except OSError:
        pass
    return ""


def _manager(own: str, uid: int) -> str:
    """The user manager's cgroup: above this process when it runs under the manager, else beside it - a login or
    ssh session lives in `user-<uid>.slice/session-N.scope`, next to `user@<uid>.service`."""
    found = re.match(r"(/.*?/user@\d+\.service)(/|$)", own)
    if found is not None:
        return found.group(1)
    found = re.match(rf"(/.*?/user-{uid}\.slice)(/|$)", own)
    return f"{found.group(1)}/user@{uid}.service" if found is not None else ""


def backend(*, setting: bool = True, platform: str = sys.platform, which=shutil.which, run=subprocess.run,
            proc_self: Path = PROC_SELF, root: Path = CGROUP_ROOT, uid: int | None = None) -> tuple[str, str]:
    """(kind, why): how this machine freezes a run, and the reason, for `flotilla lane` and doctor. The scope probe
    runs once per process: it starts and ends one empty scope."""
    if not setting:
        return OFF, "machine.toml lane_freeze = false"
    if not hasattr(signal, "SIGSTOP"):
        return OFF, "this system has no stop signal"
    if platform == "darwin":
        return SIGNAL, "macOS: a stop by signal"
    if not platform.startswith("linux"):
        return SIGNAL, f"{platform}: a stop by signal"
    if not which("systemd-run") or not which("systemctl"):
        return SIGNAL, "no systemd-run: a stop by signal"
    where = _manager(_own_cgroup(proc_self), os.getuid() if uid is None else uid)
    if not where:
        return SIGNAL, "no user systemd manager: a stop by signal"
    manager = root / where.lstrip("/")
    try:
        controllers = (manager / "cgroup.controllers").read_text(encoding="utf-8").split()
    except OSError:
        controllers = []
    if "memory" not in controllers:
        return SIGNAL, "the user manager has no memory controller: a stop by signal"
    if not (manager / "cgroup.freeze").exists():
        return SIGNAL, "the user manager's cgroup cannot freeze: a stop by signal"
    key = (platform, str(proc_self), str(root)) if run is subprocess.run else None
    if key is not None and key in _probed:
        return _probed[key]
    try:
        done = run(["systemd-run", "--user", "--scope", "--quiet", "--collect", "true"], capture_output=True,
                   text=True, timeout=15, check=False)
        ok = done.returncode == 0
    except (OSError, subprocess.SubprocessError):
        ok = False
    answer = (SCOPE, "Linux user systemd") if ok else (SIGNAL, "a user scope could not be made: a stop by signal")
    if key is not None:
        _probed[key] = answer
    return answer


#: What systemd-run says when it could not make the scope - it then exits without running the command at all.
SCOPE_REFUSED = re.compile(r"^Failed to (start transient scope unit|connect to bus|connect to user scope bus"
                           r"|create bus connection)\b")   # systemd-run's own words, never a command's


def scope_refused(first_line: str) -> bool:
    return bool(SCOPE_REFUSED.match((first_line or "").strip()))


def swap_mb(meminfo: Path = Path("/proc/meminfo"), run=subprocess.run) -> tuple[int, int] | None:
    """(free, total) swap in MB; None when it cannot be read."""
    if meminfo.exists():
        try:
            fields = {}
            for line in meminfo.read_text(encoding="utf-8").splitlines():
                name, _, rest = line.partition(":")
                if name in ("SwapFree", "SwapTotal"):
                    fields[name] = int(rest.split()[0]) // 1024
            return fields["SwapFree"], fields["SwapTotal"]
        except (OSError, ValueError, IndexError, KeyError):
            return None
    try:
        done = run(["sysctl", "-n", "vm.swapusage"], capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    total = re.search(r"total\s*=\s*([\d.]+)M", done.stdout or "")
    free = re.search(r"free\s*=\s*([\d.]+)M", done.stdout or "")
    if done.returncode != 0 or total is None or free is None:
        return None
    return int(float(free.group(1))), int(float(total.group(1)))


def _cgroup_of(unit: str, run, root: Path) -> Path | None:
    try:
        done = run(["systemctl", "--user", "show", "-p", "ControlGroup", "--value", "--", unit], capture_output=True,
                   text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    path = (done.stdout or "").strip()
    if done.returncode != 0 or not path:
        return None
    found = root / path.lstrip("/")
    return found if (found / "cgroup.freeze").exists() else None


def _table() -> dict:
    proc = Path("/proc")
    if proc.is_dir():
        return peak._proc_table(proc, peak._page_kb())
    return peak._ps_table(subprocess.run) or {}


def _signal_tree(pid: int, number: int, table) -> bool:
    members = peak._members(table, pid) if pid in table else set()
    sent = False
    for member in members | {pid}:
        try:
            os.kill(member, number)
            sent = True
        except (ProcessLookupError, PermissionError):
            continue
    try:
        os.killpg(pid, number)   # a member born since the listing, still in the command's own group
        sent = True
    except (ProcessLookupError, PermissionError):
        pass
    return sent


def thaw_unit(unit: str, *, run=subprocess.run, root: Path = CGROUP_ROOT) -> bool:
    """Thaw a scope whose holder is gone or silent. True when it is thawed or gone (nothing left frozen); False when
    systemd could not be asked or the scope would not take the write, so the caller tries again."""
    try:
        done = run(["systemctl", "--user", "show", "-p", "ControlGroup", "--value", "--", unit], capture_output=True,
                   text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return False
    if done.returncode != 0:
        return False
    path = (done.stdout or "").strip()
    cgroup = root / path.lstrip("/") if path else None
    if cgroup is None or not (cgroup / "cgroup.freeze").exists():
        return True   # the scope ended: its command is gone with it
    try:
        (cgroup / "cgroup.freeze").write_text("0", encoding="utf-8")
        return True
    except OSError:
        return False


BOOKING_ENV = "FLOTILLA_LANE_BOOKING"   # set by the lane for every booked command (acquire.ENV)


def _booking_of(entries) -> str | None:
    """The booking an environment names, "" when it names none."""
    for entry in entries:
        if entry.startswith(f"{BOOKING_ENV}="):
            return entry[len(BOOKING_ENV) + 1:]
    return ""


def carries(pid: int, booking: str, *, proc_root: Path = Path("/proc"), run=subprocess.run) -> bool | None:
    """Whether process `pid` runs under lane booking `booking`, by its own environment - the journal, which any
    session writes, cannot say (security review of 0.30.0). True: it names this booking. False: positive proof it is
    not this booking's run - it is gone, or names another booking. None: no word either way (unreadable, or no lane
    variable at all, as after a re-exec with a cleared environment) - never read as "not ours", since that would
    leave a lane command stopped for good (review of 0.30.1, I2)."""
    if proc_root.is_dir():
        try:
            entries = (proc_root / str(int(pid)) / "environ").read_bytes().decode("utf-8", "replace").split("\0")
        except FileNotFoundError:
            return False
        except (OSError, ValueError):
            return None
    else:
        # macOS: `ps -E` appends the environment to the command, for the user's own processes. The command's own
        # words come first and may name a booking too, so only what follows them is read (review of ed148e3, 3)
        try:
            shown = run(["ps", "-wwE", "-p", str(int(pid)), "-o", "command="], capture_output=True, text=True,
                        timeout=5, check=False)
            bare = run(["ps", "-ww", "-p", str(int(pid)), "-o", "command="], capture_output=True, text=True,
                       timeout=5, check=False)
        except (OSError, subprocess.SubprocessError, ValueError):
            return None
        if shown.returncode != 0:
            return False
        full, command = (shown.stdout or "").strip(), (bare.stdout or "").strip()
        if bare.returncode != 0 or not full.startswith(command):
            return None
        entries = full[len(command):].split()
    named = _booking_of(entries)
    if not named:
        return None
    return named == booking


def _state(proc_root: Path, pid: int) -> str | None:
    try:
        stat = (proc_root / str(int(pid)) / "stat").read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return ""
    except (OSError, ValueError):
        return None
    fields = stat[stat.rfind(")") + 2:].split()
    return fields[0] if fields else None


def stopped(pid: int, *, proc_root: Path = Path("/proc"), run=subprocess.run) -> bool | None:
    """Whether process `pid` - the leader a frozen record names - stands stopped (T, or t when a stopped process is
    also traced), or ended a zombie whose group may still hold stopped members. Only the named leader is asked: what
    stands stopped under a running process is not proven the lane's (review of the 0.30.1 follow-up). False when it
    runs or is gone; None when its state cannot be read."""
    if proc_root.is_dir():
        leader = _state(proc_root, pid)
        if leader is None:
            return None
        return leader in ("T", "t", "Z")
    try:
        done = run(["ps", "-o", "stat=", "-p", str(int(pid))], capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    if done.returncode != 0:
        return False
    return (done.stdout or "").strip()[:1] in ("T", "Z")


def thaw_tree(pid: int, *, table=None) -> bool:
    """Continue every process of `pid`'s tree and group after a stop by signal whose holder is gone or silent."""
    return _signal_tree(pid, signal.SIGCONT, table if table is not None else _table())


class Slot:
    """The holder process's one running command - what a freeze order acts on."""

    watcher: threading.Thread | None = None   # set by Watch.start(): the thread that obeys orders
    ceiling_s: float | None = None            # set by Watch.start(): no freeze in force outlasts it
    done_freezing = False                     # let go or past its ceiling: this process freezes this command no more
    journal_since: float | None = None        # when the watch entered the journal call it is in, by this clock

    def __init__(self, kind: str, *, run=subprocess.run, root: Path = CGROUP_ROOT, clock=time.monotonic,
                 proc_self: Path = PROC_SELF, table=None, tree_mb=None):
        self.kind, self.run, self.root, self.clock, self.proc_self = kind, run, root, clock, proc_self
        self.table = table if callable(table) else ((lambda: table) if table is not None else _table)
        self.tree_mb = tree_mb or peak.tree_rss_mb
        self.unit = ""            # the scope of the command running now, when it has one
        self.pid: int | None = None
        self._lock = threading.RLock()
        self._frozen_at: float | None = None
        self._frozen_total = 0.0
        self._how = ""            # how the freeze in force was made: scope or signal
        self._frozen_pid: int | None = None   # the command the freeze in force stopped: thawed even once detached
        self._cgroup: Path | None = None   # the frozen scope's cgroup, kept: a thaw asks systemd nothing
        self.freezes = 0          # how many times it was frozen in this process

    def contain(self, argv: list[str], *, booking: str) -> list[str]:
        """The command to start: in a scope of its own where this machine makes scopes, else as it is. A command
        already inside a lane scope - a nested `lane run` - stays in its outer run's scope and is frozen with it."""
        if self.kind != SCOPE or UNIT_PREFIX in _own_cgroup(self.proc_self):
            self.unit = ""
            return list(argv)
        name = re.sub(r"[^A-Za-z0-9_.-]", "_", booking or "run")[:40]
        self.unit = f"{UNIT_PREFIX}{name}-{os.getpid()}-{next(_scopes)}.scope"
        return ["systemd-run", "--user", "--scope", "--quiet", "--collect", f"--unit={self.unit}", "-p",
                "CPUWeight=100", "--", *argv]

    def lost_scope(self) -> None:
        """The scope could not be made after all (a user manager gone since the probe, a name still taken): this
        process freezes by signal from now on, and the command is started again without a scope."""
        with self._lock:
            self.kind, self.unit = SIGNAL, ""

    def attach(self, pid: int) -> None:
        with self._lock:
            self.pid = pid

    def detach(self) -> None:
        with self._lock:
            self.pid = None
            self.unit = ""

    def freeze(self, reclaim_mb: int) -> dict:
        """Stop the command and move `reclaim_mb` of it to swap. With no command running, the slot is frozen at
        once: the next command waits until it thaws."""
        with self._lock:
            held = self.tree_mb(self.pid) if self.pid is not None else 0
            how, unit = "", ""
            began = self._frozen_at is None
            if began:   # the ceiling counts from here, before the scope freezes and reclaims
                self._frozen_at = self.clock()
            self._frozen_pid = self.pid
            try:
                if self.pid is not None:
                    cgroup = _cgroup_of(self.unit, self.run, self.root) if self.unit and self.kind == SCOPE else None
                    if cgroup is not None:
                        self._freeze_scope(cgroup, reclaim_mb)
                        how, unit, self._cgroup = SCOPE, self.unit, cgroup
                    else:   # no scope, or its scope is gone: the stop still holds the command
                        _signal_tree(self.pid, signal.SIGSTOP, self.table())
                        how = SIGNAL
            except BaseException:
                if began:   # nothing froze: the slot neither says frozen nor counts it (review of 9d9d244)
                    self._frozen_at, self._frozen_pid = None, None
                raise
            if began:
                self.freezes += 1
            self._how = how
            return {"freeze_kind": how or (SIGNAL if self.kind != SCOPE else SCOPE), "unit": unit,
                    "group": self.pid, "held_mb": held or 0}

    def _freeze_scope(self, cgroup: Path, reclaim_mb: int) -> None:
        (cgroup / "cgroup.freeze").write_text("1", encoding="utf-8")
        end = time.monotonic() + FROZEN_WAIT_S
        while time.monotonic() < end:
            try:
                if "frozen 1" in (cgroup / "cgroup.events").read_text(encoding="utf-8"):
                    break
            except OSError:
                break
            time.sleep(0.02)
        if reclaim_mb > 0:
            try:
                (cgroup / "memory.reclaim").write_text(f"{int(reclaim_mb)}M", encoding="utf-8")
            except OSError:   # EAGAIN: it reclaimed less than asked, and that much stays out; any other refusal
                pass          # leaves the run frozen and resident - the waiter then waits for memory to show free

    def thaw(self) -> bool:
        """Thaw the command. False, and still frozen, when the scope would not take the write: the caller tries
        again - a slot never says thawed over a command that is still frozen (final review of 0.30.0, I4)."""
        with self._lock:
            # by what the freeze in force stopped, not by the command attached now: its leader may be gone and the
            # rest of its scope or tree still frozen (review of d4c28c5)
            if self._how == SCOPE:
                cgroup = self._cgroup or (_cgroup_of(self.unit, self.run, self.root) if self.unit else None)
                if cgroup is not None and (cgroup / "cgroup.freeze").exists():
                    try:
                        (cgroup / "cgroup.freeze").write_text("0", encoding="utf-8")
                    except OSError:
                        return False
            elif self._how == SIGNAL and self._frozen_pid is not None:
                _signal_tree(self._frozen_pid, signal.SIGCONT, self.table())
            if self._frozen_at is not None:
                self._frozen_total += max(0.0, self.clock() - self._frozen_at)
                self._frozen_at = None
            self._how, self._cgroup, self._frozen_pid = "", None, None
            return True

    def frozen(self) -> bool:
        return self._frozen_at is not None

    def frozen_for(self) -> float:
        """How long the freeze in force has lasted; 0 when not frozen."""
        with self._lock:
            return max(0.0, self.clock() - self._frozen_at) if self._frozen_at is not None else 0.0

    def frozen_seconds(self) -> float:
        """All the time this slot has been frozen in this process, the freeze in force included."""
        with self._lock:
            now = self._frozen_total
            if self._frozen_at is not None:
                now += max(0.0, self.clock() - self._frozen_at)
            return now

    def wait_thawed(self, sleep=time.sleep) -> None:
        """A new command never starts while the slot is frozen."""
        while self.frozen():
            self.guard()
            sleep(0.2)

    def guard(self) -> bool:
        """Thaw when frozen and the watch that would thaw it is gone or stuck, or the freeze in force has reached its
        ceiling: the command's own loop asks this about every second, with no journal and no lock, so neither a dead
        watch nor one blocked on the journal's lock - held, maybe, by a process inside this very command - leaves it
        frozen (reviews of f32d91c and d4c28c5). True when it thawed."""
        watcher = self.watcher
        if self.frozen() and watcher is not None and not watcher.is_alive():
            return self.thaw()
        since = self.journal_since
        if self.frozen() and watcher is not None and since is not None and self.clock() - since > WATCH_STALE_S:
            self.done_freezing = True   # a watch stuck that long cannot be trusted to thaw it: never again
            return self.thaw()
        if self.frozen() and self.ceiling_s is not None and self.frozen_for() >= self.ceiling_s:
            self.done_freezing = True
            return self.thaw()
        return False


SLOT: Slot | None = None


def _setting() -> bool:
    try:
        from flotilla.lane import commands
        reader = getattr(commands, "freeze_on", None)
        return bool(reader()) if reader is not None else True
    except Exception:  # noqa: BLE001 - an unreadable setting is the default: freezing on
        return True


def slot() -> Slot:
    """This process's slot, made on first use with this machine's way of freezing."""
    global SLOT
    if SLOT is None:
        SLOT = Slot(backend(setting=_setting())[0])
    return SLOT
