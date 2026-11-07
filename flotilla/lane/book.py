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
import hashlib
import hmac
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from flotilla.core.text import visible

KEY = "lane"
HELD, RELEASED, WAITING, EXPIRED, LEFT = "held", "released", "waiting", "expired", "left"
#: The admission rule a booking waits under. Stage 2 admits by budget only while every live booking carries this rule
#: or a later one: a seat on an older plugin counts slots and knows nothing of reservations (lane admission, section 3).
#: Rule 2 (0.25) knows the fair share across projects: a waiter under it may be passed by, or pass, for its project.
#: Rule 3 (0.28) knows light runs: a light holder takes no place, so a waiter under an older rule - which would count
#: it as one, and could wait for ever behind a stream of them - turns light admission off while it waits.
#: Rule 4 (0.30) knows value: it admits by the memory free now, orders by purpose, wait and run time, and freezes a
#: younger run for a higher one. A waiter under an older rule turns the lane back to the count while it waits; a
#: frozen booking stays a held record, so an older flotilla still sees it holding (lane shared machine, section 7).
#: Rule 5 (0.31) is decided by the lane daemon: a waiter under it never admits itself while a daemon runs, and its
#: wrapper obeys only an order the live daemon signed (lane daemon design, sections 4.5 and 4.8).
RULE = 5
#: Why a freeze was ordered: for a waiter's start, for a machine over its floor, for a run churning at its ceiling.
CAUSES = ("waiter", "over", "pressed")
#: What a foreign computing run (unbooked) is taken to use of the light budget, and a holder whose peak was never
#: measured of its memory: the fixed prior (lane light runs design, section 2).
FOREIGN_CORES, UNKNOWN_PEAK_MB = 4.0, 2048
#: The share modes `machine.toml` `lane_share` may name; anything else is first come, first served.
SHARES = ("fifo", "fair")
#: How far back a project's last turn counts for the fair share (project share design, section 3).
SERVED_WINDOW = dt.timedelta(hours=24)
FIELDS = ("who", "note", "pid", "mark", "run_for", "why", "command", "ladder", "will_run", "project", "rule",
          "seconds", "peak_mb", "cores", "busy", "verdict", "ran", "cut", "inside", "path", "place",
          "kind", "asked", "started", "ran_s", "frozen", "order_by", "ordered_at", "reclaim_mb", "frozen_at",
          "held_mb", "frozen_s", "freezes", "heavy", "pinned", "unanswered", "freeze_kind", "unit", "group",
          "peak_cores", "group_mark", "refused_by", "refused_at", "order_cause", "order_pid", "order_mark",
          "ceiling_mb", "squeezed", "held_back", "by_hand", "floor_mb", "held_freezes", "order_sig")
MEASURED = ("seconds", "peak_mb", "cores", "busy", "verdict", "ran", "peak_cores")
#: A freeze order's progress on a held booking: ordered by a waiter, then frozen by its holder; absent while it runs.
FROZEN_STATES = ("ordered", "frozen")
#: A run's purpose, the type part of its priority (lane shared machine, section 2.6); anything else is a plain run.
KINDS = ("push", "handover", "review", "run")
FREEZE_KINDS = ("scope", "signal")
MOMENTS = ("asked", "started", "ordered_at", "frozen_at", "refused_at")
FLAGS = ("heavy", "pinned", "unanswered", "squeezed", "by_hand")
#: A lane scope's name as the holder makes it: any session writes the journal, and a unit's name reaches systemctl,
#: so anything else folds to nothing (final review of 0.30.0, I7).
UNIT = re.compile(r"flotilla-lane-[A-Za-z0-9_.-]{1,120}\.scope")
LISTS = ("ladder", "will_run")
MAX_TEXT = 2048   # a command and each of its signatures: every session folds the whole journal, so text is bounded
#: Every session writes the journal and any may be steered: a measured figure of the wrong type or out of range
#: folds as unknown, never as a crash of `flotilla lane` for all of them (review of stage 1).
BOUNDS = {"seconds": (0.0, 7 * 86400.0), "peak_mb": (0.0, 1e7), "cores": (0.0, 1024.0), "busy": (0.0, 1.0)}
#: The booking's own figures beside the measured ones a tier also carries (a tier's are BOUNDS alone).
FIGURES = {**BOUNDS, "ran_s": BOUNDS["seconds"], "frozen_s": BOUNDS["seconds"], "reclaim_mb": BOUNDS["peak_mb"],
           "held_mb": BOUNDS["peak_mb"], "peak_cores": BOUNDS["cores"], "ceiling_mb": BOUNDS["peak_mb"],
           "floor_mb": BOUNDS["peak_mb"]}
WHOLE = ("peak_mb", "reclaim_mb", "held_mb", "ceiling_mb", "floor_mb")


