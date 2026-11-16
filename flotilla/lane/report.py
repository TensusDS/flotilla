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

HUNG, LOST = "lane-hung", "lane-lost"
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


def items(state: Path, project: str, now: dt.datetime) -> list[Item]:
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
