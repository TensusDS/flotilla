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

import copy
import datetime as dt
from dataclasses import dataclass, field

from flotilla.core.text import visible

KEY = "lane"
HELD, RELEASED, WAITING, EXPIRED, LEFT = "held", "released", "waiting", "expired", "left"
#: The admission rule a booking waits under. Stage 2 admits by budget only while every live booking carries this rule
#: or a later one: a seat on an older plugin counts slots and knows nothing of reservations (lane admission, section 3).
#: Rule 2 (0.25) knows the fair share across projects: a waiter under it may be passed by, or pass, for its project.
RULE = 2
#: The share modes `machine.toml` `lane_share` may name; anything else is first come, first served.
SHARES = ("fifo", "fair")
#: How far back a project's last turn counts for the fair share (project share design, section 3).
SERVED_WINDOW = dt.timedelta(hours=24)
FIELDS = ("who", "note", "pid", "mark", "run_for", "why", "command", "ladder", "will_run", "project", "rule",
          "seconds", "peak_mb", "cores", "busy", "verdict", "ran", "cut", "inside", "path")
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
TEXT = ("who", "note", "run_for", "why", "command", "project", "path")


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
    path: str = ""                                   # how it was admitted: fifo, or fair and for which project


def fold(records, into: dict[str, Booking] | None = None) -> dict[str, Booking]:
    """The bookings the events make, applied on top of `into` (a fold of the lines before them) without changing a
    booking in it: one already handed out stays as it was read."""
    found: dict[str, Booking] = dict(into or {})
    for event in records:
        if not isinstance(event, dict) or not isinstance(event.get("booking"), str) or not _aware(event.get("at")):
            continue   # a line nobody could have written through flotilla: skipped, never fatal
        seen = found.get(event["booking"])
        item = copy.copy(seen) if seen is not None else Booking(id=event["booking"])
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


def _when(text: str) -> dt.datetime | None:
    try:
        moment = dt.datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    return moment if moment.tzinfo is not None else None


def last_served(found: dict, now: dt.datetime) -> dict[str, str]:
    """Each project's last turn in the window: when one of its bookings was granted (a held booking, or one released
    after it held; a nested run recorded already released never waited for a turn)."""
    served: dict[str, str] = {}
    for item in found.values():
        if item.state not in (HELD, RELEASED) or item.inside:
            continue
        when = _when(item.since)
        if when is None or not now - SERVED_WINDOW <= when <= now + dt.timedelta(minutes=5):
            continue
        if item.since > served.get(item.project, ""):
            served[item.project] = item.since
    return served


def fair_choice(found: dict, live, now: dt.datetime, skip=()) -> Booking | None:
    """The live waiter that gets the next slot under the fair share: among the projects with live waiters, the one
    holding the fewest live slots now, then the one whose last turn is the earliest (none first), then the one whose
    oldest waiter came first; within that project, its oldest waiter (project share design, section 3)."""
    waiting = sorted((item for item in found.values() if item.state == WAITING and live(item) and item.id not in skip),
                     key=_order)
    if not waiting:
        return None
    holding: dict[str, int] = {}
    for item in found.values():
        if item.state == HELD and live(item):
            holding[item.project] = holding.get(item.project, 0) + 1
    served = last_served(found, now)
    oldest: dict[str, Booking] = {}
    for item in waiting:
        oldest.setdefault(item.project, item)
    project = min(oldest, key=lambda name: (holding.get(name, 0), served.get(name, ""), _order(oldest[name])))
    return oldest[project]


