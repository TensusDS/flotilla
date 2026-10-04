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

from flotilla.core.text import strip_ansi
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
    exit: int | None = None   # the command's exit code; None when it was stopped for its time
    peak_mb: int | None = None   # the process group's largest resident memory, green runs only (fleet sizing)


def _tail(text: str) -> str:
    return strip_ansi("\n".join(text.rstrip().splitlines()[-TAIL_LINES:]))


def _summary(text: str) -> str | None:
    lines = [line.strip() for line in strip_ansi(text).splitlines() if _SUMMARY.search(line.strip())]
    return lines[-1] if lines else None


def run_tier(name: str, command: str, cwd: Path, *, timeout: float) -> TierRun:
    started = time.monotonic()
    # stdin is closed so a tier that reads it gets end-of-file instead of waiting out the timeout; output
    # that is not UTF-8 is replaced, never a crash.
    proc = subprocess.Popen(["/bin/sh", "-c", command], cwd=cwd, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace", start_new_session=True)
    from flotilla.lane.peak import GroupPeak
    sampler = GroupPeak(proc.pid)
    try:
        with sampler:
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
    except BaseException:
        # stopped (`flotilla lane stop`, the session ending): the tier's whole group goes with it, never left
        # computing unbooked (review of 0.6.2, I3)
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            proc.wait(timeout=ESCAPE_GRACE)   # reaped, not left a zombie: a shell that execs (macOS's) made it ours
        except subprocess.TimeoutExpired:
            pass
        raise
    seconds = round(time.monotonic() - started, 2)
    output = output or ""
    if proc.returncode < 0:
        status = "killed"
    elif proc.returncode == 0:
        status = "green"
    else:
        status = "red"
    return TierRun(name, status, seconds if status == "green" else None, _summary(output), _tail(output),
                   proc.returncode, sampler.peak_mb if status == "green" else None)


def _path(state: Path, repo_key: str) -> Path:
    return state / "measurements" / f"{repo_key}.toml"


HEADER = "Tier run times and peak memory on this machine, green runs only."


def _load_all(state: Path, repo_key: str) -> dict:
    try:
        return tomllib.loads(_path(state, repo_key).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def _write_all(state: Path, repo_key: str, data: dict) -> Path:
    """The whole file, staged and renamed: each writer keeps the tables it does not own (`seconds`, `peak_mb`)."""
    path = _path(state, repo_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        staged.write_text(render_toml(data, header=HEADER), encoding="utf-8")
        os.replace(staged, path)
    except BaseException:
        staged.unlink(missing_ok=True)
        raise
    return path


def save_measurements(state: Path, repo_key: str, runs: list[TierRun]) -> Path:
    data = _load_all(state, repo_key)
    data["seconds"] = {run.name: run.seconds for run in runs if run.status == "green"}
    peaks = {run.name: run.peak_mb for run in runs if run.status == "green" and run.peak_mb is not None}
    if peaks:
        data["peak_mb"] = {**dict(data.get("peak_mb") or {}), **peaks}
    path = _write_all(state, repo_key, data)
    return path


def measure_once(state: Path, repo_key: str, times: dict[str, float]) -> None:
    """Record a tier's green time where this machine has none yet (twosuns field test of 0.6.7, W2): a receipt is
    the first green run a machine sees after the one onboarding made elsewhere, and a measurement already there -
    onboarding's - stands. A side effect: an unreadable file or a failed write measures nothing and never costs the
    receipt it rides on (review of 0.6.8, I1). Written whole through a staged file, so a reader never sees half; the
    peak table rides along untouched."""
    try:
        data = _load_all(state, repo_key)
        known = dict(data.get("seconds") or {})
        new = {name: seconds for name, seconds in times.items() if name not in known and seconds is not None}
        if not new:
            return
        data["seconds"] = {**known, **new}
        _write_all(state, repo_key, data)
    except (OSError, ValueError):
        pass


def measure_peaks(state: Path, repo_key: str, peaks: dict[str, int | None]) -> None:
    """Record each tier's peak memory, keeping the larger of what is there and what was just measured: fleet sizing
    asks how much one run can take, and the worst run seen answers that. Unknown peaks (None) record nothing. Like
    `measure_once`, a side effect that never costs the receipt it rides on."""
    try:
        data = _load_all(state, repo_key)
        known = dict(data.get("peak_mb") or {})
        merged = dict(known)
        for name, mb in peaks.items():
            if mb is not None and (name not in merged or mb > merged[name]):
                merged[name] = mb
        if merged == known:
            return
        data["peak_mb"] = merged
        _write_all(state, repo_key, data)
    except (OSError, ValueError):
        pass


def load_peaks(state: Path, repo_key: str) -> dict[str, int]:
    """Each tier's largest measured peak memory in MB on this machine; a tier never measured is absent."""
    try:
        return dict(_load_all(state, repo_key).get("peak_mb") or {})
    except (OSError, ValueError):
        return {}


def load_measurements(state: Path, repo_key: str) -> dict[str, float]:
    try:
        data = tomllib.loads(_path(state, repo_key).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    return dict(data.get("seconds") or {})
