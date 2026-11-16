"""One tick of the lane daemon: read the journal, the machine and every held run's group, decide, apply (lane daemon
design, 4.2).

`tick` is pure; what it needs of time - how long a run has been pressed, how long the machine has been under its
floor, how much a run grew over the last minute - is kept here, in `History`, between ticks. The decisions are
applied in one journal transaction, signed with the daemon's pid and start mark; then every scoped run's
`memory.high` is set to what the journal says, so a restarted daemon reconciles and a receipt's next scope gets its
booking's ceiling.
"""

from __future__ import annotations

import collections
import dataclasses
import datetime as dt
import time

from flotilla.lane import admission, book, estimate, freeze, groups, peak, policy, tick

#: Why a booking whose client process is gone is ended by the daemon (field notes 2026-11-16: Claude Code killed
#: lane clients under memory pressure, and their runs vanished with no trace on the row).
LOST = ("its client process was gone before it released the booking - killed, most often by Claude Code under "
        "memory pressure or by its session ending; no verdict")
#: A held run "looks hung" (the person's ruling, 2026-11-16: marked and told, never stopped by the lane) when it has
#: run HUNG_TIMES its estimate, and at least HUNG_FLOOR_S, and used under HUNG_CORES of a core for HUNG_QUIET_S.
HUNG_TIMES, HUNG_FLOOR_S, HUNG_QUIET_S, HUNG_CORES = 10, 600.0, 600.0, 0.05
#: How often the daemon reads the held runs' CPU time: a /proc listing each time.
CPU_EVERY_S = 15.0


def client_gone(procs, pid: int, mark: str) -> bool:
    """Whether a lane client is provably gone: no process under its pid, another process there (another start
    mark), or a zombie of it. A look that cannot answer is no proof."""
    if not hasattr(procs, "exists") or not hasattr(procs, "start_mark"):
        return False   # a table that cannot say
    there = procs.exists(pid)
    if there is False:
        return True
    if not there:
        return False
    if hasattr(procs, "zombie") and procs.zombie(pid) is True and procs.start_mark(pid) == mark:
        return True   # its own process, ended and not yet reaped - read, never inferred from a failed look
    found = procs.start_mark(pid)
    return found is not None and found != mark


def _estimate_s(item, hist) -> float | None:
    """What the run is estimated to take, in seconds, or None when nothing estimates it."""
    return estimate.for_booking(item, hist).seconds


def _cpu_table():
    """(pid -> (ppid, pgrp, 0), pid -> its own CPU seconds, pid -> its reaped children's), or (None, None, None)
    where there is no /proc to read. Own and children's apart: a run's client is counted by its children only, so
    its own sampling and polling never hide a hang (review of dba1899, 8)."""
    import os
    proc = "/proc"
    if not os.path.isdir(proc):
        return None, None, None
    tick = peak._tick()
    table, own, kids = {}, {}, {}
    for name in os.listdir(proc):
        if not name.isdigit():
            continue
        try:
            with open(f"{proc}/{name}/stat", encoding="utf-8", errors="replace") as handle:
                fields = handle.read().rsplit(")", 1)[1].split()
            pid = int(name)
            table[pid] = (int(fields[1]), int(fields[2]), 0)
            own[pid] = (int(fields[11]) + int(fields[12])) / tick
            kids[pid] = (int(fields[13]) + int(fields[14])) / tick
        except (OSError, IndexError, ValueError):
            continue
    return table, own, kids

GROWTH_WINDOW_S = 60.0


