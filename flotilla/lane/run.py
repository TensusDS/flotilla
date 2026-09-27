"""Running a command under the lane, and saying what it said (spec, sections 9 and 6.9).

The command runs as its own process - never through a pipeline, whose exit code belongs to the last command in
it - and its output is shown as it comes. A process killed by a signal (a negative return code, or 137/143 from a
shell that saw the kill) has no verdict: a suite killed at 8 % prints no failure, and reading it as green records
evidence over code nobody checked. The summary is the last line that counts tests ("212 passed"), or the last line.
"""

from __future__ import annotations

import re
import subprocess
import sys
from collections import deque
from dataclasses import dataclass

SUMMARY = re.compile(r"\b\d+ (?:passed|failed|errors?|skipped|xfailed|xpassed|deselected)\b")
SHELL_KILLS = {137: 9, 143: 15}


@dataclass(frozen=True)
class RunResult:
    exit: int
    verdict: str
    summary: str
    signal: int | None


def summarize(lines) -> str:
    lines = [line.strip() for line in lines if line.strip()]
    for line in reversed(lines):
        if SUMMARY.search(line):
            return line.strip("= ").strip()[:200]
    return lines[-1][:200] if lines else "(no output)"


def verdict_of(returncode: int) -> tuple[str, int | None, int]:
    if returncode < 0:
        return "killed", -returncode, 128 - returncode
    if returncode in SHELL_KILLS:
        return "killed", SHELL_KILLS[returncode], returncode
    return ("green" if returncode == 0 else "red"), None, returncode


def _stop(process) -> None:
    process.terminate()
    try:
        process.wait(5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def execute(command: list[str], *, cwd=None, popen=subprocess.Popen, out=None) -> RunResult:
    out = out if out is not None else sys.stdout   # chosen at call time, so a redirected stdout is honoured
    try:
        process = popen(command, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                        errors="replace", bufsize=1)
    except OSError as err:
        return RunResult(127, "red", f"could not start: {err}", None)
    tail: deque = deque(maxlen=200)
    try:
        for line in process.stdout:
            out.write(line)
            out.flush()
            tail.append(line.rstrip("\n"))
    except BaseException:
        _stop(process)   # the booking is about to be released; the command must not go on computing under it
        raise
    verdict, signal_number, code = verdict_of(process.wait())
    summary = summarize(tail)
    if verdict == "killed":
        summary = f"killed by signal {signal_number} - no verdict (last line: {summary})"
    return RunResult(code, verdict, summary, signal_number)