def _figure(key, value):
    low, high = FIGURES[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
        return None   # NaN fails both comparisons; infinities fail the bound
    return int(value) if key in WHOLE else value


def _tier(value) -> dict | None:
    if not isinstance(value, dict) or not isinstance(value.get("name"), str):
        return None
    tier = {"name": visible(value["name"][:200]), "status": value.get("status") if isinstance(value.get("status"), str)
            else ""}
    if value.get("kind") == "setup":
        tier["kind"] = "setup"
    for key in BOUNDS:
        tier[key] = _figure(key, value.get(key))
    if _figure("peak_cores", value.get("peak_cores")) is not None:   # only where measured: older tiers stay as read
        tier["peak_cores"] = _figure("peak_cores", value.get("peak_cores"))
    return tier


def _clean(key, value):
    """A field as the journal may hold it, or its empty form."""
    if key in FIGURES:
        return _figure(key, value)
    if key == "frozen":
        return value if value in FROZEN_STATES else ""
    if key == "kind":
        return value if value in KINDS else "run"
    if key == "freeze_kind":
        return value if value in FREEZE_KINDS else ""
    if key in MOMENTS:
        return value if _aware(value) else ""
    if key == "unit":
        return value if isinstance(value, str) and UNIT.fullmatch(value) else ""
    if key == "order_cause":
        return value if value in CAUSES else ""
    if key == "order_sig":
        return value if isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) else ""
    if key == "held_back":
        return visible(value[:200]) if isinstance(value, str) else ""
    if key in ("order_by", "group_mark", "refused_by", "order_mark"):
        return visible(value[:80]) if isinstance(value, str) else ""
    if key in FLAGS:
        return value is True
    if key in ("freezes", "held_freezes"):
        return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0
    if key == "group":
        return value if isinstance(value, int) and not isinstance(value, bool) and value >= 2 else None
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
    if key in ("pid", "order_pid"):   # a number another session's signal may reach: a real process's, never 0, 1, -1 or text
        return value if isinstance(value, int) and not isinstance(value, bool) and value >= 2 else None
    if key == "rule":
        return value if isinstance(value, int) and not isinstance(value, bool) else None
    if key == "place":
        return value if isinstance(value, int) and not isinstance(value, bool) and value >= 1 else None
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
    place: int | None = None                         # the place it stands in: an expired booking's, made again
    kind: str = ""                                   # its purpose: push, handover, review or run (rule 4)
    asked: str = ""                                  # when it first asked; `since` moves with every record
    started: str = ""                                # when it last started or thawed
    ran_s: float | None = None                       # run time before its last start, frozen time left out
    frozen: str = ""                                 # "", "ordered" (a waiter asked) or "frozen" (its holder obeyed)
    order_by: str = ""                               # the waiter whose order froze it
    ordered_at: str = ""
    reclaim_mb: int | None = None                    # what the order asks to move to swap
    frozen_at: str = ""
    held_mb: int | None = None                       # what it held when frozen
    frozen_s: float | None = None                    # all its frozen time
    freezes: int = 0
    heavy: bool = False                              # heavy once, heavy for good (section 2.3)
    pinned: bool = False                             # never frozen: by hand, a `freeze = false` tier
    unanswered: bool = False                         # an order lapsed, or it was let go: never ordered again
    freeze_kind: str = ""                            # how its holder froze it: scope or signal
    unit: str = ""                                   # its scope, for whoever thaws it when its holder is gone
    group: int | None = None                         # its command's process group, likewise
    group_mark: str = ""                             # that group leader's start mark: the same process, not a reused pid
    refused_by: str = ""                             # the waiter whose order its holder last refused, and when: that
    refused_at: str = ""                             # waiter does not order it again until the answer bound is over
    peak_cores: float | None = None                  # the busiest stretch of cores it used, measured
    order_cause: str = ""                            # why its order was given: waiter, over or pressed (rule 5)
    order_pid: int | None = None                     # the daemon that signed the order: its pid and start mark
    order_mark: str = ""
    ceiling_mb: int | None = None                    # the soft ceiling the daemon set on its scope; None: none
    squeezed: bool = False                           # its ceiling was lowered below its use for an overload
    held_back: str = ""                              # a waiter's own gates hold it (CI, disk...): the daemon skips it
    by_hand: bool = False                            # a waiter taking the lane by hand: admitted as nobody's
    floor_mb: int | None = None                      # the asking project's memory floor
    held_freezes: int = 0                            # freezes for an overload or a pressure, not for a waiter
    order_sig: str = ""                              # the daemon's seal over its order (lane daemon design, 4.8)


