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
import secrets
from dataclasses import dataclass, fields as dc_fields

from flotilla.core.text import visible

KEY = "rig"
REQUESTED, PROVISIONING, READY, BUSY, DRAINING = "requested", "provisioning", "ready", "busy", "draining"
GONE, FAILED, STUCK = "gone", "failed", "stuck"
OPEN, CLOSING, CLOSED = "open", "closing", "closed"
ASKED, ANSWERED = "asked", "answered"
MACHINE_STATES = (REQUESTED, PROVISIONING, READY, BUSY, DRAINING, GONE, FAILED, STUCK)
SESSION_STATES = (OPEN, CLOSING, CLOSED)
REQUEST_STATES = (ASKED, ANSWERED)
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
_NUMBERED = re.compile(r"^[smrj]([0-9]{1,9})$")
PREFIX = {"session": "s", "machine": "m", "request": "r", "run": "j"}
WAITING, RUNNING, DONE = "waiting", "running", "done"
RUN_STATES = (WAITING, RUNNING, DONE)
#: Two runs share a machine whatever its readings say; past them, room is the readings' (rig design, section 7).
FLOOR = 2
#: Past the floor, one more run only once every run on the machine has run its command this long: a run still in
#: its tree or setup has not taken its memory yet, and the readings would admit a crowd (third review of 2b).
RUN_SETTLE = dt.timedelta(seconds=60)
DEFAULT_CAP = 4
#: A machine is not given back within this long of coming up unused, or of a peer's run ending on it (0.12.0).
GIVE_BACK_GRACE = dt.timedelta(minutes=5)
_PROGRAM = re.compile(r"[A-Za-z0-9._+-]{1,24}")
_TAG = re.compile(r"j[0-9]{1,9}-[0-9a-f]{8}")
_SLUG = re.compile(r"[A-Za-z0-9._-]{0,40}-[0-9a-f]{8}")   # transfer.project_slug's shape, and only that



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
    credit_open: float | None = None   # the account's credit at the first reading in this session (0.13.0)
    credit_now: float | None = None
    credited: str = ""                 # when credit_now was read


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
    address: str = ""
    requested: str = ""
    suspect: str = ""
    cpus: int | None = None           # the machine's shape, from its first readings
    ram_mb: int | None = None
    gpu_total_mb: int | None = None
    keyed: str = ""      # when the rig's ssh key was put on the instance; not ready without it


@dataclass
class Request:
    id: str
    kind: str = ""
    who: str = ""
    why: str = ""
    project: str = ""
    image: str = ""
    state: str = ""
    since: str = ""
    reason: str = ""


@dataclass
class Run:
    """One `rig run`: a line per session, a place on a machine, what it measured (rig design, section 7)."""
    id: str
    session: str = ""
    machine: str = ""
    tag: str = ""                     # FLOTILLA_RUN on the machine: the id plus a random part, so j1 never matches j10
    who: str = ""
    project: str = ""
    revision: str = ""
    program: str = ""                 # checked: the only part of a command an orchestrator's model sees
    ladder: tuple = ()
    state: str = ""
    verdict: str = ""
    exit: int | None = None
    seconds: float | None = None
    cost: float | None = None
    peak_mb: int | None = None
    cores: float | None = None
    gpu_mb: int | None = None
    gpu_shared_mb: int | None = None  # GPU memory measured while another run shared the machine: raises, never lowers
    pid: int | None = None
    mark: str = ""
    since: str = ""
    command_at: str = ""
    swept: str = ""                   # when a later run ended on the machine what this gone run had left there
    slug: str = ""                    # its project's directory on the machine, so `rig stop` finds its status files
    ended: str = ""                   # its first `done` event, from the fold: a later `swept` never moves it
    waits: str = ""                   # why start_run refused it inside the lock, once per change; never displayed
    reason: str = ""


TEXT = ("who", "why", "waits",
        "session", "provider", "instance", "label", "gpu", "reason", "run_mark", "address", "project",
        "image", "suspect", "revision", "verdict", "machine", "mark")
