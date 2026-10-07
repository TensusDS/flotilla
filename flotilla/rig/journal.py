"""The rig's journal: sessions the person opened, machines rented in them, and every move of either (rig design,
sections 2, 3 and 5).

The lane's way: an append-only event log under a file lock, folded into records, every field validated on the way
in, because every session may write here and a hand-written line must fold as unknown, never crash the reaper. A move
the state machine does not allow is refused, so the record tells only a story that can happen: `gone` comes after a
verified drain, or straight from `failed` when no instance ever existed, and nothing comes back from `gone`.

Every move may carry an expectation - the state and lease the caller decided on - checked inside the transaction:
a run that renewed its lease after the reaper looked is never drained on the strength of the old lease (review of
2026-10-06).
"""

from __future__ import annotations

import datetime as dt
import math
import re
from dataclasses import dataclass, fields as dc_fields

from flotilla.core.text import visible

KEY = "rig"
REQUESTED, PROVISIONING, READY, BUSY, DRAINING = "requested", "provisioning", "ready", "busy", "draining"
GONE, FAILED, STUCK = "gone", "failed", "stuck"
OPEN, CLOSING, CLOSED = "open", "closing", "closed"
MACHINE_STATES = (REQUESTED, PROVISIONING, READY, BUSY, DRAINING, GONE, FAILED, STUCK)
SESSION_STATES = (OPEN, CLOSING, CLOSED)
LIVE = tuple(state for state in MACHINE_STATES if state != GONE)   # whatever may still cost money
RENEWABLE = (REQUESTED, PROVISIONING, READY, BUSY)
MOVES = {
    REQUESTED: (PROVISIONING, FAILED, DRAINING),
    PROVISIONING: (READY, FAILED, DRAINING),
    READY: (BUSY, DRAINING),
    BUSY: (READY, DRAINING),
    DRAINING: (DRAINING, GONE, STUCK),
    STUCK: (STUCK, GONE),
    FAILED: (DRAINING, GONE),
    GONE: (),
}
SESSION_MOVES = {OPEN: (CLOSING,), CLOSING: (CLOSING, CLOSED), CLOSED: ()}
LEASE = dt.timedelta(minutes=30)
IDLE = dt.timedelta(minutes=15)
SETTLE = dt.timedelta(minutes=2)
ATTEMPTS = 3
MAX_TEXT = 300
_NUMBERED = re.compile(r"^[sm]([0-9]{1,9})$")


class RigError(ValueError):
    """A move the rig's states do not allow, or a record that does not exist."""


@dataclass
class Session:
    id: str
    who: str = ""
    why: str = ""
    until: str = ""
    budget: float | None = None
    state: str = ""
    opened: str = ""
    ended: str = ""
    reason: str = ""


@dataclass
class Machine:
    id: str
    session: str = ""
    provider: str = ""
    instance: str = ""
    label: str = ""
    gpu: str = ""
    hourly: float | None = None
    state: str = ""
    created: str = ""
    lease_until: str = ""
    idle_since: str = ""
    attempts: int = 0
    reason: str = ""
    ended: str = ""
    run_pid: int | None = None
    run_mark: str = ""


TEXT = ("who", "why", "session", "provider", "instance", "label", "gpu", "reason", "run_mark")
TIMES = ("until", "opened", "ended", "created", "lease_until", "idle_since")
MONEY = {"budget": 1e4, "hourly": 1e3}


def _aware(text) -> bool:
    try:
        return isinstance(text, str) and dt.datetime.fromisoformat(text).tzinfo is not None
    except ValueError:
        return False


def _clean(key, value):
    if key in TEXT:
        return visible(value[:MAX_TEXT]) if isinstance(value, str) else ""
    if key in TIMES:
        return value if value == "" or _aware(value) else ""
    if key in MONEY:
        ok = isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= MONEY[key]
        return float(value) if ok else None
    if key == "attempts":
        return value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 1000 else 0
    if key == "run_pid":
        return value if isinstance(value, int) and not isinstance(value, bool) and 0 < value < 2 ** 31 else None
    return None


def _fold(records):
    sessions: dict[str, Session] = {}
    machines: dict[str, Machine] = {}
    for event in records:
        if not isinstance(event, dict) or not _aware(event.get("at")) or not isinstance(event.get("id"), str):
            continue
        kind, state = event.get("kind"), event.get("state")
        if kind == "session" and state in SESSION_STATES:
            found, item = sessions, sessions.get(event["id"]) or Session(id=event["id"])
        elif kind == "machine" and state in MACHINE_STATES:
            found, item = machines, machines.get(event["id"]) or Machine(id=event["id"])
        else:
            continue   # a line flotilla never writes: skipped, never fatal
        for field in dc_fields(item):
            if field.name not in ("id", "state") and field.name in event:
                setattr(item, field.name, _clean(field.name, event[field.name]))
        item.state = state
        found[item.id] = item
    return sessions, machines


def _next_id(prefix: str, taken) -> str:
    numbers = [int(found.group(1)) for found in map(_NUMBERED.match, taken) if found]
    return f"{prefix}{max(numbers, default=0) + 1}"


def _parse(text: str) -> dt.datetime:
    return dt.datetime.fromisoformat(text)


def _iso(moment: dt.datetime) -> str:
    return moment.isoformat(timespec="seconds")


