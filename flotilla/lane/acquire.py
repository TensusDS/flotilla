"""Waiting for the lane and taking it (spec, section 9).

A caller joins the queue, then asks all four questions each time it polls: the booking log (live holders against
the capacity, and whether anyone who came earlier still waits), a foreign run computing (it uses a slot), CI on
this machine (it blocks outright) and anything that could not be asked (it blocks too). `--wait` bounds only the
caller's own waiting; when it runs out, the refusal names what is in the way and the wait is recorded as expired.
A caller interrupted while waiting leaves the queue on the way out.
"""

from __future__ import annotations

import contextlib
import os
import time
from dataclasses import dataclass

POLL = 15.0


class LaneRefused(RuntimeError):
    """The lane could not be taken within the wait; the message names what is in the way."""


@dataclass(frozen=True)
class Grant:
    booking: object | None
    reading: object
    why: str


def in_the_way(lanes, reading, capacity: int) -> str:
    parts = []
    held = [item for item in lanes.holders() if lanes.live(item)]
    if held:
        parts.append("held by " + "; ".join(f"{item.who} ({item.note or 'no note'}) since {item.since}"
                                            for item in held))
    parts += [f"{answer.question}: {answer.text}" for answer in reading.answers if answer.blocks is not False]
    return "; ".join(parts) or f"someone who came earlier is waiting, or all {capacity} slot(s) are taken"


def acquire(lanes, read_machine, *, who: str, note: str, capacity: int, wait: float, pid: int | None = None,
            mark: str = "", run_for: str = "", poll: float = POLL, clock=time.monotonic, sleep=time.sleep) -> Grant:
    mine = lanes.enqueue(who, note, pid=pid, mark=mark, run_for=run_for)
    deadline = clock() + max(wait, 0)
    try:
        while True:
            reading = read_machine()
            ci = [answer for answer in reading.answers if answer.question == "ci" and answer.blocks is not False]
            unknown = [answer for answer in reading.answers if answer.blocks is None]
            if not ci and not unknown:
                granted = lanes.grant(mine.id, slots=capacity - len(reading.computing))
                if granted is not None:
                    return Grant(granted, reading, "")
            if clock() >= deadline:
                why = in_the_way(lanes, reading, capacity)
                lanes.expire(mine.id, f"waited {wait:g} s; {why}")
                return Grant(None, reading, why)
            sleep(poll)
    except BaseException:
        lanes.leave(mine.id)
        raise


@contextlib.contextmanager
def held(lanes, read_machine, *, who: str, note: str, capacity: int, wait: float, table, run_for: str = "",
         poll: float = POLL, clock=time.monotonic, sleep=time.sleep):
    pid = os.getpid()
    grant = acquire(lanes, read_machine, who=who, note=note, capacity=capacity, wait=wait, pid=pid,
                    mark=table.start_mark(pid) or "", run_for=run_for, poll=poll, clock=clock, sleep=sleep)
    if grant.booking is None:
        raise LaneRefused(grant.why)
    try:
        yield grant
    finally:
        lanes.release(grant.booking.id)