TIMES = ("until", "opened", "ended", "created", "lease_until", "idle_since", "requested", "keyed", "command_at",
         "swept", "credited")
CREDIT = ("credit_open", "credit_now")   # an account's credit may be below zero
MONEY = {"budget": 1e4, "hourly": 1e3, "cost": 1e4}
WHOLE = {"peak_mb": 10 ** 7, "gpu_mb": 10 ** 7, "gpu_shared_mb": 10 ** 7,
         "cpus": 10 ** 4, "ram_mb": 10 ** 8, "gpu_total_mb": 10 ** 7}


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
    if key in CREDIT:
        ok = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and \
            abs(value) <= 1e6
        return float(value) if ok else None
    if key in MONEY:
        ok = isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= MONEY[key]
        return float(value) if ok else None
    if key == "attempts":
        return value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 1000 else 0
    if key == "slug":
        return value if isinstance(value, str) and _SLUG.fullmatch(value) else ""
    if key in ("run_pid", "pid"):
        return value if isinstance(value, int) and not isinstance(value, bool) and 0 < value < 2 ** 31 else None
    if key in WHOLE:
        return value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= WHOLE[key] else None
    if key in ("seconds", "cores"):
        ok = isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= 1e6
        return float(value) if ok else None
    if key == "exit":
        return value if isinstance(value, int) and not isinstance(value, bool) and -255 <= value <= 255 else None
    if key == "program":
        return value if isinstance(value, str) and _PROGRAM.fullmatch(value) else "a program"
    if key == "tag":
        return value if isinstance(value, str) and _TAG.fullmatch(value) else ""
    if key == "ladder":
        items = value if isinstance(value, list) else []
        return tuple(visible(item[:200]) for item in items[:6] if isinstance(item, str))
    return None


def _fold(records):
    sessions: dict[str, Session] = {}
    machines: dict[str, Machine] = {}
    requests: dict[str, Request] = {}
    for event in records:
        if not isinstance(event, dict) or not _aware(event.get("at")) or not isinstance(event.get("id"), str):
            continue
        kind, state = event.get("kind"), event.get("state")
        if not _NUMBERED.fullmatch(event["id"]) or event["id"][0] != PREFIX.get(kind):
            continue   # an id flotilla never issues: text a shell wrote must not travel as an id (final review, C2)
        if kind == "session" and state in SESSION_STATES:
            found, item = sessions, sessions.get(event["id"]) or Session(id=event["id"])
        elif kind == "machine" and state in MACHINE_STATES:
            found, item = machines, machines.get(event["id"]) or Machine(id=event["id"])
        elif kind == "request" and state in REQUEST_STATES:
            found, item = requests, requests.get(event["id"]) or Request(id=event["id"])
        else:
            continue   # a line flotilla never writes: skipped, never fatal
        skip = ("id", "state", "kind", "since") if isinstance(item, Request) else ("id", "state")
        for field in dc_fields(item):
            if field.name not in skip and field.name in event:
                setattr(item, field.name, _clean(field.name, event[field.name]))
        item.state = state
        if isinstance(item, Request):
            if "ask" in event:   # the record's own `kind` key says "request"; the request's kind travels as `ask`
                item.kind = event["ask"] if event["ask"] in ("machine", "image") else ""
            if state == ASKED:
                item.since = event["at"]
        found[item.id] = item
    return sessions, machines, requests


def _fold_runs(records) -> dict[str, Run]:
    runs: dict[str, Run] = {}
    for event in records:
        if not isinstance(event, dict) or event.get("kind") != "run" or not _aware(event.get("at")):
            continue
        run_id, state = event.get("id"), event.get("state")
        if not isinstance(run_id, str) or not _NUMBERED.fullmatch(run_id) or run_id[0] != "j":
            continue
        if state not in RUN_STATES:
            continue
        item = runs.get(run_id) or Run(id=run_id)
        for field in dc_fields(item):
            if field.name not in ("id", "state", "since", "ended") and field.name in event:
                setattr(item, field.name, _clean(field.name, event[field.name]))
        if item.tag and not item.tag.startswith(run_id + "-"):
            item.tag = ""
        item.state = state
        if state == WAITING and not item.since:   # a later `waits` line never resets how long it has waited
            item.since = event["at"]
        if state == DONE and not item.ended:
            item.ended = event["at"]
        runs[run_id] = item
    return runs


