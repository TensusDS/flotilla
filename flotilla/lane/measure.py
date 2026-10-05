"""What a run took: its wall time, the cores it used, its peak memory, and how busy the machine was meanwhile
(lane admission design, section 2). Cores are CPU seconds over wall seconds - what the run received, which under
contention is less than what it would use, so each sample carries the machine's busy share and estimates take only
unsaturated ones. Every figure that could not be read is None: unknown, never zero."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

PROC_STAT = Path("/proc/stat")


@dataclass(frozen=True)
class CpuTimes:
    busy: int
    total: int


@dataclass(frozen=True)
class Usage:
    seconds: float
    peak_mb: int | None
    cores: float | None
    busy: float | None


def cpu_times(path: Path = PROC_STAT) -> CpuTimes | None:
    """The machine's busy and total jiffies from the first line of /proc/stat (idle and iowait are not busy)."""
    try:
        first = path.read_text(encoding="utf-8").splitlines()[0].split()
        values = [int(value) for value in first[1:]]
    except (OSError, ValueError, IndexError):
        return None
    if not first or first[0] != "cpu" or len(values) < 5:
        return None
    total = sum(values[:8])
    return CpuTimes(total - values[3] - values[4], total)


def busy_share(before: CpuTimes | None, after: CpuTimes | None) -> float | None:
    if before is None or after is None or after.total <= before.total:
        return None
    return round((after.busy - before.busy) / (after.total - before.total), 3)


def children_cpu_seconds() -> float | None:
    """User and system CPU seconds of every child this process reaped."""
    try:
        import resource
        usage = resource.getrusage(resource.RUSAGE_CHILDREN)
    except (ImportError, OSError, ValueError):
        return None
    return usage.ru_utime + usage.ru_stime


class Measurer:
    """Measures the run of process group `pgid` between enter and exit. The run must be reaped before exit, so its
    CPU time reaches RUSAGE_CHILDREN. Like the peak sampler it holds, it never fails the run it measures."""

    def __init__(self, pgid: int, *, clock=time.monotonic, cpu=children_cpu_seconds, stat=cpu_times, peak=None):
        from flotilla.lane.peak import GroupPeak
        self.clock, self.cpu, self.stat = clock, cpu, stat
        self.peak = peak if peak is not None else GroupPeak(pgid)
        self.usage: Usage | None = None
        self._done = False

    def __enter__(self) -> "Measurer":
        self._began, self._cpu, self._stat = self.clock(), self.cpu(), self.stat()
        self.peak.__enter__()
        return self

    def __exit__(self, *exc) -> None:
        if self._done:
            return
        self._done = True
        self.peak.__exit__(*exc)
        seconds = max(0.0, self.clock() - self._began)
        cpu_after = self.cpu()
        cores = None
        if self._cpu is not None and cpu_after is not None and seconds > 0:
            cores = round(max(0.0, cpu_after - self._cpu) / seconds, 2)
        self.usage = Usage(round(seconds, 2), self.peak.peak_mb, cores, busy_share(self._stat, self.stat()))
