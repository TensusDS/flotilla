"""Waiting for the lane and taking it (spec, section 9).

A caller joins the queue, then asks all five questions each time it polls: the booking log (live holders against
the capacity, and whether anyone who came earlier still waits), a foreign run computing (it uses a slot), memory
under the floor and CI on this machine (they block outright), and anything that could not be asked (it blocks
too). `--wait` bounds only the caller's own waiting; when it runs out, the refusal names what is in the way and the
wait is recorded as expired. A caller interrupted while waiting leaves the queue on the way out.
"""

from __future__ import annotations

import contextlib
import os
import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from flotilla.lane import book

POLL = 15.0
#: How long a waiter lets the fair choice keep a free slot before passing it over: two of its polls. A choice its own
#: project's checks hold back (CI, a memory floor, the foreign runs its patterns count) never asks for the slot, and
#: waiting on it for ever would leave the slot empty while others wait (final review of 0.25.0, I1).
FAIR_GRACE = 2 * POLL
#: Answers that hold the lane whatever the capacity: CI on this machine, memory under the floor, no room on the disk.
OUTRIGHT = ("ci", "memory", "disk")
ENV = "FLOTILLA_LANE_BOOKING"   # set for a booked command, so a booking inside it reuses the one it runs under


class LaneRefused(RuntimeError):
    """The lane could not be taken within the wait; the message names what is in the way."""


@dataclass(frozen=True)
class Grant:
    booking: object | None
    reading: object
    why: str
    measured: dict = field(default_factory=dict)   # what the holder's run took; `held` records it on release


def _order(item) -> int:
    return book._order(item)


def in_the_way(lanes, reading, capacity: int, mine: str = "", found: dict | None = None, share: str = "fifo") -> str:
    import datetime as dt
    parts = []
    found = lanes.bookings() if found is None else found
    me = found.get(mine)
    now = dt.datetime.now(dt.timezone.utc)
    lights: set[str] = set()   # a heavy waiter waits for no light run: they are left out of its reason (0.28.1)
    if me is not None and (me.rule or 0) >= book.RULE and not book.older_waiting(found, lanes.live, mine):
        from flotilla.lane import estimate
        hist = estimate.history(found, now=now)
        from flotilla.lane import machine
        if not book.is_light(me, hist, machine.cpus()):
            lights = {item.id for item in found.values() if item.state == book.WAITING and lanes.live(item)
                      and book.is_light(item, hist)}
            lights |= {item.id for item in found.values() if item.state == book.HELD and item.path.startswith("light")}
    if share == "fair" and me is not None and (me.rule or 0) >= 2:
        choice = book.fair_choice(found, lanes.live, now, frozenset(lights))
        if choice is not None and choice.id != mine:
            served = book.last_served(found, now).get(choice.project, "")
            parts.append(f"`{choice.project or 'no project'}` goes first: {choice.id} {choice.who}, its project served "
                         f"{served or 'not in 24 h'}")
    held = [item for item in lanes.holders(found) if lanes.live(item) and item.id not in lights]
    if held:
        parts.append("held by " + "; ".join(f"{item.who} ({item.note or 'no note'}) since {item.since}"
                                            for item in held))
    ahead = [item for item in lanes.waiters(found) if mine and me is not None and item.id != mine
             and lanes.live(item) and _order(item) < _order(me) and item.id not in lights]
    if ahead:
        parts.append("waiting ahead: " + "; ".join(f"{item.id} {item.who} ({item.note or 'no note'})"
                                                    for item in ahead))
    parts += [f"{answer.question}: {answer.text}" for answer in reading.answers if answer.blocks is not False]
    return "; ".join(parts) or f"someone who came earlier is waiting, or all {capacity} slot(s) are taken"


