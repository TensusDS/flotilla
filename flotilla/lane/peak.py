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


def _tree(table, root: int) -> set[int]:
    """`root` and every descendant of it, whatever group each is in; empty when `root` is not in the table."""
    if root not in table:
        return set()
    children: dict[int, list[int]] = {}
    for pid, (ppid, _pgrp, _kb) in table.items():
        children.setdefault(ppid, []).append(pid)
    seen, todo = set(), [root]
    while todo:
        pid = todo.pop()
        if pid not in seen:
            seen.add(pid)
            todo.extend(children.get(pid, ()))
    return seen


def _tick() -> int:
    try:
        return os.sysconf("SC_CLK_TCK")
    except (ValueError, OSError, AttributeError):
        return 100


def _proc_table(proc_root: Path, page: int, cpu: dict | None = None,
                tick: int = 100) -> dict[int, tuple[int, int, int]]:
    """pid -> (ppid, pgrp, kB); with `cpu`, also fills pid -> CPU seconds, its own and its reaped children's
    (stat fields 14-17), so a worker that finished inside the tree still counts through its parent."""
    table = {}
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            stat = (entry / "stat").read_text(encoding="utf-8", errors="replace")
            fields = stat[stat.rindex(")") + 2:].split()
            rss = int((entry / "statm").read_text(encoding="utf-8").split()[1]) * page
            table[int(entry.name)] = (int(fields[1]), int(fields[2]), rss)
            if cpu is not None:
                cpu[int(entry.name)] = sum(int(value) for value in fields[11:15]) / tick
        except (OSError, ValueError, IndexError):
            continue   # a process that exited between the listing and the read
    return table


def _clock_seconds(text: str) -> float | None:
    """`ps -o time=`: [[dd-]hh:]mm:ss, fractions allowed."""
    days, _, rest = text.rpartition("-")
    try:
        parts = [float(part) for part in rest.split(":")]
        whole = sum(value * 60 ** at for at, value in enumerate(reversed(parts)))
        return whole + (int(days) * 86400 if days else 0)
    except ValueError:
        return None


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


def _ps_usage(run) -> tuple[dict, dict] | None:
    """The `ps` listing with each process's CPU time, where there is no /proc (macOS)."""
    try:
        done = run(["ps", "-A", "-o", "pid=,ppid=,pgid=,rss=,time="], capture_output=True, text=True, check=False,
                   timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    table, cpu = {}, {}
    for line in done.stdout.splitlines():
        words = line.split()
        if len(words) == 5 and all(word.isdigit() for word in words[:4]):
            seconds = _clock_seconds(words[4])
            pid, ppid, pgrp, rss = map(int, words[:4])
            table[pid] = (ppid, pgrp, rss)
            if seconds is not None:
                cpu[pid] = seconds
    return table, cpu


def group_usage(pgid: int, *, proc_root: Path = Path("/proc"), run=subprocess.run, page_kb: int | None = None,
                tick: int | None = None) -> tuple[int | None, float | None] | None:
    """(kB, CPU seconds) of group `pgid` and its descendants, as `group_rss_kb` counts them; None when none could be
    read. The CPU seconds are what the members alive now have used, so a rate is taken between two readings."""
    if proc_root.is_dir():
        cpu: dict = {}
        table = _proc_table(proc_root, page_kb or _page_kb(), cpu, tick or _tick())
    else:
        found = _ps_usage(run)
        if found is None:
            return None
        table, cpu = found
    members = _members(table, pgid) & table.keys()
    if not members:
        return None
    used = [cpu[pid] for pid in members if pid in cpu]
    return sum(table[pid][2] for pid in members), (sum(used) if used else None)


def tree_rss_mb(pid: int, *, proc_root: Path = Path("/proc"), run=subprocess.run,
                page_kb: int | None = None) -> int | None:
    """The resident memory, in MB rounded up, of `pid` and every descendant of it: what a holder - the wrapper and
    the command under it - holds now. Its group is not counted: a wrapper may share the session's group."""
    if proc_root.is_dir():
        table = _proc_table(proc_root, page_kb or _page_kb())
    else:
        table = _ps_table(run)
        if table is None:
            return None
    members = _tree(table, pid)
    return -(-sum(table[member][2] for member in members) // 1024) if members else None


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

    def __init__(self, pgid: int, *, sample: float = SAMPLE, read=None, rusage=None, sampler=None,
                 clock=time.monotonic):
        self.pgid, self.sample, self.clock = pgid, sample, clock
        # `read` measures memory alone (kB); `sampler` measures (kB, CPU seconds) and so also the busiest stretch
        if sampler is None and read is not None:
            sampler = lambda group: (read(group), None)   # noqa: E731
        self.sampler = sampler or (lambda group: group_usage(group))
        self.rusage = rusage or children_max_rss_kb
        self.peak_kb: int | None = None
        self.peak_cores: float | None = None
        self._last: tuple[float, float] | None = None   # (time, CPU seconds) of the reading a rate is taken from
        self._before: int | None = None
        self._started = False
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def _loop(self) -> None:
        while not self._stop.is_set():
            began = time.thread_time()
            self._take()
            # a read that cost much CPU (thousands of processes) stretches the wait so the sampler keeps near 5% of a
            # core - CPU time, not wall time, so a loaded machine does not stretch it; and never past five samples, so
            # a gap never swallows a whole short tier (review of 0.7.14)
            cost = time.thread_time() - began
            self._stop.wait(min(MAX_STRETCH * self.sample, max(self.sample, COST_SHARE * cost)))

    def _take(self) -> None:
        """One sample: the memory now, and the cores since the last reading at least a sample ago - the busiest
        stretch, which the run's average hides (a suite that runs eight workers for a minute of its ten)."""
        try:
            reading = self.sampler(self.pgid)
            now = self.clock()
        except Exception:  # noqa: BLE001 - a sample that failed measures nothing; the run goes on
            return
        kb, cpu = reading if reading is not None else (None, None)
        self._keep(kb)
        if cpu is None:
            return
        if self._last is None:
            self._last = (now, cpu)
            return
        then, before = self._last
        if now - then < self.sample:
            return
        self._last = (now, cpu)
        if cpu >= before:   # a member that left the tree takes its seconds with it: no rate from that pair
            cores = round((cpu - before) / (now - then), 2)
            if self.peak_cores is None or cores > self.peak_cores:
                self.peak_cores = cores

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
