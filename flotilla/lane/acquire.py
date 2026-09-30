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
import time
from dataclasses import dataclass

POLL = 15.0
#: Answers that hold the lane whatever the capacity: CI on this machine, and memory under the floor.
OUTRIGHT = ("ci", "memory")
ENV = "FLOTILLA_LANE_BOOKING"   # set for a booked command, so a booking inside it reuses the one it runs under


class LaneRefused(RuntimeError):
    """The lane could not be taken within the wait; the message names what is in the way."""


@dataclass(frozen=True)
class Grant:
    booking: object | None
    reading: object
    why: str


def _order(item) -> int:
    return int(item.id[1:]) if item.id[1:].isdigit() else 0


def in_the_way(lanes, reading, capacity: int, mine: str = "") -> str:
    parts = []
    held = [item for item in lanes.holders() if lanes.live(item)]
    if held:
        parts.append("held by " + "; ".join(f"{item.who} ({item.note or 'no note'}) since {item.since}"
                                            for item in held))
    ahead = [item for item in lanes.waiters() if mine and item.id != mine and lanes.live(item)
             and _order(item) < int(mine[1:] or 0)]
    if ahead:
        parts.append("waiting ahead: " + "; ".join(f"{item.id} {item.who} ({item.note or 'no note'})"
                                                    for item in ahead))
    parts += [f"{answer.question}: {answer.text}" for answer in reading.answers if answer.blocks is not False]
    return "; ".join(parts) or f"someone who came earlier is waiting, or all {capacity} slot(s) are taken"


def acquire(lanes, read_machine, *, who: str, note: str, capacity: int, wait: float, pid: int | None = None,
            mark: str = "", run_for: str = "", by_hand: bool = False, poll: float = POLL, clock=time.monotonic,
            sleep=time.sleep, say=None) -> Grant:
    mine = lanes.enqueue(who, note, pid=pid, mark=mark, run_for=run_for)
    deadline = clock() + max(wait, 0)
    said = ""
    try:
        while True:
            reading = read_machine()
            outright = [answer for answer in reading.answers
                        if answer.question in OUTRIGHT and answer.blocks is not False]
            unknown = [answer for answer in reading.answers if answer.blocks is None]
            if not outright and not unknown:
                granted = lanes.grant(mine.id, slots=capacity - len(reading.computing), by_hand=by_hand)
                if granted is not None:
                    return Grant(granted, reading, "")
            why = in_the_way(lanes, reading, capacity, mine.id)
            lasting = [answer for answer in unknown if answer.lasting]
            remaining = deadline - clock()
            if lasting or remaining <= 0:
                lanes.expire(mine.id, why if lasting else f"waited {wait:g} s; {why}")
                return Grant(None, reading, why)
            if say is not None and why != said:
                say(f"waiting for the lane: {why}")
                said = why
            sleep(min(poll, remaining))
    except BaseException:
        lanes.leave(mine.id)
        raise


@contextlib.contextmanager
def held(lanes, read_machine, *, who: str, note: str, capacity: int, wait: float, table, run_for: str = "",
         poll: float = POLL, clock=time.monotonic, sleep=time.sleep, say=None):
    outer = lanes.bookings().get(os.environ.get(ENV, ""))
    if outer is not None and outer.state == "held" and lanes.live(outer) and \
            (outer.pid is None or outer.pid in set(table.ancestors(os.getpid()))):
        yield Grant(outer, None, f"inside booking {outer.id}")
        return
    pid = os.getpid()
    grant = acquire(lanes, read_machine, who=who, note=note, capacity=capacity, wait=wait, pid=pid,
                    mark=table.start_mark(pid) or "", run_for=run_for, poll=poll, clock=clock, sleep=sleep, say=say)
    if grant.booking is None:
        raise LaneRefused(grant.why)
    previous = os.environ.get(ENV)
    os.environ[ENV] = grant.booking.id
    try:
        yield grant
    finally:
        lanes.release(grant.booking.id)
        if previous is None:
            os.environ.pop(ENV, None)
        else:
            os.environ[ENV] = previous
