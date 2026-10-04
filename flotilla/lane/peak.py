"""The peak memory of a test run: the resident memory of its whole process group, sampled once a second.

A tier is run as `/bin/sh -c <command>` in a new session, so its process group holds the shell, the test runner and
every worker it starts. The sum over the group is what the run asks of the machine; the largest sum seen is the
run's peak, recorded beside its time so fleet sizing can tell how many runs fit (fleet sizing design, section 2.1).
A group that could not be read gives no number: an unknown peak, never zero.
"""

from __future__ import annotations

import os
import subprocess
import threading
from pathlib import Path

SAMPLE = 1.0


def _page_kb() -> int:
    try:
        return os.sysconf("SC_PAGE_SIZE") // 1024
    except (ValueError, OSError, AttributeError):
        return 4


def group_rss_kb(pgid: int, *, proc_root: Path = Path("/proc"), run=subprocess.run,
                 page_kb: int | None = None) -> int | None:
    """The resident memory of every process in group `pgid`, in kB; None when no process of it could be read."""
    if proc_root.is_dir():
        page = page_kb or _page_kb()
        total, seen = 0, False
        for entry in proc_root.iterdir():
            if not entry.name.isdigit():
                continue
            try:
                stat = (entry / "stat").read_text(encoding="utf-8", errors="replace")
                fields = stat[stat.rindex(")") + 2:].split()
                if int(fields[2]) != pgid:
                    continue
                total += int((entry / "statm").read_text(encoding="utf-8").split()[1]) * page
                seen = True
            except (OSError, ValueError, IndexError):
                continue
        return total if seen else None
    try:
        done = run(["ps", "-o", "rss=", "-g", str(pgid)], capture_output=True, text=True, check=False, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    values = [int(word) for word in done.stdout.split() if word.isdigit()]
    return sum(values) if values else None


class GroupPeak:
    """A context manager sampling group `pgid` every `sample` seconds on a daemon thread; `peak_mb` is the largest
    reading in MB (rounded up), or None when nothing could be read."""

    def __init__(self, pgid: int, *, sample: float = SAMPLE, read=None):
        self.pgid, self.sample = pgid, sample
        self.read = read or (lambda group: group_rss_kb(group))
        self.peak_kb: int | None = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                kb = self.read(self.pgid)
            except Exception:  # noqa: BLE001 - a sample that failed measures nothing; the run goes on
                kb = None
            if kb is not None and (self.peak_kb is None or kb > self.peak_kb):
                self.peak_kb = kb
            self._stop.wait(self.sample)

    def __enter__(self) -> "GroupPeak":
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        self._thread.join(timeout=2 * self.sample + 1)

    @property
    def peak_mb(self) -> int | None:
        return None if self.peak_kb is None else -(-self.peak_kb // 1024)
