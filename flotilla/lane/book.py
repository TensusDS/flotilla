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
          "seconds", "peak_mb", "cores", "busy", "verdict", "ran", "cut", "inside")
MEASURED = ("seconds", "peak_mb", "cores", "busy", "verdict", "ran")
LISTS = ("ladder", "will_run")
MAX_TEXT = 2048   # a command and each of its signatures: every session folds the whole journal, so text is bounded
#: Every session writes the journal and any may be steered: a measured figure of the wrong type or out of range
#: folds as unknown, never as a crash of `flotilla lane` for all of them (review of stage 1).
BOUNDS = {"seconds": (0.0, 7 * 86400.0), "peak_mb": (0.0, 1e7), "cores": (0.0, 1024.0), "busy": (0.0, 1.0)}


def _figure(key, value):
    low, high = BOUNDS[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
        return None   # NaN fails both comparisons; infinities fail the bound
    return int(value) if key == "peak_mb" else value


def _tier(value) -> dict | None:
    if not isinstance(value, dict) or not isinstance(value.get("name"), str):
        return None
    tier = {"name": visible(value["name"][:200]), "status": value.get("status") if isinstance(value.get("status"), str)
            else ""}
    if value.get("kind") == "setup":
        tier["kind"] = "setup"
    for key in BOUNDS:
        tier[key] = _figure(key, value.get(key))
    return tier


def _clean(key, value):
    """A field as the journal may hold it, or its empty form."""
    if key in BOUNDS:
        return _figure(key, value)
    if key in LISTS:
        return [visible(v[:MAX_TEXT]) for v in value if isinstance(v, str)] if isinstance(value, list) else []
    if key == "ran":
        return [t for t in (_tier(v) for v in value) if t] if isinstance(value, list) else []
    if key == "cut":
        return value is True
    if key in ("verdict", "project", "command"):
        return visible(value[:MAX_TEXT]) if isinstance(value, str) else ""   # cut first: visible walks every char
    if key == "inside":
        return visible(value[:40]) if isinstance(value, str) else ""
    if key == "rule":
        return value if isinstance(value, int) and not isinstance(value, bool) else None
    return visible(value) if key in TEXT and isinstance(value, str) else value


def _aware(text) -> bool:
    try:
        return isinstance(text, str) and dt.datetime.fromisoformat(text).tzinfo is not None
    except ValueError:
        return False
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
    inside: str = ""                                 # a run inside this booking, recorded beside it, never booked


def fold(records) -> dict[str, Booking]:
    found: dict[str, Booking] = {}
    for event in records:
        if not isinstance(event, dict) or not isinstance(event.get("booking"), str) or not _aware(event.get("at")):
            continue   # a line nobody could have written through flotilla: skipped, never fatal
        item = found.get(event["booking"]) or Booking(id=event["booking"])
        for key in FIELDS:
            if key in event:
                setattr(item, key, _clean(key, event[key]))
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
                               run_for=run_for, command=command[:MAX_TEXT],
                               ladder=[step[:MAX_TEXT] for step in ladder], will_run=list(will_run),
                               project=project, rule=RULE)

    def record(self, inside: str, *, who: str, note: str, command: str = "", ladder=(), will_run=(),
               project: str = "", measured: dict | None = None) -> Booking:
        """A run inside a held booking: written once, already released, so it teaches its command's history and
        holds nothing. Never a new state - a flotilla before stage 1 folding this journal sees a released booking."""
        fields = {k: v for k, v in (measured or {}).items() if k in MEASURED and v is not None}
        with self.store.transaction(KEY) as tx:
            found = fold(tx.read().records)
            return self._write(tx, f"b{len(found) + 1}", RELEASED, who=who, note=note, command=command[:MAX_TEXT],
                               ladder=[step[:MAX_TEXT] for step in ladder], will_run=list(will_run),
                               project=project, rule=RULE, inside=inside, **fields)

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
