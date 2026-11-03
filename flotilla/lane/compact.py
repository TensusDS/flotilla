"""Compacting the lane journal (design 2026-11-03-lane-journal-compaction).

A booking's state is the fold of its events, so the journal is never cut by line count: a booking taken by hand stays
held however old its first line is. A compaction keeps by meaning - every live booking and every booking of the last
day as written (the fair share reads when each project was served), and each other ended booking an estimate reads as
one record holding what estimates read - and archives the rest. Only ordinary records: a seat on an older flotilla
folds the compacted journal as before.

Measured on a copy of the field journal of 2026-11-03: 25 624 records and 8.35 MB became 2856 records and 1.47 MB,
with every estimate usable that day unchanged; 4484 of its 5747 steps were one-off exact commands with one sample.
"""

from __future__ import annotations

import datetime as dt

from flotilla.lane import book, estimate
from flotilla.lane.signature import tier_signature

THRESHOLD = 2_000_000   # bytes: past this a write compacts (the person, 2026-11-03)
GROWTH = 1.5            # ... and only once the journal has grown this much past what the last compaction left
STALE = dt.timedelta(days=7)   # a step no estimate can read yet, whose newest sample is older, is not kept
WINDOW = dt.timedelta(days=30)   # what estimate.history reads
#: What estimate.history reads from an ended booking, besides its state and time.
ESTIMATE_FIELDS = ("project", "ladder", "ran", "seconds", "peak_mb", "cores", "busy", "verdict", "cut")


def _when(text) -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat(text) if isinstance(text, str) else None
    except ValueError:
        return None


def _tier_key(item, tier: dict) -> str:
    return f"setup:{item.project}" if tier.get("kind") == "setup" else tier_signature(tier["name"], project=item.project)


def _measured_tiers(item) -> list[dict]:
    return [tier for tier in item.ran or []
            if isinstance(tier, dict) and tier.get("name") and tier.get("seconds") is not None]


def _sampled(found: dict, now: dt.datetime) -> dict[str, set[str]]:
    """For each step an estimate may read, the bookings among its last samples - as estimate.history takes them."""
    since, latest = now - WINDOW, now + estimate.SKEW
    done = sorted((item for item in found.values() if item.state == book.RELEASED and not item.cut
                   and (item.seconds is not None or item.ran) and _when(item.ended) is not None
                   and since <= _when(item.ended) <= latest), key=lambda item: _when(item.ended))
    steps: dict[str, list] = {}
    for item in done:
        if item.seconds is not None and item.verdict not in estimate.UNMEASURED:
            for step in item.ladder:
                steps.setdefault(step, []).append(item)
        for tier in _measured_tiers(item):
            steps.setdefault(_tier_key(item, tier), []).append(item)
    kept = {}
    for step, items in steps.items():
        items = items[-estimate.KEEP:]
        if len(items) < estimate.ENOUGH and now - _when(items[-1].ended) > STALE:
            continue   # a one-off: no estimate reads it, and a coarser step of its ladder estimates it if it comes again
        kept[step] = {item.id for item in items}
    return kept


def _recent(item, now: dt.datetime) -> bool:
    return any(moment is not None and now - moment <= book.SERVED_WINDOW
               for moment in (_when(item.since), _when(item.ended)))


def plan(records: list[dict], now: dt.datetime) -> tuple[list[dict], list[dict]]:
    """(kept, archived): what the compacted journal holds, in journal order, and every original record it does not
    hold as written. A rewritten booking's original lines are all archived."""
    found = book.fold(records)
    verbatim = {item.id for item in found.values() if item.state in (book.HELD, book.WAITING) or _recent(item, now)}
    sampled = _sampled(found, now)
    rewritten: dict[str, dict] = {}
    for item in found.values():
        if item.id in verbatim:
            continue
        ladder = [step for step in item.ladder if item.id in sampled.get(step, ())]
        ran = [tier for tier in _measured_tiers(item) if item.id in sampled.get(_tier_key(item, tier), ())]
        if not ladder and not ran:
            continue
        record = {"at": item.ended, "booking": item.id, "state": item.state}
        for key in ESTIMATE_FIELDS:
            value = {"ladder": ladder, "ran": ran}.get(key, getattr(item, key, None))
            if value not in (None, "", [], ()):
                record[key] = list(value) if isinstance(value, tuple) else value
        rewritten[item.id] = record
    last = {record.get("booking"): at for at, record in enumerate(records) if isinstance(record, dict)}
    kept, archived = [], []
    for at, record in enumerate(records):
        name = record.get("booking") if isinstance(record, dict) else None
        if name in verbatim:
            kept.append(record)
            continue
        archived.append(record)
        if name in rewritten and last[name] == at:   # the rewritten record stands where the booking ended
            kept.append(rewritten[name])
    return kept, archived
