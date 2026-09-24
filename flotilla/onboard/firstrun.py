"""The first run of every chosen test tier (spec, section 4.2 step 3).

A tier that is not green is not recorded as working: no time is saved for it, and the caller shows
the tail. A run is killed as a process group on timeout, because a shell's child can hold the output
pipe open long after the shell itself is gone. Measured times are machine facts and live in the
state directory, never in the project (plan decision 1). The lane will book the machine for these
runs once it exists (plan decision 3).
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path

from flotilla.onboard.tomlw import render_toml

_SUMMARY = re.compile(r"\b\d+ passed\b|^test result: |^ok\s|\bTests?:\s+\d+")
TAIL_LINES = 20
ESCAPE_GRACE = 3


@dataclass(frozen=True)
class TierRun:
    name: str
    status: str
    seconds: float | None
    summary: str | None
    tail: str


def _tail(text: str) -> str:
    return "\n".join(text.rstrip().splitlines()[-TAIL_LINES:])


def _summary(text: str) -> str | None:
    lines = [line.strip() for line in text.splitlines() if _SUMMARY.search(line.strip())]
    return lines[-1] if lines else None


def run_tier(name: str, command: str, cwd: Path, *, timeout: float) -> TierRun:
    started = time.monotonic()
    # stdin is closed so a tier that reads it gets end-of-file instead of waiting out the timeout; output
    # that is not UTF-8 is replaced, never a crash.
    proc = subprocess.Popen(["/bin/sh", "-c", command], cwd=cwd, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace", start_new_session=True)
    try:
        output, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        try:
            output, _ = proc.communicate(timeout=ESCAPE_GRACE)
        except subprocess.TimeoutExpired:
            # A descendant left the process group (setsid) and still holds the pipe: stop listening.
            proc.stdout.close()
            proc.wait()
            output = ""
        return TierRun(name, "timed-out", None, None, _tail(output or ""))
    seconds = round(time.monotonic() - started, 2)
    output = output or ""
    if proc.returncode < 0:
        status = "killed"
    elif proc.returncode == 0:
        status = "green"
    else:
        status = "red"
    return TierRun(name, status, seconds if status == "green" else None, _summary(output), _tail(output))


def _path(state: Path, repo_key: str) -> Path:
    return state / "measurements" / f"{repo_key}.toml"


def save_measurements(state: Path, repo_key: str, runs: list[TierRun]) -> Path:
    path = _path(state, repo_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"seconds": {run.name: run.seconds for run in runs if run.status == "green"}}
    path.write_text(render_toml(data, header="Tier run times on this machine, green runs only."), encoding="utf-8")
    return path


def load_measurements(state: Path, repo_key: str) -> dict[str, float]:
    try:
        data = tomllib.loads(_path(state, repo_key).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    return dict(data.get("seconds") or {})