class History:
    """What a pure tick cannot keep (module docstring)."""

    def __init__(self):
        self._runs: dict[str, dict] = {}
        self._under_since: float | None = None

    def run(self, now: float, booking: str, reading: groups.Reading | None, ceiling: int | None) -> dict:
        seen = self._runs.setdefault(booking, {"events": None, "pressed_since": None, "samples": collections.deque(),
                                                "peak": 0, "refault": None, "at": None})
        if reading is None:
            seen["pressed_since"] = None
            return {"at_ceiling": False, "pressure": None, "pressed_s": 0.0, "grew_mb": 0, "peak_mb": seen["peak"]}
        at = seen["events"] is not None and reading.high_events > seen["events"]
        seen["events"] = reading.high_events
        # pressed is hot: held at its ceiling and reading its own pages back from swap. Pressure alone does not tell
        # it from a cold run pushing pages out (the bench of 2026-11-07: hot 13-19 %, cold up to 17 % while it fills)
        rate = 0.0
        if seen["refault"] is not None and seen["at"] is not None and now > seen["at"]:
            rate = max(0, reading.refault_mb - seen["refault"]) / (now - seen["at"])
        seen["refault"], seen["at"] = reading.refault_mb, now
        pressed = at and ceiling is not None and rate >= tick.REFAULT_MB_S
        if not pressed:
            seen["pressed_since"] = None
        elif seen["pressed_since"] is None:
            seen["pressed_since"] = now
        samples = seen["samples"]
        samples.append((now, reading.current_mb))
        while len(samples) > 1 and samples[1][0] <= now - GROWTH_WINDOW_S:
            samples.popleft()
        seen["peak"] = max(seen["peak"], reading.current_mb)
        return {"at_ceiling": at, "pressure": reading.pressure,
                "pressed_s": now - seen["pressed_since"] if seen["pressed_since"] is not None else 0.0,
                "grew_mb": max(0, reading.current_mb - samples[0][1]), "peak_mb": seen["peak"]}

    def machine(self, now: float, available: int | None, floor: int) -> float:
        """Seconds MemAvailable has stood below the floor; 0 above it or unknown."""
        if available is None or available >= floor:
            self._under_since = None
            return 0.0
        if self._under_since is None:
            self._under_since = now
        return now - self._under_since

    def forget(self, live: set[str]) -> None:
        for booking in [b for b in self._runs if b not in live]:
            del self._runs[booking]


def _total_mb() -> int | None:
    from flotilla.lane import machine
    found = machine._memtotal()
    return found // 1024 if found else None


def _seconds_since(text: str, now: dt.datetime) -> float:
    moment = book._when(text)
    return max(0.0, (now - moment).total_seconds()) if moment is not None else 0.0


def _written(mb: int | None) -> int | None:
    """A ceiling as the daemon writes it: never one that starves (MIN_CEILING_MB); None lifts it to max."""
    return None if mb is None else max(policy.MIN_CEILING_MB, int(mb))


