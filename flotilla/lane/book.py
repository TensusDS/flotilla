"""The lane's booking log: who holds the machine for a long run, who waits, in what order (spec, section 9).

Not a lock, and said so: nothing stops a peer from running a suite without asking. The log makes "is the machine
busy" survive the session that answered it. It lives in the state directory, one per machine, because the machine
is the resource. It is an append-only event log folded into bookings, under a file lock.

A booking held by a process (`lane run`, a receipt) carries that process's pid and start mark; a booking taken by
hand carries neither and stays until released by hand. Nothing expires by the clock: time is a guess about work,
not an observation of it. `sweep` removes only bookings whose process is gone. Waiters are served in the order they
came, and a waiter whose process died is skipped and swept.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from flotilla.core.text import visible

KEY = "lane"
HELD, RELEASED, WAITING, EXPIRED, LEFT = "held", "released", "waiting", "expired", "left"
#: The admission rule a booking waits under. Stage 2 admits by budget only while every live booking carries this rule
#: or a later one: a seat on an older plugin counts slots and knows nothing of reservations (lane admission, section 3).
RULE = 1
FIELDS = ("who", "note", "pid", "mark", "run_for", "why", "command", "ladder", "will_run", "project", "rule",
          "seconds", "peak_mb", "cores", "busy", "verdict", "ran", "cut")
MEASURED = ("seconds", "peak_mb", "cores", "busy", "verdict", "ran")
LISTS = ("ladder", "will_run")
#: What a caller wrote: another session's text, made visible where the log is read, so no place that prints a
#: booking can forge a line or move the cursor (scan of 0.7.0, F2).
TEXT = ("who", "note", "run_for", "why", "command", "project")


@dataclass
class Booking:
    id: str
    who: str = ""
    note: str = ""
    state: str = ""
    pid: int | None = None
    mark: str = ""
    run_for: str = ""
    since: str = ""
    ended: str = ""
    why: str = ""
    command: str = ""                                # the full command, the note cut to 80 is not (stage 1)
    ladder: list = field(default_factory=list)       # its signatures, exact to coarse
    will_run: list = field(default_factory=list)     # a receipt's tiers that will run
    project: str = ""
    rule: int | None = None                          # None: booked by a flotilla before stage 1
    seconds: float | None = None                     # what the run took, recorded on release
    peak_mb: int | None = None
    cores: float | None = None
    busy: float | None = None
    verdict: str = ""
    ran: list = field(default_factory=list)          # a receipt's tiers as they ran
    cut: bool = False                                # swept: its process died, nothing was measured


def fold(records) -> dict[str, Booking]:
    found: dict[str, Booking] = {}
    for event in records:
        item = found.get(event["booking"]) or Booking(id=event["booking"])
        for key in FIELDS:
            if key in event:
                value = event[key]
                if key in LISTS and isinstance(value, list):
                    value = [visible(v) if isinstance(v, str) else v for v in value]
                setattr(item, key, visible(value) if key in TEXT and isinstance(value, str) else value)
        item.state = event["state"]
        if item.state in (HELD, WAITING):
            item.since = event["at"]
        else:
            item.ended = event["at"]
        found[item.id] = item
    return found


def _order(item: Booking) -> int:
    return int(item.id[1:]) if item.id[1:].isdigit() else 0


class Book:
    def __init__(self, store, procs, *, clock=None):
        self.store = store
        self.procs = procs
        self.clock = clock

    def _now(self) -> str:
        moment = self.clock() if self.clock else dt.datetime.now(dt.timezone.utc)
        return moment.isoformat(timespec="seconds")

    def bookings(self) -> dict[str, Booking]:
        return fold(self.store.read(KEY).records)

    def holders(self, found: dict | None = None) -> list[Booking]:
        found = self.bookings() if found is None else found
        return sorted((item for item in found.values() if item.state == HELD), key=_order)

    def waiters(self, found: dict | None = None) -> list[Booking]:
        found = self.bookings() if found is None else found
        return sorted((item for item in found.values() if item.state == WAITING), key=_order)

    def live(self, item: Booking) -> bool:
        return self.procs.alive(item.pid, item.mark)

    def _write(self, tx, booking_id: str, state: str, **fields) -> Booking:
        tx.append({"at": self._now(), "booking": booking_id, "state": state, **fields})
        return fold(tx.read().records)[booking_id]

    def enqueue(self, who: str, note: str, *, pid: int | None = None, mark: str = "", run_for: str = "",
                command: str = "", ladder=(), will_run=(), project: str = "") -> Booking:
        with self.store.transaction(KEY) as tx:
            found = fold(tx.read().records)
            return self._write(tx, f"b{len(found) + 1}", WAITING, who=who, note=note, pid=pid, mark=mark,
                               run_for=run_for, command=command, ladder=list(ladder), will_run=list(will_run),
                               project=project, rule=RULE)

    def grant(self, booking_id: str, *, slots: int, by_hand: bool = False) -> Booking | None:
        with self.store.transaction(KEY) as tx:
            found = fold(tx.read().records)
            mine = found.get(booking_id)
            if mine is None or mine.state != WAITING:
                return None
            if len([item for item in self.holders(found) if self.live(item)]) >= slots:
                return None
            ahead = [item for item in self.waiters(found) if _order(item) < _order(mine) and self.live(item)]
            if ahead:
                return None
            if by_hand:   # it waited as its process; it holds as nobody's, until released by hand
                return self._write(tx, booking_id, HELD, pid=None, mark="")
            return self._write(tx, booking_id, HELD)

    def _end(self, booking_id: str, state: str, why: str = "", **fields) -> Booking:
        with self.store.transaction(KEY) as tx:
            return self._write(tx, booking_id, state, **({"why": why} if why else {}), **fields)

    def expire(self, booking_id: str, why: str) -> Booking:
        return self._end(booking_id, EXPIRED, why)

    def leave(self, booking_id: str) -> Booking:
        return self._end(booking_id, LEFT)

    def release(self, booking_id: str, why: str = "", *, measured: dict | None = None) -> Booking:
        """Released, with what the run took when the caller measured it (lane admission, stage 1)."""
        return self._end(booking_id, RELEASED, why,
                         **{k: v for k, v in (measured or {}).items() if k in MEASURED and v is not None})

    def sweep(self) -> list[Booking]:
        swept = []
        with self.store.transaction(KEY) as tx:
            found = fold(tx.read().records)
            for item in [*self.holders(found), *self.waiters(found)]:
                if item.pid is None or self.live(item):
                    continue
                state = RELEASED if item.state == HELD else LEFT
                cut = {"cut": True} if state == RELEASED else {}   # a run cut short measures nothing; never dropped
                swept.append(self._write(tx, item.id, state, why="no process behind it", **cut))
        return swept
