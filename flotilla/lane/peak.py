"""The peak memory of a test run: the resident memory of its whole process group, sampled once a second.

A tier is run as `/bin/sh -c <command>` in a new session, so its process group holds the shell, the test runner and
every worker it starts. The sum over the group is what the run asks of the machine; the largest sum seen is the
run's peak, recorded beside its time so fleet sizing can tell how many runs fit (fleet sizing design, section 2.1).
A group that could not be read gives no number: an unknown peak, never zero. Processes outside this machine's
process tree - a container a tier starts through a daemon - are not counted.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

SAMPLE = 1.0
COST_SHARE = 20   # wait at least this many times the CPU the last read took
MAX_STRETCH = 5   # and never longer than this many samples


def _page_kb() -> int:
    try:
        return os.sysconf("SC_PAGE_SIZE") // 1024
    except (ValueError, OSError, AttributeError):
        return 4


def _members(table, pgid: int) -> set[int]:
    """The group's processes and every descendant of any of them, whatever group it moved to: Playwright starts
    Chromium detached, in its own group, and the browser is the heaviest part of an e2e run (review of 0.7.14, C1)."""
    children: dict[int, list[int]] = {}
    for pid, (ppid, _pgrp, _kb) in table.items():
        children.setdefault(ppid, []).append(pid)
    todo = [pid for pid, (_ppid, pgrp, _kb) in table.items() if pgrp == pgid]
    if pgid in table:
        todo.append(pgid)
    seen: set[int] = set()
    while todo:
        pid = todo.pop()
        if pid in seen:
            continue
        seen.add(pid)
        todo.extend(children.get(pid, ()))
    return seen


def _proc_table(proc_root: Path, page: int) -> dict[int, tuple[int, int, int]]:
    table = {}
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            stat = (entry / "stat").read_text(encoding="utf-8", errors="replace")
            fields = stat[stat.rindex(")") + 2:].split()
            rss = int((entry / "statm").read_text(encoding="utf-8").split()[1]) * page
            table[int(entry.name)] = (int(fields[1]), int(fields[2]), rss)
        except (OSError, ValueError, IndexError):
            continue   # a process that exited between the listing and the read
    return table


def _ps_table(run) -> dict[int, tuple[int, int, int]] | None:
    """One portable listing (`-A`, columns by name): `ps -g` selects a session on procps, not a group."""
    try:
        done = run(["ps", "-A", "-o", "pid=,ppid=,pgid=,rss="], capture_output=True, text=True, check=False,
                   timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    table = {}
    for line in done.stdout.splitlines():
        words = line.split()
        if len(words) == 4 and all(word.isdigit() for word in words):
            pid, ppid, pgrp, rss = map(int, words)
            table[pid] = (ppid, pgrp, rss)
    return table


def group_rss_kb(pgid: int, *, proc_root: Path = Path("/proc"), run=subprocess.run,
                 page_kb: int | None = None) -> int | None:
    """The resident memory, in kB, of every process in group `pgid` and of their descendants; None when none of
    them could be read."""
    if proc_root.is_dir():
        table = _proc_table(proc_root, page_kb or _page_kb())
    else:
        table = _ps_table(run)
        if table is None:
            return None
    members = _members(table, pgid) & table.keys()
    return sum(table[pid][2] for pid in members) if members else None


def children_max_rss_kb() -> int | None:
    """The largest resident set of any child this process reaped, in kB (bytes on macOS); None where unknown."""
    try:
        import resource
        value = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    except (ImportError, OSError, ValueError):
        return None
    return value // 1024 if sys.platform == "darwin" else value


class GroupPeak:
    """A context manager sampling group `pgid` every `sample` seconds on a daemon thread; `peak_mb` is the largest
    reading in MB (rounded up), or None when nothing could be read. The largest child the kernel reaped during the
    run backs the samples: a tier shorter than a sample, or a spike between two. A sampler that cannot start
    measures nothing and never fails the tier it measures (review of 0.7.14)."""

    def __init__(self, pgid: int, *, sample: float = SAMPLE, read=None, rusage=None):
        self.pgid, self.sample = pgid, sample
        self.read = read or (lambda group: group_rss_kb(group))
        self.rusage = rusage or children_max_rss_kb
        self.peak_kb: int | None = None
        self._before: int | None = None
        self._started = False
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def _loop(self) -> None:
        while not self._stop.is_set():
            began = time.thread_time()
            try:
                kb = self.read(self.pgid)
            except Exception:  # noqa: BLE001 - a sample that failed measures nothing; the run goes on
                kb = None
            self._keep(kb)
            # a read that cost much CPU (thousands of processes) stretches the wait so the sampler keeps near 5% of a
            # core - CPU time, not wall time, so a loaded machine does not stretch it; and never past five samples, so
            # a gap never swallows a whole short tier (review of 0.7.14)
            cost = time.thread_time() - began
            self._stop.wait(min(MAX_STRETCH * self.sample, max(self.sample, COST_SHARE * cost)))

    def _keep(self, kb) -> None:
        if kb is not None and (self.peak_kb is None or kb > self.peak_kb):
            self.peak_kb = kb

    def __enter__(self) -> "GroupPeak":
        try:
            self._before = self.rusage()
            self._thread.start()
            self._started = True
        except Exception:  # noqa: BLE001 - `can't start new thread` under memory pressure: unknown, not a failure
            self._started = False
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        if not self._started:
            return
        self._thread.join(timeout=2 * self.sample + 1)
        try:
            after = self.rusage()
        except Exception:  # noqa: BLE001
            after = None
        if after is not None and self._before is not None and after > self._before:
            self._keep(after)   # a new largest child appeared during this run: it was this run's

    @property
    def peak_mb(self) -> int | None:
        return None if self.peak_kb is None else -(-self.peak_kb // 1024)
