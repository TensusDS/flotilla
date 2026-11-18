"""What the lane tells a project's orchestrator (field notes 2026-11-16).

Two things nobody saw that night: a held run that hung - long past its estimate, computing next to nothing - and a
run cut short because its client process was killed (Claude Code ends background shells under memory pressure), which
left no verdict and no trace on the row. The daemon finds both (`Scheduler._hung`, and the sweep of a booking whose
client is gone); this turns them into the orchestrator's items for the project they belong to. The lane stops
neither by itself here: a hung run is its owner's to stop, a lost one its owner's to book again.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from flotilla.core.text import visible
from flotilla.watch.whose import Item

HUNG, LOST, MEMORY, OUTSIDE, STARVED = "lane-hung", "lane-lost", "lane-memory", "lane-outside", "lane-starved"
#: How long a run cut short stays an item, unless its owner books the same work again sooner.
LOST_FOR = dt.timedelta(hours=6)


def _when(value) -> dt.datetime | None:
    try:
        found = dt.datetime.fromisoformat(value) if isinstance(value, str) and value else None
    except ValueError:
        return None
    return found if found is None or found.tzinfo else found.replace(tzinfo=dt.timezone.utc)


def number(value) -> float | None:
    """A finite number from the daemon's beat, or None: the beat is a file, and a malformed one must not break a
    listing or a prompt (review of dba1899, 11)."""
    import math
    try:
        found = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return found if math.isfinite(found) else None


def hung_words(hung: dict) -> tuple[int, str, int]:
    """(minutes held, how the estimate reads, minutes quiet) of a beat's hung entry."""
    estimate = number(hung.get("estimate_s"))
    told = f"its estimate {round(estimate)} s" if estimate else "no estimate"
    return round((number(hung.get("ran_s")) or 0) / 60), told, round((number(hung.get("quiet_s")) or 0) / 60)


def _what(who: str, note: str, run_for: str) -> str:
    target = f" for `{visible(run_for)}`" if run_for else ""
    return f"{visible(who) or 'a session'}'s run ({visible(note) or 'no note'}){target}"


def items(state: Path, project: str, now: dt.datetime, profile: dict | None = None) -> list[Item]:
    """The lane's items for `project` (a repo key): its held runs that look hung, and its runs cut short."""
    from flotilla.core.storage import LocalLogStore, StorageCorrupt
    from flotilla.lane import book, daemon
    root = Path(state) / "lane"
    if not root.is_dir() or not project:
        return []
    found: list[Item] = []
    beat = None if daemon.gone(root) else daemon.heartbeat(root)   # a dead daemon's last beat is no news
    for booking, hung in sorted((beat.hung if beat is not None else {}).items()):
        if not isinstance(hung, dict) or hung.get("project") != project:
            continue
        held, told, quiet = hung_words(hung)
        run_for = str(hung.get("run_for") or "")
        found.append(Item(HUNG, visible(run_for),
                          f"{_what(str(hung.get('who') or ''), str(hung.get('note') or ''), run_for)} looks hung: "
                          f"{held} min held ({told}), next to no CPU for {quiet} min - ask its owner to stop it "
                          f"(`flotilla lane stop`) and look for what it waits on; `flotilla lane` shows "
                          f"{visible(str(booking))}", "", who=f"{booking} hung"))
    try:
        bookings = book.Book(LocalLogStore(root), None).bookings()
    except (StorageCorrupt, OSError, ValueError):
        return found
    order = list(bookings.values())   # the journal's order: a booking made later stands after it
    found.extend(memory_items(order, project, _available(), *_line_and_floor(profile or {}), live=_live))
    found.extend(outside_items(order, project, beat.outside if beat is not None else {}))
    found.extend(starved_items(order, project))
    for place, item in enumerate(order):
        ended = _when(item.ended or item.since)
        if (item.project != project or item.state != book.RELEASED or not item.cut or ended is None
                or now - ended > LOST_FOR):
            continue
        again = any(other.project == project and other.who == item.who
                    and (other.run_for, other.note) == (item.run_for, item.note) for other in order[place + 1:])
        if again:
            continue   # its owner booked the same work again: the item is answered
        found.append(Item(LOST, item.run_for,
                          f"{_what(item.who, item.note, item.run_for)} was cut short at {ended:%H:%M} UTC with no "
                          "verdict: its client process was killed (most often by Claude Code under memory pressure, "
                          "or its session ending). Ask its owner to book it again - narrower, one run at a time while "
                          "memory is short", item.ended or item.since, who=f"{item.id} lost"))
    return found


