"""What the orchestrator is told about rented machines, and what `flotilla fleet` lists (rig design, section 3).

Items join the orchestrator's `watch` and session-start screen. A request a seat made is shown to its own project's
orchestrator for a day, and the line it carries names the request's id and nothing a session wrote: the person pastes
it, and a reason a session typed must never become shell the person runs. Trouble with money - a STUCK machine, a
silent reaper, a failed pass - is shown to every orchestrator and said again every half hour until it clears.
"""

from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

from flotilla.core.storage import LocalLogStore, StorageCorrupt
from flotilla.rig import health
from flotilla.rig import journal as j
from flotilla.rig import settings as rs
from flotilla.watch.whose import Item

KIND = "rig"
SLOW = dt.timedelta(minutes=10)
ENDING = dt.timedelta(minutes=15)
WARN_AT = 0.8
REQUEST_LIFE = dt.timedelta(hours=24)
LOUD_EVERY = dt.timedelta(minutes=30)
#: A requester's name as the census names sessions; anything else - `--as` takes any text outside the census - is
#: shown as "a session", so no text a session chose reaches the orchestrator's model as if it were the screen's.
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9 _.-]{0,59}")


def _who(name: str) -> str:
    return name if _NAME.fullmatch(name or "") else "a session"


def _when(text: str):
    try:
        return dt.datetime.fromisoformat(text) if text else None
    except ValueError:
        return None


def _hhmm(moment: dt.datetime) -> str:
    return moment.astimezone().strftime("%H:%M")


def _item(text: str, since: str, who: str) -> Item:
    return Item(kind=KIND, branch="", text=f"rig: {text}", since=since, who=who)


def _loud(now: dt.datetime) -> str:
    return str(int(now.timestamp() // LOUD_EVERY.total_seconds()))


def items(state: Path, now: dt.datetime, project: str = "") -> list[Item]:
    on = rs.settings(state).on
    rig = j.Rig(LocalLogStore(state / "rig"), clock=lambda: now)
    try:
        machines, sessions, requests = rig.machines(), rig.sessions(), rig.requests()
    except StorageCorrupt as err:
        return [_item(f"the rig journal is damaged ({err}); `flotilla rig reap` destroys this machine's labelled "
                      "instances", "", f"rig:journal:{_loud(now)}")]
    live = [m for m in machines.values() if m.state in j.LIVE]
    found = []
    for machine in live:
        if machine.state == j.STUCK:
            found.append(_item(f"machine {machine.id} STUCK ({machine.reason or 'still listed'}) - instance "
                               f"{machine.instance} may still cost money; tell the person now", machine.created,
                               f"rig:stuck:{machine.id}:{_loud(now)}"))
        created = _when(machine.created)
        if machine.state == j.PROVISIONING and created and now - created > SLOW:
            minutes = int((now - created).total_seconds() // 60)
            found.append(_item(f"machine {machine.id} provisioning for {minutes} min (instance {machine.instance})",
                               machine.created, f"rig:slow:{machine.id}"))
    if live:
        last = health.last_reap(state)
        ok, note = health.last_outcome(state)
        if last is None or now - last > health.SILENT_AFTER:
            found.append(_item("the reaper is silent while a machine lives - is cron running? `flotilla rig reap` now",
                               "", f"rig:silent:{_loud(now)}"))
        elif not ok:
            found.append(_item(f"the last reaper pass failed: {note}", "", f"rig:failed:{_loud(now)}"))
    open_ = [s for s in sessions.values() if s.state == j.OPEN]
    for session in open_:
        spent = rig.spent(session.id, now)
        if session.budget and spent >= WARN_AT * session.budget:
            found.append(_item(f"session {session.id} has spent at least {spent:.2f} $ of its budget "
                               f"{session.budget:.2f} $", session.opened, f"rig:budget:{session.id}"))
        until = _when(session.until)
        busy = [m for m in live if m.session == session.id and m.state == j.BUSY]
        if until and busy and now < until <= now + ENDING:
            found.append(_item(f"session {session.id} ends at {_hhmm(until)} with machine(s) still busy "
                               f"({', '.join(m.id for m in busy)})", session.opened, f"rig:ending:{session.id}"))
    for request in requests.values():
        since = _when(request.since)
        if request.state != j.ASKED or request.project != project or since is None or now - since > REQUEST_LIFE:
            continue
        if request.kind == "image":
            if not rs.IMAGE.fullmatch(request.image or ""):   # a line written past `rig up` names nothing to paste
                continue
            line = f"! flotilla rig allow-image {request.image} --for {request.id}"
            text = (f"{_who(request.who)} needs image {request.image}, which the person has not allowed - ask the "
                    f"person to paste: {line}")
        elif open_:
            continue
        else:
            line = f"! flotilla rig open --hours 2 --budget 1 --for {request.id}"
            first = "" if on else "rig is off: the person runs `! flotilla rig enable --provider vast` first, then "
            text = (f"{_who(request.who)} asks for a machine; no session is open - {first}ask the person to paste: "
                    f"{line}")
        found.append(_item(text, request.since, f"rig:request:{request.id}"))
    return found


def lines(state: Path, now: dt.datetime) -> list[str]:
    rig = j.Rig(LocalLogStore(state / "rig"), clock=lambda: now)
    try:
        machines, sessions = rig.machines(), rig.sessions()
    except StorageCorrupt as err:
        return [f"rig: the journal is damaged ({err})"]
    live = [m for m in machines.values() if m.state in j.LIVE]
    shown = [s for s in sessions.values() if s.state in (j.OPEN, j.CLOSING)]
    if not rs.settings(state).on and not live and not shown:
        return []
    found = []
    for session in shown:
        budget = f"{session.budget:.2f}" if session.budget is not None else "?"
        until = _when(session.until)
        found.append(f"rig: session {session.id} {session.state} ({session.why}) until "
                     f"{_hhmm(until) if until else '?'}, spent at least {rig.spent(session.id, now):.2f} of {budget} $")
    if not shown:
        found.append("rig: no session open")
    for machine in live:
        price = f"{machine.hourly:.3f} $/h" if machine.hourly is not None else "price unknown"
        found.append(f"  machine {machine.id} {machine.state}: {machine.gpu or 'gpu ?'}, {price}"
                     f"{', ' + machine.address if machine.address else ''}"
                     f"{' (' + machine.reason + ')' if machine.reason else ''}")
    return found