def seal(key: bytes, booking: str, cause: str, ordered_at: str, order_by: str, pid, mark: str) -> str:
    """The daemon's seal over one order: HMAC-SHA256 with the key only the daemon makes, over everything the order
    says - a line copying a pid and a mark, or turning one order into another, does not carry it."""
    text = "\n".join([booking, cause, ordered_at, order_by, str(pid), mark])
    return hmac.new(key, text.encode("utf-8"), hashlib.sha256).hexdigest()


def number(name) -> int | None:
    """A booking's number, or None for a name flotilla never gives: ASCII digits only, at most 18 of them - a name
    `isdigit` accepts and `int` refuses must not stop the numbering for the machine (review of 0.26.1, I3)."""
    digits = name[1:] if isinstance(name, str) and name[:1] == "b" else ""
    return int(digits) if digits and len(digits) <= 18 and digits.isascii() and digits.isdecimal() else None


def _next_id(found: dict) -> str:
    """Past the highest number taken: a compacted journal holds fewer bookings than its highest number (compact.py),
    so a number counted from the bookings, as flotilla before 0.26.1 counts it, may be one already taken."""
    highest = max((found_number for found_number in map(number, found) if found_number is not None), default=0)
    return f"b{max(highest, len(found)) + 1}"


def fold(records, into: dict[str, Booking] | None = None) -> dict[str, Booking]:
    """The bookings the events make, applied on top of `into` (a fold of the lines before them) without changing a
    booking in it: one already handed out stays as it was read."""
    found: dict[str, Booking] = dict(into or {})
    for event in records:
        if not isinstance(event, dict) or not isinstance(event.get("booking"), str) or not _aware(event.get("at")) \
                or not isinstance(event.get("state"), str):
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
    """Where a booking stands in the line: the place it inherited from its own expired wait, else its number."""
    if item.place:
        return item.place
    return int(item.id[1:]) if item.id[1:].isdigit() else 0


#: How long after its wait ran out a booking made again keeps its place (lane light runs design, section 3).
SENIORITY = dt.timedelta(minutes=10)


def inherited(found: dict, who: str, ladder, now: dt.datetime) -> int | None:
    """The place a new booking of `who` for `ladder` keeps: the latest expired booking of the same session and the
    same exact signature, ended within SENIORITY, whose place no booking since has taken. None otherwise."""
    if not ladder:
        return None
    for item in sorted(found.values(), key=lambda item: number(item.id) or 0, reverse=True):
        ended = _when(item.ended)
        if item.state != EXPIRED or item.who != who or item.ladder[:1] != list(ladder)[:1] or ended is None:
            continue
        if not now - SENIORITY <= ended <= now:
            return None
        place = _order(item)
        taken = {other.place for other in found.values() if other.place and (number(other.id) or 0) >
                 (number(item.id) or 0)}   # its own place is the one it hands on; only a later booking took it
        return None if place in taken else place
    return None


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
        if item.state not in (HELD, RELEASED) or item.inside or item.path.startswith("light"):   # no turn taken
            continue
        when = _when(item.since)
        if when is None or not now - SERVED_WINDOW <= when <= now + dt.timedelta(minutes=5):
            continue
        if item.since > served.get(item.project, ""):
            served[item.project] = item.since
    return served


@dataclass(frozen=True)
class Room:
    """What a light run may take: `factor` x `cpus` cores, and `memory_left()` MB - the available memory less the
    floor, read when the grant decides, inside the journal's lock - less what the holders are estimated to peak at.
    `foreign`: unbooked runs computing now."""
    cpus: int
    factor: float
    memory_left: Callable[[], int | None]
    foreign: int = 0


def _light(found: dict, mine: Booking, room: Room, hist, live) -> tuple[str, str]:
    """("", how) when `mine` may start now as a light run, else (why not, "")."""
    from flotilla.lane import estimate
    own = estimate.for_booking(mine, hist, cpus=room.cpus)
    if (mine.rule or 0) < RULE or not estimate.light_booking(mine, hist, cpus=room.cpus):
        return f"not light: {own.source}", ""
    waiting = [item for item in found.values() if item.state == WAITING and item.id != mine.id and live(item)]
    if any((item.rule or 0) < RULE for item in waiting):
        return "a waiter of an older flotilla is live", ""
    earlier = [item for item in waiting if _order(item) < _order(mine)
               and estimate.light_booking(item, hist, cpus=room.cpus)]
    if earlier:
        return f"behind light {min(earlier, key=_order).id}", ""
    used, total, reserved = budget(found, room, hist, live)
    if own.cores > total - used:
        return f"needs {own.cores:g} cores, {max(total - used, 0):g} left", ""
    free = room.memory_left()
    if free is None:
        return "memory cannot be read", ""
    if own.peak_mb > free - reserved:
        return f"needs {own.peak_mb} MB, {max(free - reserved, 0)} left", ""
    return "", f"light: {used + own.cores:g} of {total:g} cores, {free - reserved - own.peak_mb} MB left"


