"""A rig session's line: the order its waiting runs stand in, and when each one starts and ends by estimates (rig
queue design, A2-A3).

The model is a plain one and says so: each ready machine of the session serves one run after another, and the next
run in line takes the machine that frees first. Packing two shared runs on one machine only makes runs start sooner,
so a start reads "around HH:MM or sooner". A run never measured counts at the ceiling it was queued with, and the
count of such runs travels with the answer instead of being hidden in it. With no machine up there is no number.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from flotilla.rig import journal as j
from flotilla.rig import packing
from flotilla.rig.profile import DEFAULT_RUN_SECONDS

LEAST = dt.timedelta(minutes=1)   # what a running run past its estimate is still given


@dataclass(frozen=True)
class Place:
    run: j.Run
    number: int
    ahead: str                    # the run just ahead in line, "" for the first
    starts: dt.datetime | None
    ends: dt.datetime | None
    unmeasured: int               # runs counted at their ceiling, up to and including this one
    fits: bool | None             # ends before its session does; None when either end is unknown


def _when(text: str) -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat(text) if text else None
    except ValueError:
        return None


def _span(run: j.Run, hist) -> tuple[dt.timedelta, bool]:
    """How long `run` takes by estimates, and whether that is its ceiling because it was never measured."""
    seconds = packing.estimate(run.ladder, hist).seconds
    if seconds is not None:
        return dt.timedelta(seconds=seconds), False
    return dt.timedelta(seconds=run.ceiling or DEFAULT_RUN_SECONDS), True


def places(rig: j.Rig, session_id: str, now: dt.datetime, alive) -> list[Place]:
    runs, machines = rig.runs(), rig.machines()
    session = rig.sessions().get(session_id)
    until = _when(session.until) if session is not None else None
    hist = packing.history(runs, now=now)
    lanes, unmeasured = {}, 0
    for machine in machines.values():
        if machine.session == session_id and machine.state in (j.READY, j.BUSY):
            lanes[machine.id] = now
    for run in runs.values():
        if run.state != j.RUNNING or run.machine not in lanes:
            continue
        span, guessed = _span(run, hist)
        unmeasured += guessed
        began = _when(run.command_at) or now   # still in setup: its whole estimate is ahead of it
        lanes[run.machine] = max(lanes[run.machine], began + span, now + LEAST)
    waiting = sorted((run for run in runs.values() if run.state == j.WAITING and run.session == session_id
                      and alive(run.pid, run.mark)), key=lambda run: int(run.id[1:]))
    found, ahead = [], ""
    for number, run in enumerate(waiting, start=1):
        span, guessed = _span(run, hist)
        unmeasured += guessed
        starts = ends = fits = None
        if lanes:
            lane = min(lanes, key=lambda mid: (lanes[mid], mid))
            starts, ends = lanes[lane], lanes[lane] + span
            lanes[lane] = ends
            fits = ends <= until if until is not None else None
        found.append(Place(run, number, ahead, starts, ends, unmeasured, fits))
        ahead = run.id
    return found


def _ordinal(number: int) -> str:
    suffix = "th" if number % 100 in (11, 12, 13) else {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")
    return f"{number}{suffix}"


def says(place: Place, session: j.Session) -> str:
    """The one sentence a waiting run prints and the screen reuses: its place, when it starts and ends by estimates,
    and when its session ends."""
    from flotilla.rig.surface import _hhmm
    text = f"{place.run.id} is {_ordinal(place.number)} in line" + (f", after {place.ahead}" if place.ahead else "")
    until = _when(session.until)
    if place.starts is None:
        text += "; a machine is raised first, so there is no start time yet"
    else:
        text += (f"; by estimates it starts around {_hhmm(place.starts)} or sooner and ends around "
                 f"{_hhmm(place.ends)}")
        if place.unmeasured:
            text += f" ({place.unmeasured} run(s) not measured yet, counted at their ceiling)"
    if until is not None:
        text += f"; session {session.id} ends {_hhmm(until)}"
    return text


def warning(place: Place, session: j.Session) -> str:
    """What a run that by estimates ends after its session is told before it starts, once; "" when it fits or when
    either end is unknown. It is told, not refused: the estimate may be wrong, and its own ceiling bounds it."""
    from flotilla.rig.surface import _hhmm
    if place.fits is not False:
        return ""
    return (f"by estimates {place.run.id} ends around {_hhmm(place.ends)}, after session {session.id} ends at "
            f"{_hhmm(_when(session.until))} - lower its ceiling with --max, or run it in the next session")
