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


TABLES = ("seconds", "peak_mb")


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _clean(data) -> dict:
    """Only the tables this module writes, and only numbers in them: a hand-edited `peak_mb = 5` or `unit = "slow"`
    is dropped, never a TypeError out of the receipt it rides on (review of 0.7.14)."""
    out = {}
    for table in TABLES:
        values = data.get(table) if isinstance(data, dict) else None
        if isinstance(values, dict):
            out[table] = {str(name): value for name, value in values.items() if _number(value)}
    return out


def _read(state: Path, repo_key: str) -> tuple[dict, str]:
    """(the clean tables, why the file could not be read or "")."""
    path = _path(state, repo_key)
    try:
        return _clean(tomllib.loads(path.read_text(encoding="utf-8"))), ""
    except FileNotFoundError:
        return {}, ""
    except (OSError, ValueError) as err:   # TOMLDecodeError and UnicodeDecodeError are ValueErrors
        return {}, f"the measurements file {path} could not be read ({err.__class__.__name__}: {err})"


def measurements_problem(state: Path, repo_key: str) -> str:
    """Why this machine's measurements file could not be read, or "": an unreadable file is not "never measured"."""
    return _read(state, repo_key)[1]


def _write_all(state: Path, repo_key: str, data: dict) -> Path:
    """The whole file, staged and renamed, so a reader never sees half."""
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


def _update(state: Path, repo_key: str, change) -> None:
    """Read, change and write the file under a lock, so two receipts finishing together do not lose each other's
    peak. A file that cannot be read is this machine's cache gone bad: the writer starts again from nothing, and
    any green run repairs it (review of 0.7.14). `change(data)` returns whether it changed anything."""
    path = _path(state, repo_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_name(f".{path.name}.lock"), "a+") as lock:
        try:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX)
        except (ImportError, OSError):
            pass   # no advisory locks here: the staged rename still keeps the file whole
        data, problem = _read(state, repo_key)
        if change(data) or problem:
            _write_all(state, repo_key, data)


def save_measurements(state: Path, repo_key: str, runs: list[TierRun]) -> Path:
    """Onboarding's run: its times replace the table; its peaks join the worst seen, as every receipt's do."""
    def change(data):
        data["seconds"] = {run.name: run.seconds for run in runs if run.status == "green"}
        peaks = dict(data.get("peak_mb") or {})
        for run in runs:
            if run.status == "green" and run.peak_mb is not None:
                peaks[run.name] = max(run.peak_mb, peaks.get(run.name, 0))
        if peaks:
            data["peak_mb"] = peaks
        return True
    _update(state, repo_key, change)
    return _path(state, repo_key)


def measure_once(state: Path, repo_key: str, times: dict[str, float]) -> None:
    """Record a tier's green time where this machine has none yet (twosuns field test of 0.6.7, W2): a receipt is
    the first green run a machine sees after the one onboarding made elsewhere, and a measurement already there -
    onboarding's - stands. A side effect: a failed write measures nothing and never costs the receipt it rides on
    (review of 0.6.8, I1)."""
    def change(data):
        known = data.get("seconds") or {}
        new = {name: seconds for name, seconds in times.items() if name not in known and seconds is not None}
        data["seconds"] = {**known, **new}
        return bool(new)
    try:
        _update(state, repo_key, change)
    except (OSError, ValueError, TypeError):
        pass


def measure_peaks(state: Path, repo_key: str, peaks: dict[str, int | None]) -> None:
    """Record each tier's peak memory, keeping the larger of what is there and what was just measured: fleet sizing
    asks how much one run can take, and the worst run seen answers that. Unknown peaks (None) record nothing. Like
    `measure_once`, a side effect that never costs the receipt it rides on."""
    def change(data):
        merged = dict(data.get("peak_mb") or {})
        moved = False
        for name, mb in peaks.items():
            if _number(mb) and mb > merged.get(name, -1):
                merged[name], moved = mb, True
        data["peak_mb"] = merged
        return moved
    try:
        _update(state, repo_key, change)
    except (OSError, ValueError, TypeError):
        pass


def load_peaks(state: Path, repo_key: str) -> dict[str, int]:
    """Each tier's largest measured peak memory in MB on this machine; a tier never measured is absent."""
    return dict(_read(state, repo_key)[0].get("peak_mb") or {})


def load_measurements(state: Path, repo_key: str) -> dict[str, float]:
    """Each tier's green time on this machine; {} when there is none or the file cannot be read
    (`measurements_problem` says which)."""
    return dict(_read(state, repo_key)[0].get("seconds") or {})
