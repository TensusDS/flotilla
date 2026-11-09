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

from flotilla.lane import admission, book, estimate, groups, policy, tick

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
                 default_floor, total_mb=None):
        self.lanes, self.value_of, self.signer, self.groups = lanes, value_of, signer, groups_io
        self.clock, self.default_floor = clock, default_floor
        self.total_mb = total_mb or _total_mb
        self.history = History()
        self._paths: dict[tuple[str, str], object] = {}
        self.last_machine: policy.Machine | None = None
        # the ceilings this daemon decided, and the runs it squeezed: never the journal's word, which any session
        # writes (final review C1). A run first seen in a scope - after a restart - takes its group's ceiling, clamped as
        # it will be written (`_written`).
        self.ceilings: dict[str, int | None] = {}
        self.squeezed: set[str] = set()

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
            lanes._rescue(tx, lanes._found(tx), value)
        found = lanes.bookings()
        now, moment = self.clock(), lanes._moment()
        live = {item.id: item for item in found.values() if item.state in (book.HELD, book.WAITING)
                and lanes.live(item)}
        default = self.default_floor() if callable(self.default_floor) else self.default_floor
        floor = policy.clamp_floor(max((item.floor_mb for item in live.values() if item.floor_mb is not None),
                                       default=default), self.total_mb())
        available = value.available_mb()
        value = dataclasses.replace(value, available_mb=lambda: available, floor_mb=floor)
        runs, machine = admission.view(found, estimate.history(found, now=moment), value, now=moment,
                                       live=lanes.live, mine="")
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
        }
        return actions, picture