class Scheduler:
    def __init__(self, lanes, value_of, *, signer: tuple, groups_io=groups, clock=time.monotonic,
                 default_floor, total_mb=None, share="fifo"):
        self.lanes, self.value_of, self.signer, self.groups = lanes, value_of, signer, groups_io
        # machine.toml `lane_share`: a callable is read each tick, so a change holds from the next one
        self.clock, self.default_floor, self.share = clock, default_floor, share
        self.total_mb = total_mb or _total_mb
        self.history = History()
        self._paths: dict[tuple[str, str], object] = {}
        self.last_machine: policy.Machine | None = None
        # the ceilings this daemon decided, and the runs it squeezed: never the journal's word, which any session
        # writes (final review C1). A run first seen in a scope - after a restart - takes its group's ceiling, clamped as
        # it will be written (`_written`).
        self.ceilings: dict[str, int | None] = {}
        self.squeezed: set[str] = set()
        self.cpu_seen: dict[str, collections.deque] = {}   # a held run's (clock, CPU seconds), HUNG_QUIET_S of them
        self.watched: dict[str, tuple[int, str]] = {}   # held runs this daemon saw live: their client's pid and mark
        self._cpu_at: float | None = None
        self.hung: dict[str, dict] = {}

    def _path(self, item):
        key = (item.id, item.unit)
        if key not in self._paths:
            found = self.groups.cgroup_of(item.unit, item.id)
            if found is None:
                return None   # asked again next tick: the scope may not be made yet
            self._paths[key] = found
        return self._paths[key]

    def step(self) -> tuple[tick.Actions, dict]:
        lanes = self.lanes
        value = self.value_of(None)
        with lanes.store.transaction(book.KEY) as tx:
            lanes._rescue(tx, lanes._found(tx), value)   # a dead frozen holder: thawed, released cut, killed
        released = set(getattr(lanes, "rescued", ()))
        found = lanes.bookings()
        if any(item.state in (book.HELD, book.WAITING) and self._gone(item) for item in found.values()):
            # its client was killed: nobody releases it, nobody reads its verdict
            released |= {item.id for item in lanes.sweep(gone=self._gone, why=LOST) if item.state == book.RELEASED}
            found = lanes.bookings()
        self._end_lost(found, released)
        now, moment = self.clock(), lanes._moment()
        live = {item.id: item for item in found.values() if item.state in (book.HELD, book.WAITING)
                and lanes.live(item)}
        default = self.default_floor() if callable(self.default_floor) else self.default_floor
        floor = policy.clamp_floor(max((item.floor_mb for item in live.values() if item.floor_mb is not None),
                                       default=default), self.total_mb())
        available = value.available_mb()
        value = dataclasses.replace(value, available_mb=lambda: available, floor_mb=floor)
        share = self.share() if callable(self.share) else self.share
        hist = estimate.history(found, now=moment)
        runs, machine = admission.view(found, hist, value, now=moment, live=lanes.live, mine="", share=share)
        self.hung = self._hung(found, runs, hist, now)
        paths, readings, enriched = {}, {}, []
        for run in runs:
            item = found[run.id]
            if item.state == book.WAITING and item.held_back:
                continue
            path = self._path(item) if item.state == book.HELD and item.unit else None
            reading = self.groups.read(path) if path is not None else None
            paths[run.id], readings[run.id] = path, reading
            if item.state == book.HELD and run.id not in self.ceilings and reading is not None:
                self.ceilings[run.id] = _written(reading.high_mb)   # adopted as it will be written (review of bd42f9c)
            ceiling = self.ceilings.get(run.id) if item.state == book.HELD else None
            seen = self.history.run(now, run.id, reading, ceiling) if item.state == book.HELD else {}
            frozen = item.frozen == "frozen"
            # a ceiling decided and not read back - a write that failed, a file set back - holds nothing
            written = _written(ceiling)
            bound = written is None or (reading is not None and reading.high_mb is not None
                                        and abs(reading.high_mb - written) <= 1)
            enriched.append(dataclasses.replace(
                run, mem_mb=reading.current_mb if reading is not None and run.state != policy.WAITING else run.mem_mb,
                ceiling_mb=ceiling, scoped=path is not None, bound=bound, at_ceiling=seen.get("at_ceiling", False),
                pressure=seen.get("pressure"), pressed_s=seen.get("pressed_s", 0.0), grew_mb=seen.get("grew_mb", 0),
                peak_mb=seen.get("peak_mb", 0), squeezed=run.id in self.squeezed,
                cause=item.order_cause or (policy.WAITER if item.order_by else ""),
                frozen_s=_seconds_since(item.frozen_at, moment) if frozen else 0.0, held_freezes=item.held_freezes,
                reclaimed_mb=(item.reclaim_mb or 0) if frozen else 0, rule=item.rule or 0))
        machine = dataclasses.replace(machine, available_mb=available, floor_mb=floor,
                                      over_s=self.history.machine(now, available, floor))
        self.last_machine = machine
        actions = tick.tick(enriched, machine)
        applied = set(lanes.apply(actions, self.signer))
        for booking, mb in actions.ceilings:
            if booking in applied:
                self.ceilings[booking] = mb
        self.squeezed |= {b for b in actions.squeezed if b in applied}
        # a squeeze the daemon ended is over whether or not a line had to be written: a line any session appends may
        # have said so first (review of b6173cd); one no longer held leaves through `held` all the same
        self.squeezed -= set(actions.restored)
        after = lanes.bookings()
        for booking, path in paths.items():
            item = after.get(booking)
            if path is None or item is None or item.state != book.HELD or booking not in self.ceilings:
                continue
            want = _written(self.ceilings[booking])
            reading = readings[booking]
            if reading is None or reading.high_mb != want:
                self.groups.set_high(path, want)
        self.history.forget(set(live))
        self._paths = {key: path for key, path in self._paths.items() if key[0] in live}
        held = {b for b, item in live.items() if item.state == book.HELD}
        self.ceilings = {b: mb for b, mb in self.ceilings.items() if b in held or b in applied}
        self.squeezed &= held | applied
        pressed = {r.id: r.pressed_s >= tick.PRESSED_S for r in enriched}
        under = available is not None and available < floor
        picture = {
            "why": dict(actions.why),
            "groups": {b: {"ceiling_mb": self.ceilings.get(b), "current_mb": readings[b].current_mb,
                           "pressure": readings[b].pressure, "squeezed": b in self.squeezed,
                           "pressed": pressed.get(b, False)}
                       for b in readings if readings[b] is not None and b in after},
            "over": {"mb": floor - available, "s": machine.over_s, "nothing": actions.over_mb} if under else {},
            "hung": {b: found_ for b, found_ in self.hung.items() if b in after and after[b].state == book.HELD},
        }
        return actions, picture

    def _gone(self, item) -> bool:
        """Whether a booking's client is provably gone - no longer there, or another process under its pid. A look
        that cannot answer is no proof: `flotilla lane` keeps the same rule, so one failed look never releases a
        live holder (review of dba1899, 10)."""
        if item.pid is None or not item.mark:
            return False
        procs = self.lanes.procs
        return client_gone(procs, item.pid, item.mark)

    def _end_lost(self, found, released: set) -> None:
        """End what is left of the runs this daemon saw live and itself released this tick - by the sweep or by the
        rescue of a frozen one (review of dba1899, 3) - by the processes' own word (`freeze.end_booking`); and
        remember the live ones. Neither a run this daemon never saw live nor one released by a journal line is
        ended: a forged "cut" right after a normal release would end the servers that run left (review of 972db77,
        D). A lost run first seen by a new daemon - a handover, a restart - is released and told, not ended."""
        root = getattr(self.lanes.store, "root", "")
        for booking, (pid, mark) in list(self.watched.items()):
            item = found.get(booking)
            if item is None or item.state != book.HELD:
                self.watched.pop(booking, None)
                if booking in released:
                    freeze.end_booking(booking, pid, mark, root, self.lanes.procs)
        for booking, item in found.items():
            if item.state == book.HELD and item.pid is not None and item.mark and self.lanes.live(item):
                self.watched.setdefault(booking, (item.pid, item.mark))

    def _hung(self, found, runs, hist, now: float) -> dict[str, dict]:
        """The held runs that look hung: long past their estimate and computing next to nothing. Read every
        CPU_EVERY_S; between, the last finding stands."""
        held = {run.id: run for run in runs if found[run.id].state == book.HELD and found[run.id].pid}
        for gone in set(self.cpu_seen) - set(held):
            self.cpu_seen.pop(gone, None)
        if self._cpu_at is not None and now - self._cpu_at < CPU_EVERY_S:
            return {b: v for b, v in self.hung.items() if b in held}
        self._cpu_at = now
        table, own, kids = _cpu_table() if held else (None, None, None)
        if table is None:
            return {}
        out = {}
        for booking, run in held.items():
            item = found[booking]
            members = peak._tree(table, item.pid) if item.pid in table else set()
            if not members or item.frozen == "frozen":
                self.cpu_seen.pop(booking, None)   # gone from the table, or frozen: its quiet is no hang
                continue
            used = kids.get(item.pid, 0.0) + sum(own.get(pid, 0.0) + kids.get(pid, 0.0)
                                                 for pid in members if pid != item.pid)
            samples = self.cpu_seen.setdefault(booking, collections.deque())
            samples.append((now, used))
            while len(samples) > 1 and now - samples[1][0] >= HUNG_QUIET_S:
                samples.popleft()
            first_at, first_used = samples[0]
            quiet_s = now - first_at
            if quiet_s < HUNG_QUIET_S:
                continue
            rate = max(0.0, used - first_used) / quiet_s
            seconds = _estimate_s(item, hist)
            floor = max(HUNG_FLOOR_S, HUNG_TIMES * seconds if seconds else 0.0)
            if run.ran_s >= floor and rate < HUNG_CORES:
                out[booking] = {"ran_s": round(run.ran_s), "estimate_s": round(seconds) if seconds else None,
                                "cores": round(rate, 3), "quiet_s": round(quiet_s), "who": item.who,
                                "note": item.note, "run_for": item.run_for, "project": item.project}
        return out
