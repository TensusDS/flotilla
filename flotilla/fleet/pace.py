"""How fast this fleet works: the pace signal of fleet sizing (design, section 3).

Two medians from the ledger's own history: how long an author takes from a claim (or a return for fixing) to the
handover, and how long a handover waits for its verdict (accept or fix). Under three samples each falls back to a
default - one handover an author-hour, twenty minutes a review - and says so: a young ledger has no pace yet.
"""

from __future__ import annotations

import datetime as dt
import statistics
from dataclasses import dataclass

DEFAULT_RATE = 1.0
DEFAULT_REVIEW_HOURS = 0.33
MIN_SAMPLES = 3
STARTS = frozenset({"claim", "fix"})
VERDICTS = frozenset({"accept", "fix"})


@dataclass(frozen=True)
class Pace:
    handovers_per_author_hour: float
    review_hours: float
    measured: bool
    note: str


def _at(text) -> dt.datetime | None:
    try:
        when = dt.datetime.fromisoformat(str(text))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=dt.timezone.utc)


def pace(rows, *, now: dt.datetime, window_days: int = 30) -> Pace:
    """The fleet's measured pace over rounds that ended in the last `window_days`, or the defaults where fewer than
    three rounds were seen."""
    since = now - dt.timedelta(days=window_days)
    authoring, reviewing = [], []
    for row in (rows.values() if isinstance(rows, dict) else rows):
        started = handed = None
        for event in row.history:
            when, move = _at(event.get("at")), event.get("move")
            if when is None or when > now:   # an event from the future is no pace anyone kept (review of 0.7.14)
                continue
            if move == "hand":
                if started is not None and since <= when and when >= started:
                    authoring.append((when - started).total_seconds() / 3600)
                started, handed = None, when
            elif move in VERDICTS and handed is not None:
                if since <= when and when >= handed:
                    reviewing.append((when - handed).total_seconds() / 3600)
                handed = None
            if move in STARTS:
                started = when
    notes, measured = [], True
    if len(authoring) >= MIN_SAMPLES and statistics.median(authoring) > 0:
        rate = round(1 / statistics.median(authoring), 2)
        notes.append(f"authoring: median of {len(authoring)} rounds")
    else:
        rate, measured = DEFAULT_RATE, False
        notes.append(f"authoring: {len(authoring)} round(s) in {window_days} days, default {DEFAULT_RATE}/h")
    if len(reviewing) >= MIN_SAMPLES:
        review = round(statistics.median(reviewing), 2)
        notes.append(f"review: median of {len(reviewing)} verdicts")
    else:
        review, measured = DEFAULT_REVIEW_HOURS, False
        notes.append(f"review: {len(reviewing)} verdict(s) in {window_days} days, default {DEFAULT_REVIEW_HOURS} h")
    return Pace(rate, review, measured, "; ".join(notes))
