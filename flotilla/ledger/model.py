"""Ledger events and the rows they fold into.

An event is one move: which row, which move, the state it leaves the row in, who made it (name, post, whether
the name came from the census or from `--as`, and who really called), the rules it was checked against, the row
fields it sets, its evidence, and the plugin version that wrote it. The current state of a row is the fold of its events; nothing else is stored, so history is never lost.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

EVENT_VERSION = 1
STATES = ("reserved", "claimed", "handed", "fixing", "accepted", "queued", "landed", "shipped", "walked",
          "closed", "released", "offledger", "inbatch")
TERMINAL = frozenset({"closed", "released", "offledger", "inbatch"})
ROW_FIELDS = ("branch", "owner", "tree", "base", "ref", "requires", "tip", "reader", "taken", "verdict",
              "waiting_on", "note", "why", "pr", "gate")


class LedgerVersionError(RuntimeError):
    """The log holds events from a newer flotilla than this one."""


@dataclass
class Row:
    id: str
    branch: str = ""
    owner: str = ""
    state: str = ""
    tree: str = ""
    base: str = ""
    ref: str = ""
    requires: list = field(default_factory=list)
    tip: str = ""
    reader: str = ""
    taken: bool = False
    verdict: str = ""
    waiting_on: str = ""
    note: str = ""
    why: str = ""
    pr: str = ""
    gate: str = ""
    updated_at: str = ""
    history: list = field(default_factory=list)

    @property
    def is_open(self) -> bool:
        return self.state not in TERMINAL


def now_iso(now: dt.datetime | None = None) -> str:
    return (now or dt.datetime.now(dt.timezone.utc)).isoformat(timespec="seconds")


def make_event(*, row: str, move: str, state: str, by: str, post: str, via: str, fields: dict,
               evidence: dict, at: str, plugin: str, caller: str = "", rules: str = "") -> dict:
    unknown = sorted(set(fields) - set(ROW_FIELDS))
    if unknown:
        raise ValueError(f"unknown row fields: {', '.join(unknown)}")
    if state not in STATES:
        raise ValueError(f"unknown state: {state}")
    return {"v": EVENT_VERSION, "at": at, "row": row, "move": move, "state": state, "by": by, "post": post,
            "via": via, "caller": caller, "rules": rules, "fields": dict(fields), "evidence": dict(evidence),
            "plugin": plugin}


def fold(records) -> dict[str, Row]:
    rows: dict[str, Row] = {}
    for event in records:
        if event.get("v", 0) > EVENT_VERSION:
            raise LedgerVersionError(f"a ledger event has version {event['v']}; this flotilla reads up to "
                                     f"{EVENT_VERSION}; update the plugin")
        row = rows.get(event["row"]) or Row(id=event["row"])
        for key, value in (event.get("fields") or {}).items():
            if key in ROW_FIELDS:
                setattr(row, key, value)
        row.state = event["state"]
        row.updated_at = event["at"]
        row.history.append({"at": event["at"], "move": event["move"], "by": event["by"],
                            "caller": event.get("caller", ""),
                            "state": event["state"], "evidence": event.get("evidence") or {}})
        rows[row.id] = row
    return rows


def next_row_id(rows: dict[str, Row]) -> str:
    return f"r{len(rows) + 1}"
