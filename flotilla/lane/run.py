"""Running a command under the lane, and saying what it said (spec, sections 9 and 6.9).

The command runs as its own process - never through a pipeline, whose exit code belongs to the last command in
it - and its output is shown as it comes. A process killed by a signal (a negative return code, or 137/143 from a
shell that saw the kill) has no verdict: a suite killed at 8 % prints no failure, and reading it as green records
evidence over code nobody checked. The summary is the last line that counts tests ("212 passed"), or the last line that is not a shell's own error
(`bash: kill: ... No such process` from a teardown), or the last line.
"""

from __future__ import annotations

import os
import re
import signal as signals
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass

from flotilla.core.text import strip_ansi
from flotilla.lane.measure import Measurer, Usage

SUMMARY = re.compile(r"\b\d+ (?:passed|failed|errors?|skipped|xfailed|xpassed|deselected)\b")
WRAPPER = re.compile(r"^(?:ba|z|)sh: |^kill: |^/bin/(?:ba)?sh: ")   # a shell's own error, not the command's
SHELL_KILLS = {137: 9, 143: 15}


@dataclass(frozen=True)
class RunResult:
    exit: int
    verdict: str
    summary: str
    signal: int | None
    usage: Usage | None = None   # what the run took (lane admission, stage 1); None when it never started
    at_ceiling: bool = False     # stopped at the ceiling: its time is a lower bound, not a full run
    frozen_s: float = 0.0        # how long it stood frozen for a higher run (lane shared machine)
    freezes: int = 0
    orphans: int | None = 0      # processes outside the command's group still holding its output, ended by the
                                 # lane; None: the output stayed open and what held it could not be found


def summarize(lines) -> str:
    lines = [strip_ansi(line).strip() for line in lines if strip_ansi(line).strip()]
    for line in reversed(lines):
        if SUMMARY.search(line):
            return line.strip("= ").strip()[:200]
    said = [line for line in lines if not WRAPPER.search(line)]
    return (said or lines)[-1][:200] if lines else "(no output)"


def verdict_of(returncode: int) -> tuple[str, int | None, int]:
    if returncode < 0:
        return "killed", -returncode, 128 - returncode
    if returncode in SHELL_KILLS:
        return "killed", SHELL_KILLS[returncode], returncode
    return ("green" if returncode == 0 else "red"), None, returncode


def _signal_group(process, number) -> None:
    try:
        os.killpg(process.pid, number)   # the command and everything it started: a test runner's workers too
    except (ProcessLookupError, PermissionError):
        pass


#: Whether this process reaps the command's orphans (`_subreaper`): then a member of the group that died after its
#: parent is a zombie of this process, and would keep the group "left" for ever unless collected here.
_REAPING = False


def _reap_strays(leader: int, proc: str = "/proc") -> None:
    """Collect the dead children of this process other than the command itself, whose exit status is the run's
    verdict and is Popen's to collect. Only while this process is the orphans' reaper: otherwise its other
    children are their owners' to wait for."""
    if not _REAPING:
        return
    me = os.getpid()
    try:
        names = os.listdir(proc)
    except OSError:
        return
    for name in names:
        if not name.isdigit() or int(name) == leader:
            continue
        try:
            with open(f"{proc}/{name}/stat", encoding="utf-8", errors="replace") as handle:
                rest = handle.read().rsplit(")", 1)[1].split()
        except (OSError, IndexError):
            continue
        if len(rest) > 1 and rest[0] == "Z" and rest[1] == str(me):
            try:
                os.waitpid(int(name), os.WNOHANG)
            except (ChildProcessError, OSError):
                continue