def _holds(item, expect) -> bool:
    return all(getattr(item, key) == value for key, value in (expect or {}).items())


class Rig:
    def __init__(self, store, *, clock=None):
        self.store = store
        self.clock = clock

    def now(self) -> dt.datetime:
        return self.clock() if self.clock else dt.datetime.now(dt.timezone.utc)

    def sessions(self) -> dict[str, Session]:
        return _fold(self.store.read(KEY).records)[0]

    def machines(self) -> dict[str, Machine]:
        return _fold(self.store.read(KEY).records)[1]

    def _append(self, tx, kind: str, item_id: str, state: str, **fields) -> None:
        tx.append({"at": _iso(self.now()), "kind": kind, "id": item_id, "state": state, **fields})

    def open_session(self, who: str, why: str, *, hours: float, budget: float) -> Session:
        with self.store.transaction(KEY) as tx:
            sessions, _ = _fold(tx.read().records)
            session_id = _next_id("s", sessions)
            now = self.now()
            self._append(tx, "session", session_id, OPEN, who=who, why=why, budget=budget, opened=_iso(now),
                         until=_iso(now + dt.timedelta(hours=hours)))
            return _fold(tx.read().records)[0][session_id]

    def set_session(self, session_id: str, state: str, reason: str = "", **fields) -> Session:
        with self.store.transaction(KEY) as tx:
            found = _fold(tx.read().records)[0].get(session_id)
            if found is None:
                raise RigError(f"no session {session_id}")
            if state not in SESSION_MOVES[found.state]:
                raise RigError(f"session {session_id} cannot move from {found.state} to {state}")
            extra = {"ended": _iso(self.now())} if state == CLOSED else {}
            self._append(tx, "session", session_id, state, **({"reason": reason} if reason else {}), **extra,
                         **fields)
            return _fold(tx.read().records)[0][session_id]

    def add_machine(self, session_id: str, provider: str, label_of) -> Machine:
        """Written before the provider is asked, so a labelled instance always has a journal entry (section 5)."""
        with self.store.transaction(KEY) as tx:
            sessions, machines = _fold(tx.read().records)
            if session_id not in sessions:
                raise RigError(f"no session {session_id}")
            machine_id = _next_id("m", machines)
            self._append(tx, "machine", machine_id, REQUESTED, session=session_id, provider=provider,
                         label=label_of(machine_id), lease_until=_iso(self.now() + LEASE))
            return _fold(tx.read().records)[1][machine_id]

    def move(self, machine_id: str, state: str, reason: str = "", *, expect=None, **fields) -> Machine | None:
        with self.store.transaction(KEY) as tx:
            found = _fold(tx.read().records)[1].get(machine_id)
            if found is None:
                raise RigError(f"no machine {machine_id}")
            if not _holds(found, expect):
                return None
            if state not in MOVES[found.state]:
                raise RigError(f"machine {machine_id} cannot move from {found.state} to {state}")
            if found.state == FAILED and state == GONE and found.instance:
                raise RigError(f"machine {machine_id} has instance {found.instance}: it is gone only after a drain")
            now = _iso(self.now())
            extra = {}
            if state == READY:
                extra.update(idle_since=now, run_pid=None, run_mark="")
            elif state == BUSY:
                extra["idle_since"] = ""
            if state == GONE:
                extra["ended"] = now
            self._append(tx, "machine", machine_id, state, **({"reason": reason} if reason else {}), **extra,
                         **fields)
            return _fold(tx.read().records)[1][machine_id]

    def note(self, machine_id: str, *, expect=None, **fields) -> Machine | None:
        with self.store.transaction(KEY) as tx:
            found = _fold(tx.read().records)[1].get(machine_id)
            if found is None:
                raise RigError(f"no machine {machine_id}")
            if not _holds(found, expect):
                return None
            self._append(tx, "machine", machine_id, found.state, **fields)
            return _fold(tx.read().records)[1][machine_id]

    def renew(self, machine_ids=None) -> list[Machine]:
        """A lease renewed by the work: `rig run` calls this for its own machine (stage 2)."""
        renewed = []
        with self.store.transaction(KEY) as tx:
            until = _iso(self.now() + LEASE)
            for item in _fold(tx.read().records)[1].values():
                if item.state in RENEWABLE and (machine_ids is None or item.id in machine_ids):
                    self._append(tx, "machine", item.id, item.state, lease_until=until)
                    renewed.append(item.id)
            machines = _fold(tx.read().records)[1]
            return [machines[machine_id] for machine_id in renewed]

    def spent(self, session_id: str, now: dt.datetime | None = None) -> float:
        """Money a session has cost by this side's count: each machine's price times the minutes begun since it was
        created, plus the minutes the provider keeps billing after a destroy. A floor, not the bill (section 5)."""
        moment = now or self.now()
        total = 0.0
        for item in self.machines().values():
            if item.session != session_id or item.hourly is None or not item.created:
                continue
            end = _parse(item.ended) if item.state == GONE and item.ended else moment
            minutes = math.ceil(max(0.0, (end - _parse(item.created)).total_seconds()) / 60)
            if item.state == GONE:
                minutes += int(SETTLE.total_seconds() // 60)
            total += item.hourly * minutes / 60
        return total

    def rate(self, session_id: str) -> float:
        return sum(item.hourly for item in self.machines().values()
                   if item.session == session_id and item.state in LIVE and item.created and item.hourly is not None)