def acquire(lanes, read_machine, *, who: str, note: str, capacity: int | Callable[[], int], wait: float,
            share: str | Callable[[], str] = "fifo",
            pid: int | None = None, mark: str = "", run_for: str = "", by_hand: bool = False, poll: float = POLL,
            clock=time.monotonic, sleep=time.sleep, say=None, command: str = "", ladder=(), will_run=(),
            project: str = "", room: Callable | None = None, jitter=lambda: random.uniform(0.0, 1.0),
            value: Callable | None = None, kind: str = "", pinned: bool = False) -> Grant:
    """`room(reading)`: what a light run may take on this poll (lane light runs design); None, the count alone.
    `value(reading)`: what a grant by value reads on this poll (lane shared machine design); with it, the run is
    admitted by memory and priority while no waiter of an older flotilla is live."""
    mine = lanes.enqueue(who, note, pid=pid, mark=mark, run_for=run_for, command=command, ladder=ladder,
                         will_run=will_run, project=project, kind=kind, pinned=pinned)
    deadline = clock() + max(wait, 0)
    said = ""
    seen: dict[str, float] = {}   # each fair choice this waiter was refused for, and since when
    skip: set[str] = set()
    reading, read_at = None, 0.0
    try:
        while True:
            stamp = lanes.stamp() if hasattr(lanes, "stamp") else None   # before the look: a release during it wakes
            if reading is None or clock() - read_at >= poll:
                # the machine - process table, CPU samples, CI through `gh` - is read once a poll; a wake on a journal
                # write decides on the journal with the last reading, so N waiters woken by every write cost N
                # journal folds, not N machine reads (0.28.1, the regression review of 0.28.0)
                reading, read_at = read_machine(), clock()
            slots = capacity() if callable(capacity) else capacity   # read each poll: a raise reaches the queue
            mode = share() if callable(share) else share
            outright = [answer for answer in reading.answers
                        if answer.question in OUTRIGHT and answer.blocks is not False]
            unknown = [answer for answer in reading.answers if answer.blocks is None]
            space = room(reading) if room is not None else None
            worth = value(reading) if value is not None else None
            if not outright and not unknown:
                granted = lanes.grant(mine.id, slots=slots - len(reading.computing), by_hand=by_hand,
                                     share=mode, skip=frozenset(skip), room=space, value=worth)
                if granted is not None:
                    return Grant(granted, reading, "")
                choice = getattr(lanes, "passed_for", "")
                if choice and choice not in skip and clock() - seen.setdefault(choice, clock()) >= FAIR_GRACE:
                    skip.add(choice)   # it has had the free slot for two polls and not taken it: ask once more now
                    granted = lanes.grant(mine.id, slots=slots - len(reading.computing), by_hand=by_hand,
                                          share=mode, skip=frozenset(skip), room=space, value=worth)
                    if granted is not None:
                        return Grant(granted, reading, "")
            why = in_the_way(lanes, reading, slots, mine.id, share=mode)
            light = getattr(lanes, "waits_for", "")
            if light and not outright and not unknown:   # a light run waits for room beside the heavy ones, or a place
                why = f"{why}; as a light run beside them: {light}"
            lasting = [answer for answer in unknown if answer.lasting]
            remaining = deadline - clock()
            if lasting or remaining <= 0:
                lanes.expire(mine.id, why if lasting else f"waited {wait:g} s; {why}")
                return Grant(None, reading, why)
            if say is not None and why != said:
                say(f"waiting for the lane: {why}")
                said = why
            # never past the next look at the machine: a wake that reused the last one must not push it back
            _nap(lanes, min(poll + jitter(), max(1.0, read_at + poll - clock())), remaining, clock=clock,
                 sleep=sleep, stamp=stamp)
    except BaseException:
        lanes.leave(mine.id)
        raise


def _nap(lanes, poll: float, remaining: float, *, clock, sleep, stamp=None) -> None:
    """Wait for the next poll a second at a time, and wake at once when the journal changed: a release reaches the
    next waiter in about a second, not a poll's 15 (lane light runs design, section 4). Only a `stat` each second;
    the machine is read again by the caller."""
    until = clock() + min(poll, remaining)
    while clock() < until:
        sleep(min(1.0, until - clock()))
        if stamp is not None and lanes.stamp() != stamp:
            return


@contextlib.contextmanager
def held(lanes, read_machine, *, who: str, note: str, capacity: int | Callable[[], int], wait: float, table,
         share: str | Callable[[], str] = "fifo",
         run_for: str = "", poll: float = POLL, clock=time.monotonic, sleep=time.sleep, say=None, command: str = "",
         ladder=(), will_run=(), project: str = "", room: Callable | None = None, value: Callable | None = None,
         kind: str = "", pinned: bool = False):
    outer = lanes.bookings().get(os.environ.get(ENV, ""))
    if outer is not None and outer.state == "held" and lanes.live(outer) and \
            (outer.pid is None or outer.pid in set(table.ancestors(os.getpid()))):
        # a run inside a booking is part of it: the owner releases it; what this run measured is recorded beside
        # it, already released, so the inner command's history learns without booking twice
        grant = Grant(outer, None, f"inside booking {outer.id}")
        try:
            yield grant
        finally:
            if grant.measured:
                lanes.record(outer.id, who=who, note=note, command=command, ladder=ladder, will_run=will_run,
                             project=project, measured=grant.measured)
        return
    pid = os.getpid()
    grant = acquire(lanes, read_machine, who=who, note=note, capacity=capacity, wait=wait, share=share, pid=pid,
                    mark=table.start_mark(pid) or "", run_for=run_for, poll=poll, clock=clock, sleep=sleep, say=say,
                    command=command, ladder=ladder, will_run=will_run, project=project, room=room, value=value,
                    kind=kind, pinned=pinned)
    if grant.booking is None:
        raise LaneRefused(grant.why)
    previous = os.environ.get(ENV)
    os.environ[ENV] = grant.booking.id
    watcher = None
    if value is not None and grant.booking.path.startswith("value") and not grant.booking.pinned:
        from flotilla.lane import freeze, watch
        watcher = watch.Watch(lanes, grant.booking.id, freeze.slot(), value)   # it obeys this booking's orders
        watcher.start()
    try:
        yield grant
    finally:
        if watcher is not None:
            watcher.stop()
        lanes.release(grant.booking.id, measured=grant.measured)
        if previous is None:
            os.environ.pop(ENV, None)
        else:
            os.environ[ENV] = previous