def budget(found: dict, room: Room, hist, live) -> tuple[float, float, int]:
    """The cores the live holders and foreign runs use by estimates, the cores there are, and the memory the holders
    are estimated to peak at: what a light run is measured against, and what `flotilla lane` prints."""
    from flotilla.lane import estimate
    used, reserved = room.foreign * FOREIGN_CORES, 0
    for item in found.values():
        if item.state == HELD and live(item):
            theirs = estimate.for_booking(item, hist, cpus=room.cpus)
            guessed = "prior" in theirs.source
            used += room.cpus if guessed or theirs.cores is None else theirs.cores
            reserved += theirs.peak_mb if theirs.peak_mb is not None else UNKNOWN_PEAK_MB
    return used, room.factor * room.cpus, reserved


def is_light(item: Booking, hist, cpus: int | None = None) -> bool:
    """A waiter the light path serves: under this rule, and light by its own measurements."""
    from flotilla.lane import estimate
    return (item.rule or 0) >= RULE and estimate.light_booking(item, hist, cpus=cpus)


def older_waiting(found: dict, live, mine: str = "") -> bool:
    """A live waiter of a flotilla before light runs: while one waits, the lane counts every run as before - it counts
    light holders as places and would wait for ever behind them, and it waits behind a light waiter ahead of it (the
    final review of 0.28.0, C1)."""
    return any(item.state == WAITING and item.id != mine and live(item) and (item.rule or 0) < RULE
               for item in found.values())


def heavy_starts(found: dict, hist, *, slots: int, now: dt.datetime, live, ceiling: float,
                 cpus: int | None = None, counted: bool = False) -> dict[str, dt.datetime | None]:
    """When each live heavy waiter starts by estimates: `slots` lanes, each serving one heavy run after another in
    the line's order, the next run taking the lane that frees first. A heavy holder has its estimate less what it has
    run left, at least a minute; a run never measured counts at `ceiling`. None with no slot at all. An answer, not a
    promise: it reads "or sooner" (lane light runs design, section 5)."""
    from flotilla.lane import estimate

    def span(item) -> float:
        seconds = estimate.for_booking(item, hist, cpus=cpus).seconds
        return seconds if seconds is not None else ceiling

    lanes = []
    for item in found.values():
        if item.state == HELD and live(item) and (counted or not item.path.startswith("light")):
            began = _when(item.since) or now
            lanes.append(max(began + dt.timedelta(seconds=span(item)), now + dt.timedelta(minutes=1)))
    lanes = sorted(lanes)[len(lanes) - slots:] if 0 < slots <= len(lanes) else sorted(lanes) if slots > 0 else []
    lanes += [now] * max(slots - len(lanes), 0)
    starts: dict[str, dt.datetime | None] = {}
    for item in sorted((item for item in found.values() if item.state == WAITING and live(item)
                        and (counted or not is_light(item, hist, cpus))), key=_order):
        if not lanes:
            starts[item.id] = None
            continue
        first = min(range(len(lanes)), key=lambda at: lanes[at])
        starts[item.id] = lanes[first]
        lanes[first] = lanes[first] + dt.timedelta(seconds=span(item))
    return starts


def light_verdict(found: dict, mine: Booking, room: Room, hist, live) -> str:
    """"" when `mine` may start now as a light run beside what holds the lane, else why not (lane light runs
    design, section 2)."""
    return _light(found, mine, room, hist, live)[0]


def _seconds_since(text: str, now: dt.datetime) -> float:
    moment = _when(text)
    return max(0.0, (now - moment).total_seconds()) if moment is not None else 0.0


def _frozen_for(item: Booking, now: dt.datetime) -> float:
    return round((item.frozen_s or 0.0) + _seconds_since(item.frozen_at, now), 2)


