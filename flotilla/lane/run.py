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


def _stop(process) -> None:
    _signal_group(process, signals.SIGTERM)
    try:
        process.wait(5)
    except subprocess.TimeoutExpired:
        _signal_group(process, signals.SIGKILL)
        process.wait()


def execute(command: list[str], *, cwd=None, popen=subprocess.Popen, out=None, ceiling: float | None = None) -> RunResult:
    """Run `command` in its own process group. Past `ceiling` seconds the group is stopped and the run has no verdict
    (worldcore field test W21: one looping test held the machine's lane for 28 minutes)."""
    out = out if out is not None else sys.stdout   # chosen at call time, so a redirected stdout is honoured
    try:
        process = popen(command, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                        errors="replace", bufsize=1, start_new_session=True)
    except OSError as err:
        return RunResult(127, "red", f"could not start: {err}", None)
    measurer = Measurer(process.pid).__enter__()
    tail: deque = deque(maxlen=200)
    over = threading.Event()

    def stop_at_ceiling():
        over.set()
        _stop(process)
    timer = threading.Timer(ceiling, stop_at_ceiling) if ceiling else None
    if timer is not None:
        timer.daemon = True
        timer.start()
    try:
        for line in process.stdout:
            out.write(line)
            out.flush()
            tail.append(line.rstrip("\n"))
    except BaseException:
        _stop(process)   # the booking is about to be released; the command must not go on computing under it
        measurer.__exit__(None, None, None)
        raise
    finally:
        if timer is not None:
            timer.cancel()
    verdict, signal_number, code = verdict_of(process.wait())
    measurer.__exit__(None, None, None)
    usage = measurer.usage
    summary = summarize(tail)
    if over.is_set():
        return RunResult(code if code else 124, "killed",
                         f"stopped at the ceiling of {ceiling:g} s - no verdict (last line: {summary})", signal_number, usage, True)
    if verdict == "killed":
        summary = f"killed by signal {signal_number} - no verdict (last line: {summary})"
    return RunResult(code, verdict, summary, signal_number, usage)