class Book:
    def __init__(self, store, procs, *, clock=None):
        self.store = store
        self.procs = procs
        self.clock = clock
        self._read = (0, None, {})   # offset, identity, bookings: a waiter polls for minutes, so it reads on (item 14)
        self.passed_for = ""         # the fair choice the last grant refused for, so the caller can see who it waits on

    def _now(self) -> str:
        moment = self.clock() if self.clock else dt.datetime.now(dt.timezone.utc)
        return moment.isoformat(timespec="seconds")

    def _found(self, tx=None) -> dict[str, Booking]:
        """The bookings, folding only the journal lines appended since this book last read it."""
        offset, identity, found = self._read
        records, after, now, whole = (tx.read_after(offset, identity) if tx is not None else
                                      self.store.read_after(KEY, offset, identity))
        found = fold(records, None if whole else found)
        self._read = (after, now, found)
        return dict(found)

    def bookings(self) -> dict[str, Booking]:
        return self._found()

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
        return self._found(tx)[booking_id]

    def enqueue(self, who: str, note: str, *, pid: int | None = None, mark: str = "", run_for: str = "",
                command: str = "", ladder=(), will_run=(), project: str = "") -> Booking:
        with self.store.transaction(KEY) as tx:
            found = self._found(tx)
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
            found = self._found(tx)
            return self._write(tx, f"b{len(found) + 1}", RELEASED, who=who, note=note, command=command[:MAX_TEXT],
                               ladder=[step[:MAX_TEXT] for step in ladder], will_run=list(will_run),
                               project=project, rule=RULE, inside=inside, **fields)

    def grant(self, booking_id: str, *, slots: int, by_hand: bool = False, share: str = "fifo",
              skip=()) -> Booking | None:
        """Admit `booking_id` if its turn has come. `skip`: fair choices the caller saw not take a free slot for a
        while - held back by their own checks - and so passes over (final review of 0.25.0, I1)."""
        self.passed_for = ""
        with self.store.transaction(KEY) as tx:
            found = self._found(tx)
            mine = found.get(booking_id)
            if mine is None or mine.state != WAITING:
                return None
            if len([item for item in self.holders(found) if self.live(item)]) >= slots:
                return None
            path = self._path(found, mine, share, skip)
            if path is None:
                return None
            fields = {"pid": None, "mark": ""} if by_hand else {}   # by hand: it holds as nobody's, until released
            return self._write(tx, booking_id, HELD, path=path, **fields)

    def _path(self, found: dict, mine: Booking, share: str, skip=()) -> str | None:
        """How `mine` is admitted now, or None while it waits its turn. First come, first served unless the share is
        fair and `mine` waits under rule 2; then it goes when it is the fair choice, and also first come, first served
        when the fair choice waits under an older rule - that one knows nothing of being chosen, and waits for whoever
        is ahead of it, so yielding to it would leave the slot empty (project share design, section 3)."""
        ahead = any(_order(item) < _order(mine) and self.live(item) for item in self.waiters(found))
        if share != "fair" or (mine.rule or 0) < 2:
            return None if ahead else "fifo"
        moment = self.clock() if self.clock else dt.datetime.now(dt.timezone.utc)
        choice = fair_choice(found, self.live, moment, skip)
        if choice is not None and choice.id == mine.id:
            when = last_served(found, moment).get(mine.project, "")
            return f"fair: {mine.project or 'no project'} served {when or 'not in 24 h'}"
        if choice is not None and (choice.rule or 0) < 2 and not ahead:
            return f"fifo: the fair choice {choice.id} waits under an older rule"
        self.passed_for = choice.id if choice is not None else ""
        return None

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

    def sweep(self, gone=None) -> list[Booking]:
        """End bookings whose process is not alive; with `gone`, only those it also says are gone."""
        swept = []
        with self.store.transaction(KEY) as tx:
            found = self._found(tx)
            for item in [*self.holders(found), *self.waiters(found)]:
                if item.pid is None or self.live(item) or (gone is not None and not gone(item)):
                    continue
                state = RELEASED if item.state == HELD else LEFT
                cut = {"cut": True} if state == RELEASED else {}   # a run cut short measures nothing; never dropped
                swept.append(self._write(tx, item.id, state, why="no process behind it", **cut))
        return swept