def _minutes(span: dt.timedelta) -> str:
    return f"{max(0, int(span.total_seconds() // 60))} min"


def _number(item_id: str) -> int:
    return int(item_id[1:])


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
            sessions = _fold(tx.read().records)[0]
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

    def note_credit(self, session_id: str, credit) -> Session | None:
        """The account's credit read now; the session's first reading is also its credit at open. A closed session
        keeps what it had."""
        with self.store.transaction(KEY) as tx:
            found = _fold(tx.read().records)[0].get(session_id)
            if found is None or found.state == CLOSED:
                return None
            fields = {"credit_now": credit, "credited": _iso(self.now())}
            if found.credit_open is None:
                fields["credit_open"] = credit
            self._append(tx, "session", session_id, found.state, **fields)
            return _fold(tx.read().records)[0][session_id]

    def add_machine(self, session_id: str, provider: str, label_of, *, limit: int | None = None) -> Machine:
        """Written before the provider is asked, so a labelled instance always has a journal entry (section 5); the
        session and the ceiling are checked here, inside the transaction, so a close or a second seat cannot slip
        between a check and the machine."""
        with self.store.transaction(KEY) as tx:
            sessions, machines, _ = _fold(tx.read().records)
            session = sessions.get(session_id)
            if session is None or session.state != OPEN:
                raise RigError(f"session {session_id} is not open")
            if session.until and self.now() >= _parse(session.until):
                raise RigError(f"session {session_id} has ended")
            if limit is not None and sum(1 for m in machines.values() if m.state in LIVE) >= limit:
                raise RigError(f"the machine ceiling ({limit}) is reached: another machine lives in this rig")
            machine_id = _next_id("m", machines)
            now = self.now()
            self._append(tx, "machine", machine_id, REQUESTED, session=session_id, provider=provider,
                         label=label_of(machine_id), lease_until=_iso(now + LEASE), requested=_iso(now))
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

    def runs(self) -> dict[str, Run]:
        return _fold_runs(self.store.read(KEY).records)

    def queue_run(self, session_id: str, *, who: str, project: str, revision: str, program: str, ladder,
                  pid: int, mark: str) -> Run:
        with self.store.transaction(KEY) as tx:
            run_id = _next_id("j", _fold_runs(tx.read().records))
            self._append(tx, "run", run_id, WAITING, session=session_id, tag=f"{run_id}-{secrets.token_hex(4)}",
                         who=who, project=project, revision=revision, program=program, ladder=list(ladder), pid=pid,
                         mark=mark)
            return _fold_runs(tx.read().records)[run_id]

    @staticmethod
    def _head(runs: dict, session_id: str, alive) -> Run | None:
        waiting = [item for item in runs.values() if item.state == WAITING and item.session == session_id
                   and alive(item.pid, item.mark)]
        return min(waiting, key=lambda item: _number(item.id), default=None)

    def head(self, session_id: str, alive) -> Run | None:
        return self._head(self.runs(), session_id, alive)

    def on(self, machine_id: str) -> list[Run]:
        return [item for item in self.runs().values() if item.state == RUNNING and item.machine == machine_id]

    def _verdict(self, runs, machine, run, alive) -> str:
        """"" when `run` is the one to start on `machine` now (before the second barrier), else why it waits: the
        packing's choice when the machine's size is known, else the session's line by arrival."""
        from flotilla.rig import packing
        live = [item for item in runs.values() if item.state == WAITING and item.session == run.session
                and alive(item.pid, item.mark)]
        if machine.session != run.session or run.id not in {item.id for item in live}:
            return "not waiting in this machine's session"
        if not packing.shape_known(machine):
            head = min(live, key=lambda item: _number(item.id))
            return "" if head.id == run.id else f"{head.id} came first"
        there = [item for item in runs.values() if item.state == RUNNING and item.machine == machine.id]
        hist = packing.history(runs, now=self.now())
        needs = {item.id: packing.estimate(item.ladder, hist) for item in live + there}
        picked = packing.choose(live, there, machine, needs, self.now())
        if picked.run_id == run.id:
            return ""
        return picked.why.get(run.id) or f"{picked.run_id} goes first"

    def would_start(self, run_id: str, machine_id: str, *, alive) -> str:
        """The same question as `start_run`'s first barrier, read without the lock and written nowhere: a waiting
        run asks it before it reads the machine, so only the run that would go makes an ssh call."""
        records = self.store.read(KEY).records
        runs, machine = _fold_runs(records), _fold(records)[1].get(machine_id)
        run = runs.get(run_id)
        if run is None or machine is None or run.state != WAITING or machine.state not in (READY, BUSY):
            return "no such waiting run or ready machine"
        return self._verdict(runs, machine, run, alive)

    def start_run(self, run_id: str, machine_id: str, *, alive, roomy: bool) -> Run | None:
        """One transaction: the run is the one its session's packing picks for this machine (or, the machine's size
        unknown, its session's head). Up to FLOOR runs share a machine without readings; past it only when the
        caller read room just now (`roomy`), every run on it is past RUN_SETTLE in its command, and the machine's
        cap (its CPUs, never under FLOOR) is not reached."""
        with self.store.transaction(KEY) as tx:
            records = tx.read().records
            runs, machine = _fold_runs(records), _fold(records)[1].get(machine_id)
            run = runs.get(run_id)
            if run is None or machine is None or run.state != WAITING or machine.state not in (READY, BUSY):
                return None
            reason = self._verdict(runs, machine, run, alive)
            if reason:
                if reason != run.waits:
                    self._append(tx, "run", run_id, WAITING, waits=reason)
                return None
            there = [item for item in runs.values() if item.state == RUNNING and item.machine == machine_id]
            if len(there) >= FLOOR:
                cap = max(FLOOR, machine.cpus) if machine.cpus else DEFAULT_CAP
                now = self.now()
                settled = all(item.command_at and now - _parse(item.command_at) >= RUN_SETTLE for item in there)
                if not roomy or not settled or len(there) >= cap:
                    return None
            self._append(tx, "run", run_id, RUNNING, machine=machine_id)
            if machine.state == READY:
                self._append(tx, "machine", machine_id, BUSY, idle_since="")
            return _fold_runs(tx.read().records)[run_id]

    def give_back(self, machine_id: str, who: str, *, alive) -> str:
        """A seat gives an idle machine back at once (0.12.0): one transaction, so no run starts on it meanwhile and
        none waits for it. "" when it drains now, else why not."""
        with self.store.transaction(KEY) as tx:
            records = tx.read().records
            machine, runs = _fold(records)[1].get(machine_id), _fold_runs(records)
            if machine is None:
                return f"no machine {machine_id}"
            on = sorted(item.id for item in runs.values() if item.state == RUNNING and item.machine == machine_id)
            if on:
                return f"{', '.join(on)} runs on {machine_id}"
            if machine.state != READY:
                return f"machine {machine_id} is {machine.state}, not idle"
            waiting = sorted((item.id for item in runs.values() if item.state == WAITING
                              and item.session == machine.session and alive(item.pid, item.mark)),
                             key=lambda x: int(x[1:]))
            if waiting:
                return f"{', '.join(waiting)} waits for a machine in this session"
            now = self.now()
            ran = [item for item in runs.values() if item.machine == machine_id and item.state == DONE and item.ended]
            if not ran and machine.idle_since and now - _parse(machine.idle_since) < GIVE_BACK_GRACE:
                return (f"machine {machine_id} came up {_minutes(now - _parse(machine.idle_since))} ago and nobody has "
                        "run on it yet - a seat may be about to")
            last = max(ran, key=lambda item: item.ended, default=None)
            if last is not None and last.who != who and now - _parse(last.ended) < GIVE_BACK_GRACE:
                return (f"{last.id} of {last.who} ended on {machine_id} {_minutes(now - _parse(last.ended))} ago - "
                        "that seat may run again")
            self._append(tx, "machine", machine_id, DRAINING, reason=f"given back by {who}")
            return ""

    def place_run(self, run_id: str, slug: str) -> Run:
        with self.store.transaction(KEY) as tx:
            run = _fold_runs(tx.read().records).get(run_id)
            if run is None:
                raise RigError(f"no run {run_id}")
            self._append(tx, "run", run_id, run.state, slug=slug)
            return _fold_runs(tx.read().records)[run_id]

    def command_started(self, run_id: str) -> Run:
        with self.store.transaction(KEY) as tx:
            run = _fold_runs(tx.read().records).get(run_id)
            if run is None:
                raise RigError(f"no run {run_id}")
            self._append(tx, "run", run_id, run.state, command_at=_iso(self.now()))
            return _fold_runs(tx.read().records)[run_id]

    def mark_swept(self, run_id: str) -> Run:
        """A later run ended on the machine what this finished run had left there: it is not swept again."""
        with self.store.transaction(KEY) as tx:
            run = _fold_runs(tx.read().records).get(run_id)
            if run is None or run.state != DONE:
                raise RigError(f"no finished run {run_id}")
            self._append(tx, "run", run_id, DONE, swept=_iso(self.now()))
            return _fold_runs(tx.read().records)[run_id]

    def finish_run(self, run_id: str, verdict: str, *, exit=None, seconds=None, cost=None, reason: str = "",
                   **measured) -> Run:
        """The run is done; its machine is ready again when no other run is on it."""
        with self.store.transaction(KEY) as tx:
            records = tx.read().records
            run = _fold_runs(records).get(run_id)
            if run is None:
                raise RigError(f"no run {run_id}")
            if run.state == DONE:    # the first verdict stands: a reaper's late "gone" never overwrites it
                return run
            fields = {key: value for key, value in dict(exit=exit, seconds=seconds, cost=cost, **measured).items()
                      if value is not None}
            self._append(tx, "run", run_id, DONE, verdict=verdict, **({"reason": reason} if reason else {}), **fields)
            if run.machine:
                others = [item for item in _fold_runs(tx.read().records).values()
                          if item.state == RUNNING and item.machine == run.machine]
                machine = _fold(records)[1].get(run.machine)
                if not others and machine is not None and machine.state == BUSY:
                    self._append(tx, "machine", run.machine, READY, idle_since=_iso(self.now()), run_pid=None,
                                 run_mark="")
            return _fold_runs(tx.read().records)[run_id]

    def requests(self) -> dict[str, Request]:
        return _fold(self.store.read(KEY).records)[2]

    def ask(self, who: str, why: str, *, kind: str = "machine", project: str = "", image: str = "") -> Request:
        """A session wanted what only the person gives: the orchestrator relays it by its id, never by its text."""
        with self.store.transaction(KEY) as tx:
            request_id = _next_id("r", _fold(tx.read().records)[2])
            self._append(tx, "request", request_id, ASKED, ask=kind, who=who, why=why, project=project, image=image)
            return _fold(tx.read().records)[2][request_id]

    def answer_requests(self, reason: str, *, kind: str | None = None, ids=None) -> list[Request]:
        with self.store.transaction(KEY) as tx:
            waiting = [item for item in _fold(tx.read().records)[2].values() if item.state == ASKED
                       and (kind is None or item.kind == kind) and (ids is None or item.id in ids)]
            for item in waiting:
                self._append(tx, "request", item.id, ANSWERED, reason=reason)
            found = _fold(tx.read().records)[2]
            return [found[item.id] for item in waiting]

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
