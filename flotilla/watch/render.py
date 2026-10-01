"""How a watcher's items read in a session: the moves first, ages printed, never more than fits.

`UserPromptSubmit` output is capped at 10,000 characters; two blocks of 20 lines of 200 characters stay under it.
"""

from __future__ import annotations

import datetime as dt

from flotilla.core.text import visible
from flotilla.watch.whose import WORKING, Item

MAX_LINES = 20
LINE_CHARS = 200
ORDER = {"done": -2, "question": -1, "person": -1, "idle": 7, "helper": 3, "ball": 0, "hold": 1, "unread": 2,
         "dropped": 3, "nobody": 4, "break": 5, "deviation": 6, "waiting": 8, "seats": 9}


def age(since: str, now: dt.datetime) -> str:
    try:
        moment = dt.datetime.fromisoformat(since)
    except (TypeError, ValueError):
        return "age unknown"
    seconds = max(0, int((now - moment).total_seconds()))
    if seconds < 3600:
        return f"{seconds // 60} min"
    if seconds < 86400:
        return f"{seconds // 3600} h"
    return f"{seconds // 86400} d"


def _cut(line: str) -> str:
    return line if len(line) <= LINE_CHARS else line[:LINE_CHARS - 3] + "..."


def lines(items: list[Item], now: dt.datetime, *, limit: int = MAX_LINES) -> list[str]:
    working = [item for item in items if item.kind == WORKING]
    rest = sorted((item for item in items if item.kind != WORKING), key=lambda item: (ORDER.get(item.kind, 7),
                                                                                     item.since))
    # an item's text carries other sessions' words (notes, whys): one line each, nothing hidden (F12)
    out = [_cut(visible(f"  {item.branch + ': ' if item.branch else ''}{item.text}"
                        f"{f' ({age(item.since, now)})' if item.since else ''}")) for item in rest]
    if working:
        oldest = min(working, key=lambda item: item.since)
        out.append(_cut(f"  {len(working)} claimed branch(es) in your hands; oldest {oldest.branch} "
                        f"({age(oldest.since, now)}): hand over, or record whom you wait on"))
    if len(out) > limit:
        out = out[:limit - 1] + [f"  ... {len(out) - limit + 1} more; run `flotilla status`"]
    return out
