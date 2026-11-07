"""A held run's cgroup, as the lane daemon reads it and sets its ceiling (lane daemon design, 4.2 and 4.3 b).

Every session writes the journal, and a unit's name reaches a file the daemon writes, so a group is answered only
for a lane scope this user's manager holds, named as the journal names it, with a process inside that carries the
booking in its own environment. Anything else answers None, and the daemon writes nothing there.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from flotilla.lane import book, freeze


@dataclass(frozen=True)
class Reading:
    current_mb: int
    high_mb: int | None        # None: "max"
    high_events: int           # memory.events "high": how often the kernel held it at its ceiling
    pressure: float | None     # memory.pressure "some avg10", percent; None when the file is absent
    refault_mb: int = 0        # memory.stat workingset_refault_anon: its own pages read back from swap, all told


def cgroup_of(unit: str, booking: str, *, run=subprocess.run, root: Path = freeze.CGROUP_ROOT,
              proc_root: Path = Path("/proc"), own: str | None = None, uid: int | None = None) -> Path | None:
    """The cgroup of `booking`'s scope `unit`, or None (module docstring)."""
    if not isinstance(unit, str) or not book.UNIT.fullmatch(unit):
        return None
    manager = freeze._manager(freeze._own_cgroup(freeze.PROC_SELF) if own is None else own,
                              os.getuid() if uid is None else uid)
    if not manager:
        return None
    try:
        done = run(["systemctl", "--user", "show", "-p", "ControlGroup", "--value", "--", unit], capture_output=True,
                   text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    shown = (done.stdout or "").strip()
    if done.returncode != 0 or not shown.startswith(manager + "/") or shown.rsplit("/", 1)[-1] != unit \
            or ".." in shown.split("/"):
        return None
    path = Path(root) / shown.lstrip("/")
    if not (path / "memory.high").is_file():
        return None
    try:
        pids = [int(line) for line in (path / "cgroup.procs").read_text(encoding="utf-8").split()]
    except (OSError, ValueError):
        return None
    if not any(freeze.carries(pid, booking, proc_root=proc_root) is True for pid in pids):
        return None
    return path


def _mb(text: str) -> int | None:
    text = text.strip()
    return None if text == "max" else int(text) // (1024 * 1024)


def read(path: Path) -> Reading | None:
    """The group's memory now, its ceiling, how often it was held there, its pressure, and how much of its own memory
    it has read back from swap; None when it is gone."""
    try:
        current = int((path / "memory.current").read_text(encoding="utf-8").strip()) // (1024 * 1024)
        high = _mb((path / "memory.high").read_text(encoding="utf-8"))
        events = dict(line.split()[:2] for line in (path / "memory.events").read_text(encoding="utf-8").splitlines()
                      if len(line.split()) >= 2)
    except (OSError, ValueError):
        return None
    pressure = None
    try:
        for line in (path / "memory.pressure").read_text(encoding="utf-8").splitlines():
            if line.startswith("some "):
                fields = dict(part.split("=", 1) for part in line.split()[1:] if "=" in part)
                pressure = float(fields["avg10"])
    except (OSError, ValueError, KeyError):
        pressure = None
    try:
        hits = int(events.get("high", 0))
    except ValueError:
        hits = 0
    refault = 0
    try:
        for line in (path / "memory.stat").read_text(encoding="utf-8").splitlines():
            name, _, number = line.partition(" ")
            if name == "workingset_refault_anon":
                refault = int(number) * 4096 // (1024 * 1024)
    except (OSError, ValueError):
        refault = 0
    return Reading(current, high, hits, pressure, refault)


def set_high(path: Path, mb: int | None) -> bool:
    """Set the soft ceiling: megabytes, or "max" to lift it. Never `memory.max`: the kernel kills at that one."""
    try:
        (path / "memory.high").write_text("max" if mb is None else f"{int(mb)}M", encoding="utf-8")
        return True
    except OSError:
        return False