def _group_left(process) -> bool:
    """Whether anything of the command's group is still there. The leader is reaped first: an unreaped leader
    would keep the group alive by itself - and so would a member re-parented here and dead (`_reap_strays`)."""
    process.poll()
    _reap_strays(process.pid)
    try:
        os.killpg(process.pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:   # a member that changed its user: still there, and no longer ours to signal
        return True
    return True


def _stop(process) -> None:
    """SIGTERM to the group, then SIGKILL to whatever of it is left after 5 s. The leader's death is no sign the
    group is gone: a shell leader dies at once, while a member that holds SIGTERM - a test runner whose output went
    to a file - computed on after the lane said stopped (2026-10-15). A frozen command is thawed first: a frozen
    group holds its signals until it thaws, and would sit out the grace and the SIGKILL in swap."""
    from flotilla.lane import freeze
    held = freeze.SLOT
    if held is not None and held.frozen():
        held.thaw()
    _signal_group(process, signals.SIGTERM)
    end = time.monotonic() + 5
    try:
        while _group_left(process) and time.monotonic() < end:
            time.sleep(0.05)
    finally:   # a second signal cutting the grace short means: now
        if _group_left(process):
            _signal_group(process, signals.SIGKILL)
    process.wait()


#: How long the output may stay open once the command's group is gone: a child that left the group (a Unity
#: editor started in a session of its own) can hold it for ever, and a reader waiting on it held a run in the lane
#: for 7 hours past its ceiling (field notes, 2026-11-16).
OUTPUT_GRACE_S = 3.0
PR_SET_CHILD_SUBREAPER = 36


def _subreaper() -> bool:
    """Make this process the reaper of the command's orphans (Linux): a child that leaves the group and loses its
    parent is re-parented here, not to init, so it can still be found and ended. False where it cannot be."""
    global _REAPING
    if not sys.platform.startswith("linux"):
        return False
    try:
        import ctypes
        libc = ctypes.CDLL(None, use_errno=True)
        _REAPING = libc.prctl(PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) == 0
    except (OSError, AttributeError):
        return False
    return _REAPING


def _descendants(root_pid: int, proc: str = "/proc") -> set[int]:
    """Every live process below `root_pid`, read from /proc; empty where there is no /proc."""
    parents: dict[int, int] = {}
    try:
        names = os.listdir(proc)
    except OSError:
        return set()
    for name in names:
        if not name.isdigit():
            continue
        try:
            with open(f"{proc}/{name}/stat", encoding="utf-8", errors="replace") as handle:
                rest = handle.read().rsplit(")", 1)[1].split()
            parents[int(name)] = int(rest[1])
        except (OSError, IndexError, ValueError):
            continue
    found, frontier = set(), {root_pid}
    while frontier:
        frontier = {pid for pid, parent in parents.items() if parent in frontier and pid not in found}
        found |= frontier
    return found


def _scope_members(unit: str) -> set[int]:
    """The processes in the run's systemd scope, whatever group or session they moved to."""
    if not unit:
        return set()
    from flotilla.lane import freeze
    cgroup = freeze._cgroup_of(unit, subprocess.run, freeze.CGROUP_ROOT)
    if cgroup is None:
        return set()
    try:
        return {int(word) for word in (cgroup / "cgroup.procs").read_text(encoding="utf-8").split() if word.isdigit()}
    except (OSError, ValueError):
        return set()


def end_orphans(unit: str = "", *, reaper: bool = False, grace: float = 5.0) -> int:
    """End what is left of a run outside its process group: the processes in its scope, and - with this process
    their reaper - every descendant still here. SIGTERM, then SIGKILL after `grace`. How many there were."""
    def left() -> set[int]:
        found = _scope_members(unit) | (_descendants(os.getpid()) if reaper else set())
        found.discard(os.getpid())
        return found
    first = left()
    for pid in first:
        try:
            os.kill(pid, signals.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
    end = time.monotonic() + grace
    while time.monotonic() < end:
        _reap(first)
        if not left():
            break
        time.sleep(0.05)
    last = left()
    for pid in last:
        try:
            os.kill(pid, signals.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    _reap(first | last)
    return len(first)


def _reap(pids) -> None:
    """Collect the orphans ended here that were re-parented to this process, so none stays a zombie - those pids
    only: another child of this process is its owner's to wait for."""
    for pid in pids:
        try:
            os.waitpid(pid, os.WNOHANG)
        except (ChildProcessError, OSError):
            continue


def execute(command: list[str], *, cwd=None, popen=subprocess.Popen, out=None, ceiling: float | None = None,
            reap_orphans: bool = False) -> RunResult:
    """Run `command` in its own process group. Past `ceiling` seconds the group is stopped and the run has no verdict
    (beta field test W21: one looping test held the machine's lane for 28 minutes). `reap_orphans` makes this
    process the reaper of the command's orphans, for the rest of its life: `flotilla lane run`, which ends with the
    run, asks it; a longer-lived caller would collect every orphan of its other children."""
    out = out if out is not None else sys.stdout   # chosen at call time, so a redirected stdout is honoured
    from flotilla.lane import acquire, freeze
    booking = os.environ.get(acquire.ENV, "")
    held = freeze.slot() if booking else None   # under a booking the command can be frozen for a higher run
    asked = list(command)
    if held is not None:
        held.wait_thawed()
        command = held.contain(command, booking=booking)
    scoped = held is not None and bool(held.unit)
    unit = held.unit if held is not None else ""
    reaper = _subreaper() if reap_orphans else False
    try:
        process = popen(command, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                        errors="replace", bufsize=1, start_new_session=True)
    except OSError as err:
        return RunResult(127, "red", f"could not start: {err}", None)
    frozen_before = held.frozen_seconds() if held is not None else 0.0
    freezes_before = getattr(held, "freezes", 0)
    if held is not None:
        held.attach(process.pid)
    measurer = Measurer(process.pid, paused=held.frozen_seconds if held is not None else (lambda: 0.0)).__enter__()
    tail: deque = deque(maxlen=200)
    first: list[str] = []
    over, ended = threading.Event(), threading.Event()
    began = time.monotonic()

    def frozen_since() -> float:
        return held.frozen_seconds() - frozen_before if held is not None else 0.0

    def keep():
        """The ceiling counts run time, not frozen time; and a watch that died never leaves the command frozen."""
        while not ended.wait(0.5):
            if held is not None:
                held.guard()
            if ceiling and time.monotonic() - began - frozen_since() >= ceiling:
                over.set()
                _stop(process)
                return
    keeper = threading.Thread(target=keep, daemon=True) if ceiling or held is not None else None
    if keeper is not None:
        keeper.start()
    eof = threading.Event()
    failed: list[BaseException] = []   # the reader's own failure (the output's reader gone): it stops the run

    def read():
        try:
            for line in process.stdout:
                out.write(line)
                out.flush()
                tail.append(line.rstrip("\n"))
                if not first:
                    first.append(line)
        except BaseException as err:  # noqa: BLE001 - handed to the main thread, which stops the command and raises it
            failed.append(err)
        finally:
            eof.set()

    def check():
        if failed:
            raise failed[0]
    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    orphans: int | None = 0
    try:
        while process.poll() is None:   # a signal meanwhile (`lane stop`) stops the group, as the ceiling does
            check()
            time.sleep(0.05)
        status = process.returncode
        while not eof.wait(0.2) and _group_left(process):
            check()   # the group's own members may still write: they are the lane's to wait for, and the ceiling's
        if not eof.wait(OUTPUT_GRACE_S):   # the group is gone and its output is still open: something outside holds it
            orphans = end_orphans(unit, reaper=reaper)
            if not eof.wait(OUTPUT_GRACE_S):
                orphans = None   # held by what could not be found here; the run is let go all the same
        check()
    except BaseException:
        _stop(process)   # the booking is about to be released; the command must not go on computing under it
        end_orphans(unit, reaper=reaper)
        measurer.__exit__(None, None, None)
        raise
    finally:
        ended.set()
        if keeper is not None and over.is_set():   # the ceiling's stop may still be waiting the group out: the lane
            keeper.join()                          # is not let go first
        if held is not None:
            held.detach()
    verdict, signal_number, code = verdict_of(status)
    measurer.__exit__(None, None, None)
    if scoped and status != 0 and first and freeze.scope_refused(first[0]):
        # systemd-run refused the scope and ran nothing: the command runs now, freezable by signal (review focus 1)
        held.lost_scope()
        out.write("lane: the run's scope could not be made; running it without a scope, freezable by signal\n")
        out.flush()
        return execute(asked, cwd=cwd, popen=popen, out=out, ceiling=ceiling, reap_orphans=reap_orphans)
    usage = measurer.usage
    summary = summarize(tail)
    frozen = round(frozen_since(), 2)
    freezes = getattr(held, "freezes", 0) - freezes_before
    if over.is_set():
        return RunResult(code if code else 124, "killed",
                         f"stopped at the ceiling of {ceiling:g} s - no verdict (last line: {summary})", signal_number,
                         usage, True, frozen, freezes, orphans)
    if verdict == "killed":
        summary = f"killed by signal {signal_number} - no verdict (last line: {summary})"
    return RunResult(code, verdict, summary, signal_number, usage, False, frozen, freezes, orphans)
