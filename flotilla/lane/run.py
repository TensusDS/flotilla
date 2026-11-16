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


def _group_left(process) -> bool:
    """Whether anything of the command's group is still there. The leader is reaped first: an unreaped leader
    would keep the group alive by itself."""
    process.poll()
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


def _pipe_holders(inode: int, proc: str = "/proc") -> set[int] | None:
    """The processes other than this one that hold the pipe `inode` open - the run's output - read from their
    descriptors (`/proc/<pid>/fd`, this user's processes); None where there is no /proc to read (macOS)."""
    if not os.path.isdir(proc):
        return None
    want, found = f"pipe:[{inode}]", set()
    for name in os.listdir(proc):
        if not name.isdigit() or int(name) == os.getpid():
            continue
        try:
            for fd in os.listdir(f"{proc}/{name}/fd"):
                if os.readlink(f"{proc}/{name}/fd/{fd}") == want:
                    found.add(int(name))
                    break
        except OSError:   # gone meanwhile, or not ours to read
            continue
    return found


def end_orphans(inode: int, *, grace: float = 5.0, proc: str = "/proc") -> int | None:
    """End the processes outside the command's group that still hold its output: only those - a server the run
    started and that closed its output is left alone. SIGTERM, then SIGKILL after `grace`. How many there were;
    None where they cannot be found."""
    first = _pipe_holders(inode, proc)
    if first is None:
        return None
    for pid in first:
        try:
            os.kill(pid, signals.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
    end = time.monotonic() + grace
    while time.monotonic() < end and (_pipe_holders(inode, proc) or set()) & first:
        time.sleep(0.05)
    for pid in (_pipe_holders(inode, proc) or set()) & first:
        try:
            os.kill(pid, signals.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    return len(first)


def execute(command: list[str], *, cwd=None, popen=subprocess.Popen, out=None, ceiling: float | None = None) -> RunResult:
    """Run `command` in its own process group. Past `ceiling` seconds the group is stopped and the run has no verdict
    (beta field test W21: one looping test held the machine's lane for 28 minutes)."""
    out = out if out is not None else sys.stdout   # chosen at call time, so a redirected stdout is honoured
    from flotilla.lane import acquire, freeze
    booking = os.environ.get(acquire.ENV, "")
    held = freeze.slot() if booking else None   # under a booking the command can be frozen for a higher run
    asked = list(command)
    if held is not None:
        held.wait_thawed()
        command = held.contain(command, booking=booking)
    scoped = held is not None and bool(held.unit)
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
    try:
        inode = os.fstat(process.stdout.fileno()).st_ino   # the output pipe: an orphan holding it is found by it
    except (OSError, AttributeError, ValueError):
        inode = None
    eof, forward = threading.Event(), threading.Event()
    forward.set()   # cleared once the run is let go: an orphan's later output is not the run's
    failed: list[BaseException] = []   # the reader's own failure (the output's reader gone): it stops the run

    def read():
        try:
            for line in process.stdout:
                if not forward.is_set():
                    continue
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
            if over.is_set() and keeper is not None and not keeper.is_alive():
                break   # stopped at the ceiling and still "there": a member SIGKILL cannot end (D state, another user)
        if not eof.wait(OUTPUT_GRACE_S):   # its group is gone or past help, and its output still open
            orphans = end_orphans(inode) if inode is not None else None
            if not eof.wait(OUTPUT_GRACE_S):
                orphans = None   # held by what could not be found or ended here; the run is let go all the same
        forward.clear()
        check()
    except BaseException:
        _stop(process)   # the booking is about to be released; the command must not go on computing under it
        forward.clear()
        if inode is not None:
            end_orphans(inode)
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
        return execute(asked, cwd=cwd, popen=popen, out=out, ceiling=ceiling)
    usage = measurer.usage
    summary = summarize(list(tail))
    frozen = round(frozen_since(), 2)
    freezes = getattr(held, "freezes", 0) - freezes_before
    if over.is_set():
        return RunResult(code if code else 124, "killed",
                         f"stopped at the ceiling of {ceiling:g} s - no verdict (last line: {summary})", signal_number,
                         usage, True, frozen, freezes, orphans)
    if verdict == "killed":
        summary = f"killed by signal {signal_number} - no verdict (last line: {summary})"
    return RunResult(code, verdict, summary, signal_number, usage, False, frozen, freezes, orphans)