def outside_items(order, project: str, outside: dict, *, sessions=None) -> list[Item]:
    """The project's heavy waiters the lane does not drain for, because the memory they need is held outside it:
    drained empty, the lane would still not hold them. The person's ruling, 2026-11-17: the lane stays open to the
    other runs, and the orchestrator is told what would let them in."""
    from flotilla.lane import book
    found: list[Item] = []
    held = None
    for item in order:
        entry = outside.get(item.id)
        if item.project != project or item.state != book.WAITING or not isinstance(entry, dict):
            continue
        need, room = number(entry.get("need_mb")), number(entry.get("room_mb"))
        if need is None or room is None:
            continue
        if held is None:
            held = (sessions or _sessions)() or ()
        whose = (f" {held[0]} Claude Code process(es) hold {held[1] / 1024:.1f} GB." if len(held) == 2 else "")
        found.append(Item(OUTSIDE, visible(item.run_for),
                          f"{_what(item.who, item.note, item.run_for)} ({visible(item.id)}) needs {round(need)} MB "
                          f"and would not fit even with the lane empty: {max(0, round(room))} MB of room over the "
                          f"lane's floor then.{whose} The lane lets other runs pass it rather than drain for nothing; "
                          "it starts once that memory is free. Retire idle seats, or narrow the run", "",
                          who=f"{item.id} outside"))
    return found


def starved_items(order, project: str) -> list[Item]:
    """The project's runs larger than this machine holds, waiting or running: no wait gives them the memory, so the
    lane starts each alone in an empty lane, and the orchestrator is told it may be killed (the person's ruling,
    2026-11-18). Its mark is on the booking, written by the daemon (`book.Book.mark_starved`)."""
    from flotilla.lane import book
    found: list[Item] = []
    for item in order:
        need, room = item.starved_need_mb, item.starved_room_mb
        if item.project != project or item.state not in (book.WAITING, book.HELD) or need is None or room is None:
            continue
        found.append(Item(STARVED, visible(item.run_for),
                          f"{_what(item.who, item.note, item.run_for)} ({visible(item.id)}) needs {need} MB, more "
                          f"than this machine gives even with the lane empty and every Claude Code session retired "
                          f"but the orchestrator's and its own: {room} MB over the lane's floor at most. The lane "
                          "starts it alone in an empty lane, and it may still be killed. Split or narrow the run", "",
                          who=f"{item.id} starved"))
    return found


def _sessions():
    """(Claude Code processes, their MB) on this machine, or None: what most often holds the memory a run lacks."""
    from flotilla.core import resources
    return resources.claude_memory()


def _available() -> int | None:
    from flotilla.core import resources
    return resources.available_mb()


def _line_and_floor(profile: dict) -> tuple[int | None, int]:
    from flotilla.lane import machine
    return machine.reap_line_mb(machine._host_total()), machine.floor_here(profile)


def _live():
    """Whether a booking's process lives, by the process table: a booking whose process is gone loses nothing."""
    from flotilla.lane.procs import ProcessTable
    try:
        table = ProcessTable.for_machine()
    except OSError:
        return lambda item: True
    return lambda item: table.alive(item.pid, item.mark) if item.pid else True


def memory_items(order, project: str, available: int | None, line: int | None, floor: int, *,
                 live=None) -> list[Item]:
    """One item while the machine's free memory is under the lane's floor and Claude Code's line can be drawn: the
    project's runs booked now are the ones Claude Code stops if their seats sit idle (its rule: idle 30 min, free
    memory under a tenth and under 4 GiB). Nothing while the project books nothing: there is nothing of it to lose."""
    from flotilla.lane import book
    if available is None or line is None or available >= floor:
        return []
    alive = live() if live is not None else (lambda item: True)   # the process table, read only when short
    booked = [item for item in order if item.project == project and item.state in (book.HELD, book.WAITING)
              and alive(item)]
    if not booked:
        return []
    named = ", ".join(f"{item.id} {visible(item.who) or 'a session'} "
                      f"({'running' if item.state == book.HELD else 'waiting'})" for item in booked[:6])
    more = f" and {len(booked) - 6} more" if len(booked) > 6 else ""
    return [Item(MEMORY, "", f"memory is short: {available} MB free, under the {floor} MB the lane keeps; under "
                             f"{line} MB Claude Code stops the background commands of seats idle 30 min, and this "
                             f"project's lane runs with them: {named}{more}. Retire idle seats now, or keep these "
                             "seats busy until their runs end", "", who="memory near Claude Code's line")]