def _thaw_command(item: Booking, procs=None) -> bool:
    """Thaw a frozen booking's command from outside its holder: its scope, else its process tree. False when that
    failed, so whoever asked keeps the booking and tries again. A process tree is signalled only when its leader is
    provably the command that was frozen - the same pid and start mark - never a pid reused since, nor one a crafted
    record names (final review of 0.30.0, I7)."""
    from flotilla.lane import freeze
    if item.unit:
        if not item.unit.startswith(f"flotilla-lane-{item.id}-"):
            return True   # a scope of another booking, or none of the lane's: never thawed for this one
        return freeze.thaw_unit(item.unit)
    if item.group:
        exists = getattr(procs, "exists", None)
        there = exists(item.group) if exists is not None else None
        if there is None:   # cannot ask whether it exists: ask its start mark instead
            there = getattr(procs, "start_mark", lambda pid: None)(item.group) is not None
        if there is False or not item.group_mark:
            return True     # gone, or never proven ours: nothing of the lane's to thaw
        if getattr(procs, "start_mark", lambda pid: None)(item.group) != item.group_mark:
            return True     # another process has its number now
        theirs = freeze.carries(item.group, item.id)   # proof from the process itself, not from the journal
        if theirs is False:
            return True     # names another booking: nothing of the lane's to thaw
        if theirs is None:
            # no word of its booking (a re-exec cleared it, another user's, a zombie): the same process as the one
            # frozen, by its start mark, and still stopped, is continued all the same - a lane command must never
            # stay stopped, and a wrong SIGCONT only resumes a stopped process (the person's ruling, 2026-11-06)
            halted = freeze.stopped(item.group)
            if halted is None:
                return False    # cannot be read now: kept, and asked again
            if not halted:
                return True     # it runs: nothing stopped there to continue
        return freeze.thaw_tree(item.group)
    return True   # frozen with no command running: nothing to thaw but the record


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
        if item.state == HELD and live(item) and not item.path.startswith("light"):   # a light run holds no place
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
        self.waits_for = ""          # why the last grant refused a light run

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

    def stamp(self) -> tuple | None:
        """The journal file's size and modification time: what a waiter checks once a second, cheaply, to know the
        lane changed. None when there is no file to ask."""
        root = getattr(self.store, "root", None)
        try:
            found = (Path(root) / f"{KEY}.jsonl").stat() if root is not None else None
        except OSError:
            return None
        return (found.st_size, found.st_mtime_ns) if found is not None else None

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
        self._compact(tx)
        return self._found(tx)[booking_id]

    def _compact(self, tx) -> None:
        """Past 2 MB the journal is rewritten to what the lane and the estimates read, in this lock (compact.py).
        A compaction that fails leaves the journal as it was; the append it followed stands."""
        from flotilla.lane import compact
        root = getattr(self.store, "root", None)
        if root is None:
            return
        path = Path(root) / f"{KEY}.jsonl"
        try:
            if compact.due(path, root):
                now = self.clock() if self.clock else dt.datetime.now(dt.timezone.utc)
                compact.compact(tx, root, path, now)
        except Exception as err:  # noqa: BLE001 - a journal too big is no reason to fail a booking
            print(f"flotilla: the lane journal could not be compacted, and stays as it was: {err}", file=sys.stderr)

    def enqueue(self, who: str, note: str, *, pid: int | None = None, mark: str = "", run_for: str = "",
                command: str = "", ladder=(), will_run=(), project: str = "", kind: str = "",
                pinned: bool = False, floor_mb: int | None = None, by_hand: bool = False) -> Booking:
        with self.store.transaction(KEY) as tx:
            found = self._found(tx)
            moment = self.clock() if self.clock else dt.datetime.now(dt.timezone.utc)
            place = inherited(found, who, [step[:MAX_TEXT] for step in ladder], moment)
            extra = {**({"place": place} if place else {}), **({"kind": kind} if kind else {}),
                     **({"pinned": True} if pinned else {}), **({"by_hand": True} if by_hand else {}),
                     **({"floor_mb": floor_mb} if floor_mb is not None else {})}
            return self._write(tx, _next_id(found), WAITING, who=who, note=note, pid=pid, mark=mark,
                               run_for=run_for, command=command[:MAX_TEXT],
                               ladder=[step[:MAX_TEXT] for step in ladder], will_run=list(will_run),
                               project=project, rule=RULE, asked=moment.isoformat(timespec="seconds"), **extra)

    def record(self, inside: str, *, who: str, note: str, command: str = "", ladder=(), will_run=(),
               project: str = "", measured: dict | None = None) -> Booking:
        """A run inside a held booking: written once, already released, so it teaches its command's history and
        holds nothing. Never a new state - a flotilla before stage 1 folding this journal sees a released booking."""
        fields = {k: v for k, v in (measured or {}).items() if k in MEASURED and v is not None}
        with self.store.transaction(KEY) as tx:
            found = self._found(tx)
            return self._write(tx, _next_id(found), RELEASED, who=who, note=note, command=command[:MAX_TEXT],
                               ladder=[step[:MAX_TEXT] for step in ladder], will_run=list(will_run),
                               project=project, rule=RULE, inside=inside, **fields)

    def grant(self, booking_id: str, *, slots: int, by_hand: bool = False, share: str = "fifo",
              skip=(), room: Room | None = None, value=None, fallback: bool = False) -> Booking | None:
        """Admit `booking_id` if its turn has come. `skip`: fair choices the caller saw not take a free slot for a
        while - held back by their own checks - and so passes over (final review of 0.25.0, I1).

        With a `value` (admission.Value) and no waiter of an older flotilla, the grant goes by value: by the memory
        free now and the priority of every run, and it may order a younger run frozen for this one (lane shared
        machine design). Otherwise, with a `room`, a run its own measurements call light takes what the heavy runs
        leave, and only that; heavy runs keep the count and the order (lane light runs design). Without either, the
        grant is the count alone, as before.

        With `fallback` - no lane daemon could be raised - a run starts only into an empty lane: no live holder, and
        no live waiter ahead of it (lane daemon design, section 6)."""
        self.passed_for = self.waits_for = ""
        fields = {"pid": None, "mark": ""} if by_hand else {}   # by hand: it holds as nobody's, until released
        with self.store.transaction(KEY) as tx:
            found = self._found(tx)
            mine = found.get(booking_id)
            if mine is None or mine.state != WAITING:
                return None
            if fallback:
                if any(self.live(item) for item in self.holders(found)) or \
                        any(self.live(item) and _order(item) < _order(mine) for item in self.waiters(found)):
                    return None
                return self._write(tx, booking_id, HELD, path="fallback: empty lane", started=self._now(), **fields)
            from flotilla.lane import admission
            if value is not None:   # whichever way it admits, a frozen command is never left for a dead holder
                found = self._rescue(tx, found, value)
            if value is not None and admission.by_value(found, self.live):
                return self._by_value(tx, booking_id, value, share, fields, by_hand)
            lights: frozenset = frozenset()
            light_path = room is not None and (mine.rule or 0) >= RULE and not older_waiting(found, self.live,
                                                                                               booking_id)
            if light_path:
                from flotilla.lane import estimate
                moment = self.clock() if self.clock else dt.datetime.now(dt.timezone.utc)
                hist = estimate.history(found, now=moment)
                if estimate.light_booking(mine, hist, cpus=room.cpus):
                    self.waits_for, how = _light(found, mine, room, hist, self.live)
                    if not self.waits_for:
                        return self._write(tx, booking_id, HELD, path=how, **fields)
                    # what the heavy runs leave does not hold it: it still goes by count and arrival when a place is
                    # free, as any run did before 0.28 - a light run never fares worse than a heavy one (0.28.1)
                else:
                    lights = frozenset(item.id for item in found.values() if item.state == WAITING
                                       and self.live(item) and is_light(item, hist, room.cpus))
            heavy = [item for item in self.holders(found) if self.live(item)
                     and not (light_path and item.path.startswith("light"))]
            if len(heavy) >= slots:
                return None
            path = self._path(found, mine, share, skip, lights)
            if path is None:
                return None
            return self._write(tx, booking_id, HELD, path=path, **fields)

    def _moment(self) -> dt.datetime:
        return self.clock() if self.clock else dt.datetime.now(dt.timezone.utc)

    def _rescue(self, tx, found: dict, value) -> dict:
        """Before deciding, in the same lock: a freeze order nobody answered lapses; a frozen command whose holder is
        dead, or whose watch went silent, is thawed - a frozen command never stays frozen for a holder that cannot
        thaw it (the person's ruling, 2026-11-06). A thaw that fails is tried again by the next grant."""
        from flotilla.lane import admission
        now = self._moment()
        for item in [item for item in found.values() if item.state != HELD and item.frozen == "frozen"]:
            # ended still frozen: an older flotilla's sweep released a dead holder without thawing its command
            if _thaw_command(item, self.procs):
                self._write(tx, item.id, item.state, frozen="")
        for item in [item for item in found.values() if item.state == HELD and item.frozen and item.pid is not None]:
            if item.frozen == "ordered":
                asked = _when(item.ordered_at)
                if asked is None or (now - asked).total_seconds() > admission.ANSWER_S:
                    self._write(tx, item.id, HELD, frozen="", unanswered=True)
                continue
            dead = item.pid is not None and not self.live(item)
            age = None if dead else value.beat_age_s(item.id)
            if not dead and age is not None and age <= admission.BEAT_STALE_S:
                continue
            if not _thaw_command(item, self.procs):
                continue
            if dead:
                self._write(tx, item.id, RELEASED, why="no process behind it", cut=True, frozen="")
            else:
                self._write(tx, item.id, HELD, frozen="", unanswered=True, started=self._now(),
                            frozen_s=_frozen_for(item, now))
        return self._found(tx)

    def _by_value(self, tx, booking_id: str, value, share: str, fields: dict,
                  by_hand: bool = False) -> Booking | None:
        from flotilla.lane import admission, estimate, policy
        found = self._found(tx)
        now = self._moment()
        hist = estimate.history(found, now=now)
        runs, machine = admission.view(found, hist, value, now=now, live=self.live, mine=booking_id, share=share,
                                       by_hand=booking_id if by_hand else "")
        by_id = {run.id: run for run in runs}
        for run in runs:   # a run that grew past the mark is heavy for good (section 2.3)
            if run.heavy and not found[run.id].heavy and run.state != policy.WAITING:
                self._write(tx, run.id, HELD, heavy=True)
        decision = policy.decide(runs, machine, booking_id)
        self.waits_for = decision.why
        if decision.start:
            me = by_id[booking_id]
            how = f"value: P {policy.priority(me, machine.immortal_s):.2f}, {'heavy' if me.heavy else 'light'}"
            return self._write(tx, booking_id, HELD, path=how, started=self._now(), heavy=me.heavy, **fields)
        for victim, reclaim in decision.freeze:
            self._write(tx, victim, HELD, frozen="ordered", order_by=booking_id, ordered_at=self._now(),
                        reclaim_mb=reclaim)
        return None

    def frozen(self, booking_id: str, *, freeze_kind: str, unit: str, group: int | None,
               held_mb: int | None) -> Booking | None:
        """Its holder obeyed: the command is frozen, and its run time so far is kept, frozen time left out. None, and
        nothing written, when the booking no longer holds: a record never brings a released booking back."""
        with self.store.transaction(KEY) as tx:
            item = self._found(tx).get(booking_id)
            if item is None or item.state != HELD:
                return None
            ran = (item.ran_s or 0.0) + _seconds_since(item.started or item.since, self._moment())
            mark = getattr(self.procs, "start_mark", lambda pid: None)(group) if group else None
            return self._write(tx, booking_id, HELD, frozen="frozen", frozen_at=self._now(), ran_s=round(ran, 2),
                               freezes=item.freezes + 1, freeze_kind=freeze_kind, unit=unit,
                               **({"held_freezes": item.held_freezes + 1} if item.order_cause in ("over", "pressed")
                                  else {}),
                               **({"group": group} if group else {}), **({"group_mark": mark} if mark else {}),
                               **({"held_mb": held_mb} if held_mb else {}))

    def thaw(self, booking_id: str, value, *, force: bool = False, never_again: bool = False,
             while_waits: str = "") -> Booking | None:
        """A frozen run asks to come back, as a waiter does: it thaws when the policy starts it (or `force`), and
        keeps its first ask and its run time. None while it waits; `waits_for` says for what. `while_waits`: force
        only while that booking still waits, read in this lock - a waiter that started meanwhile took the room, and
        the run comes back through the queue instead of beside it."""
        from flotilla.lane import admission, estimate, policy
        self.waits_for = ""
        with self.store.transaction(KEY) as tx:
            found = self._found(tx)
            item = found.get(booking_id)
            if item is None or item.state != HELD or item.frozen != "frozen":
                return None
            now = self._moment()
            if force and while_waits and (found.get(while_waits) is None or found[while_waits].state != WAITING):
                force = False
            if not force:
                runs, machine = admission.view(found, estimate.history(found, now=now), value, now=now,
                                               live=self.live, mine=booking_id)
                decision = policy.decide(runs, machine, booking_id, may_freeze=False)
                self.waits_for = decision.why
                if not decision.start:
                    return None
            return self._write(tx, booking_id, HELD, frozen="", started=self._now(),
                               frozen_s=_frozen_for(item, now), **({"unanswered": True} if never_again else {}))

    def refuse(self, booking_id: str, *, order_by: str = "", ordered_at: str = "") -> Booking | None:
        """An order its holder will not obey - from no live waiter, stale, or one the rules would not give: it lapses,
        and the booking stays freezable by a true order. With `order_by`/`ordered_at`, only that very order lapses:
        a newer one written meanwhile is left to be read."""
        with self.store.transaction(KEY) as tx:
            item = self._found(tx).get(booking_id)
            if item is None or item.state != HELD or item.frozen != "ordered":
                return None
            if (order_by or ordered_at) and (item.order_by, item.ordered_at) != (order_by, ordered_at):
                return None
            return self._write(tx, booking_id, HELD, frozen="", refused_by=item.order_by, refused_at=self._now())

    def unfreezable(self, booking_id: str) -> Booking | None:
        """Its holder can no longer obey orders (its watch failed): any order lapses, and it is never ordered again."""
        with self.store.transaction(KEY) as tx:
            item = self._found(tx).get(booking_id)
            if item is None or item.state != HELD:
                return None
            return self._write(tx, booking_id, HELD, frozen="", unanswered=True)

    def scoped(self, booking_id: str, unit: str) -> Booking | None:
        """Its holder put the command in a scope: the daemon sets the scope's ceiling (lane daemon design, 4.3 b).
        None, and nothing written, when the booking no longer holds or the name is not a lane scope's."""
        if not isinstance(unit, str) or not UNIT.fullmatch(unit):
            return None
        with self.store.transaction(KEY) as tx:
            item = self._found(tx).get(booking_id)
            if item is None or item.state != HELD:
                return None
            return self._write(tx, booking_id, HELD, unit=unit)

    def hold_back(self, booking_id: str, why: str) -> None:
        """A waiter's own gates (CI, disk, its project's floor) hold it, or no longer do: the daemon admits only a
        waiter they let through. Written only when it changes - a waiter polls for minutes."""
        why = visible(why[:200]) if isinstance(why, str) else ""
        item = self.bookings().get(booking_id)
        if item is None or item.state != WAITING or item.held_back == why:
            return
        with self.store.transaction(KEY) as tx:
            item = self._found(tx).get(booking_id)
            if item is not None and item.state == WAITING and item.held_back != why:
                self._write(tx, booking_id, WAITING, held_back=why)

    def apply(self, actions, signer: tuple) -> list[str]:
        """The daemon's decisions of one tick, in one transaction, each checked against the booking as it stands in
        this lock and skipped when it no longer applies. `signer`: the daemon's pid, start mark and key; every order
        carries the pid and mark and a seal made with the key, so a wrapper can tell it from a line anyone wrote -
        a pid and a mark alone are public (lane daemon design, 4.8; security review of c0b73f9). The ids written."""
        pid, mark = signer[0], signer[1]
        key = signer[2] if len(signer) > 2 else b""
        done: list[str] = []
        squeezed, restored = set(actions.squeezed), set(actions.restored)
        with self.store.transaction(KEY) as tx:
            found = self._found(tx)
            now = self._moment()
            for booking_id, heavy, worth in actions.starts:
                item = found.get(booking_id)
                if item is None or item.state != WAITING or not self.live(item) or (item.rule or 0) < RULE \
                        or item.held_back:
                    continue
                hand = {"pid": None, "mark": ""} if item.by_hand else {}
                self._write(tx, booking_id, HELD, path=f"value: P {worth:.2f}, {'heavy' if heavy else 'light'}",
                            started=self._now(), heavy=bool(heavy), **hand)
                done.append(booking_id)
            started = set(done)   # a run started in this very tick gets its start ceiling with it
            for booking_id, mb in actions.ceilings:
                item = found.get(booking_id)
                if item is None or (item.state != HELD and booking_id not in started):
                    continue
                flag = {"squeezed": True} if booking_id in squeezed else \
                    ({"squeezed": False} if booking_id in restored else {})
                self._write(tx, booking_id, HELD, ceiling_mb=mb, **flag)
                done.append(booking_id)
            for booking_id, reclaim, cause, waiter in actions.orders:
                item = found.get(booking_id)
                if item is None or item.state != HELD or item.frozen or item.unanswered:
                    continue
                at = self._now()
                self._write(tx, booking_id, HELD, frozen="ordered", order_by=waiter, order_cause=cause,
                            order_pid=pid, order_mark=mark, ordered_at=at, reclaim_mb=int(reclaim),
                            order_sig=seal(key, booking_id, cause, at, waiter, pid, mark) if key else "")
                done.append(booking_id)
            for booking_id in actions.thaws:
                item = found.get(booking_id)
                if item is None or item.state != HELD or item.frozen != "frozen":
                    continue
                self._write(tx, booking_id, HELD, frozen="", started=self._now(), frozen_s=_frozen_for(item, now))
                done.append(booking_id)
        return list(dict.fromkeys(done))

    def _path(self, found: dict, mine: Booking, share: str, skip=(), lights=frozenset()) -> str | None:
        """How `mine` is admitted now, or None while it waits its turn. First come, first served unless the share is
        fair and `mine` waits under rule 2; then it goes when it is the fair choice, and also first come, first served
        when the fair choice waits under an older rule - that one knows nothing of being chosen, and waits for whoever
        is ahead of it, so yielding to it would leave the slot empty (project share design, section 3)."""
        ahead = any(_order(item) < _order(mine) and self.live(item) and item.id not in lights
                    for item in self.waiters(found))
        if share != "fair" or (mine.rule or 0) < 2:
            return None if ahead else "fifo"
        moment = self.clock() if self.clock else dt.datetime.now(dt.timezone.utc)
        choice = fair_choice(found, self.live, moment, frozenset(skip) | lights)
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
                if item.frozen == "frozen" and not _thaw_command(item, self.procs):
                    continue   # kept: a frozen command must not stay in swap for ever, so the next look tries again
                cut = {"cut": True} if state == RELEASED else {}   # a run cut short measures nothing; never dropped
                swept.append(self._write(tx, item.id, state, why="no process behind it", **cut))
        return swept
