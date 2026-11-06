"""The lane's bookings and the machine, as the policy reads them (lane shared machine design, sections 2 and 4).

`view` turns the journal's live bookings and one reading of the machine into `policy.Run`s and a `policy.Machine`;
the grant calls it inside the journal's lock, so every waiter decides on the same picture. Readings are callables,
so a test fixes them and the grant reads them only when it decides.
"""

from __future__ import annotations

import datetime as dt
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from flotilla.lane import book, policy

#: A run started within this window is counted at least at its expected memory: a just-started process has not
#: grown into its memory yet, and the next waiter would read it as free (section 2.2). Builder's number, awaits the
#: field test.
WINDOW_S = 30.0
#: The expected memory of a run never measured, while this machine has too few first runs to say (the person's
#: ruling of 2026-11-06: the 75th percentile of each command's first run, this until FIRST_RUNS_NEEDED are measured).
DEFAULT_MB = 1024
FIRST_RUNS_NEEDED = 20
#: How long a victim has to answer a freeze order: its wrapper wakes within 5 s and moves about a gigabyte to swap
#: in 2.5 s. Chosen with the person (2026-11-06); awaits the field test.
ANSWER_S = 60.0
#: A frozen booking whose watch has not beaten this long is thawed by any grant and never frozen again: its wrapper
#: may be hung, and a frozen command must not stay frozen for it (the person's ruling, 2026-11-06).
BEAT_STALE_S = 60.0
BEATS = "lane-beats"


@dataclass(frozen=True)
class Value:
    """What a grant by value reads, built each poll from settings and the machine."""
    cpus: int
    cpu_factor: float
    light_reserve: float
    immortal_s: float
    max_runs: int
    freeze: str                                     # freeze.SCOPE / SIGNAL / OFF
    available_mb: Callable[[], int | None]
    floor_mb: int
    swap_mb: Callable[[], tuple[int, int] | None]   # (free, total), freeze.swap_mb
    tree_mb: Callable[[int], int | None]            # a holder's process tree, now
    beat_age_s: Callable[[str], float | None]       # seconds since a booking's watch last beat; None: no beat file
    foreign: int = 0


def default_mb(found: dict) -> int:
    """The memory a never-measured run is expected to take: the 75th percentile of the measured peaks of each
    command's first run in this machine's journal (one per broadest own signature, in the order they ended);
    DEFAULT_MB while fewer than FIRST_RUNS_NEEDED are measured."""
    firsts: dict[str, int] = {}
    measured = [item for item in found.values() if item.state == book.RELEASED and item.peak_mb is not None
                and item.ended]
    for item in sorted(measured, key=lambda item: item.ended):
        own = [step for step in item.ladder if not step.startswith("project:")]
        if own:
            firsts.setdefault(own[-1], item.peak_mb)
    if len(firsts) < FIRST_RUNS_NEEDED:
        return DEFAULT_MB
    peaks = sorted(firsts.values())
    return peaks[int(0.75 * len(peaks))]


def beat(root, booking_id: str) -> None:
    """A watch says it is alive: the mtime of a small file beside the journal, not a journal line."""
    try:
        folder = Path(root) / BEATS
        folder.mkdir(parents=True, exist_ok=True)
        (folder / booking_id).touch()
    except OSError:
        pass


def beat_age(root, booking_id: str, *, now=time.time) -> float | None:
    try:
        return max(0.0, now() - (Path(root) / BEATS / booking_id).stat().st_mtime)
    except OSError:
        return None


def forget_beat(root, booking_id: str) -> None:
    try:
        (Path(root) / BEATS / booking_id).unlink()
    except OSError:
        pass


def by_value(found: dict, live) -> bool:
    """No live waiter under an older rule: one waits by the count and knows nothing of memory, so while it waits the
    lane counts every run as before (section 7)."""
    return not any(item.state == book.WAITING and live(item) and (item.rule or 0) < book.RULE
                   for item in found.values())


def _seconds(now: dt.datetime, text: str) -> float:
    moment = book._when(text)
    return max(0.0, (now - moment).total_seconds()) if moment is not None else 0.0


def _own(item, hist, cpus: int):
    """The run's own measured estimate, or None when nothing of its own command was measured (a project or fixed
    prior is no measurement of this run)."""
    from flotilla.lane import estimate
    found = estimate.for_booking(item, hist, cpus=cpus)
    return None if "prior" in found.source else found


def view(found: dict, hist, value: Value, *, now: dt.datetime, live, mine: str,
         share: str = "fifo") -> tuple[list[policy.Run], policy.Machine]:
    """The live bookings as the policy's runs, and the machine as it is now."""
    fallback = default_mb(found)
    available = value.available_mb()
    held = [item for item in found.values() if item.state == book.HELD and live(item)]
    waiting = [item for item in found.values() if item.state == book.WAITING and live(item)]
    trees = {item.id: value.tree_mb(item.pid) for item in held if item.pid is not None}
    give = None if available is None else available - value.floor_mb + sum(mb for mb in trees.values() if mb)
    holding = {item.order_by for item in held if item.frozen in book.FROZEN_STATES and item.order_by}
    projects: dict[str, int] = {}
    for item in held:
        projects[item.project] = projects.get(item.project, 0) + 1
    served = book.last_served(found, now) if share == "fair" else {}
    reserved, runs = 0, []
    for item in [*held, *waiting]:
        own = _own(item, hist, value.cpus)
        expected = own.peak_mb if own is not None and own.peak_mb is not None else fallback
        par = (own.peak_cores or own.cores) if own is not None and (own.peak_cores or own.cores) else value.cpus / 2
        older = (item.rule or 0) < book.RULE
        pinned, freezable = item.pinned, not older and not item.unanswered
        since_start = _seconds(now, item.started or item.since)
        before = item.ran_s or 0.0
        if item.state == book.WAITING:
            state, mem, ran = policy.WAITING, expected, before
        elif item.frozen == "frozen":
            moved = [mb for mb in (item.reclaim_mb, item.held_mb) if mb is not None]
            state, mem, ran = policy.FROZEN, (min(moved) if moved else expected), before
        else:
            state = policy.ORDERED if item.frozen == "ordered" else policy.RUNNING
            tree = trees.get(item.id)
            mem = tree if tree is not None else expected
            if since_start < WINDOW_S:
                reserved += max(0, expected - (tree or 0))
                mem = max(mem, expected)
            ran = before + since_start
        heavy = policy.is_heavy(mem, give, was=item.heavy)
        if item.state == book.HELD and item.pid is None:   # taken by hand: the whole machine, never frozen
            heavy, par, pinned = True, value.cpus, True
            mem = round((1 - value.light_reserve) * give) if give is not None else expected
        if older:
            pinned = True
        runs.append(policy.Run(
            item.id, state, int(mem), heavy, float(par), kind=item.kind if item.kind in policy.TYPES else "run",
            waited_s=_seconds(now, item.asked or item.since), ran_s=ran, since_start_s=since_start,
            order=book._order(item), fair=(projects.get(item.project, 0), served.get(item.project, ""))
            if share == "fair" else (), pinned=pinned, freezable=freezable,
            holds=item.state == book.WAITING and item.id in holding))
    swap = value.swap_mb()
    from flotilla.lane import freeze
    machine = policy.Machine(
        cpus=value.cpus, give_mb=give, free_mb=None if available is None else available - value.floor_mb - reserved,
        swap_free_mb=swap[0] if swap else None, swap_total_mb=swap[1] if swap else None,
        cpu_factor=value.cpu_factor, light_reserve=value.light_reserve, immortal_s=value.immortal_s,
        max_runs=value.max_runs, freeze=value.freeze != freeze.OFF)
    return runs, machine
