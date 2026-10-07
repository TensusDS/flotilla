"""The reaper: what keeps a rented machine from outliving the side that took it (rig design, section 5).

One pass, in this order:
1. the listing of every service in use - each machine's, and each service whose key is in place - is taken first;
   the journal is read after it, so a machine written while the listing was taken is still known;
2. a session past its end, at 90 % of its budget projected one pass ahead, or open while rig is off, closes;
3. a `busy` machine whose run's process is gone returns to `ready`;
4. a machine drains on an expired lease, 15 idle minutes, or a session no longer open (a running job is let end when
   rig was only turned off). Every such move is a compare-and-set on the state and lease decided on;
5. a draining or stuck machine absent from the listing is `gone`. One listed is destroyed only when its label is
   this machine's key and this machine id, and the listing shows exactly that label; anything else is never
   destroyed (`label mismatch`). Each pass that still lists it counts; past three destroys it is `stuck`. With no
   listing, nothing is destroyed and the pass counts;
6. an instance labelled with this machine's key is an orphan unless its machine is live and holds that very instance
   (or none yet: it is being created). An instance without this machine's label is never touched;
7. prices are refreshed from the listing; a closing session with nothing live closes.

Without a usable machine key the pass still decides and records, but destroys nothing: what is ours cannot be told
from what is not. The caller holds the reaper's lock; when the journal is damaged it calls `emergency` instead.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from flotilla.rig import journal as j
from flotilla.rig.provider import ProviderError
from flotilla.rig.settings import key_of_label

OFF_REASON = "rig turned off"
PASS = dt.timedelta(minutes=5)
DRAIN_AT = 0.9
CENT = 1e-6   # money compares within a millionth of a dollar: 0.9 * 0.10 is 0.09000000000000001 in floats


@dataclass
class Outcome:
    lines: list[str] = field(default_factory=list)
    stuck: list[str] = field(default_factory=list)
    failed: bool = False


def _when(text: str) -> dt.datetime | None:
    return dt.datetime.fromisoformat(text) if text else None


def needs_reaper(rig: j.Rig) -> bool:
    return any(item.state in j.LIVE for item in rig.machines().values()) or \
        any(item.state in (j.OPEN, j.CLOSING) for item in rig.sessions().values())


def _listings(rig: j.Rig, providers, services, out: Outcome) -> dict:
    """service name -> {instance id: Listed}, or None when it could not be asked."""
    machines = rig.machines()
    names = {item.provider for item in machines.values() if item.provider} | set(services)
    found = {}
    for name in sorted(names):
        try:
            found[name] = {entry.instance: entry for entry in providers(name).instances()}
        except ProviderError as err:
            found[name] = None
            if any(item.provider == name and item.state in j.LIVE for item in machines.values()):
                out.failed = True
                out.lines.append(f"{name} could not be asked: {err}")
    return found


def _close_sessions(rig: j.Rig, now: dt.datetime, on: bool, out: Outcome) -> None:
    for session in rig.sessions().values():
        if session.state != j.OPEN:
            continue
        until = _when(session.until)
        projected = rig.spent(session.id, now) + rig.rate(session.id) * PASS.total_seconds() / 3600
        reason = ""
        if not on:
            reason = OFF_REASON
        elif until is not None and now >= until:
            reason = "session ended"
        elif session.budget is not None and projected >= DRAIN_AT * session.budget - CENT:
            reason = "budget"
        if reason:
            rig.set_session(session.id, j.CLOSING, reason=reason)
            out.lines.append(f"session {session.id} closing: {reason}")


def _dead_runs(rig: j.Rig, alive, out: Outcome) -> None:
    for item in rig.machines().values():
        if item.state == j.BUSY and not alive(item.run_pid, item.run_mark):
            if rig.move(item.id, j.READY, reason="its run is gone",
                        expect={"state": j.BUSY, "run_pid": item.run_pid}):
                out.lines.append(f"machine {item.id}: its run is gone, back to ready")


def _drain(rig: j.Rig, now: dt.datetime, out: Outcome) -> None:
    sessions = rig.sessions()
    for item in rig.machines().values():
        if item.state == j.FAILED and not item.instance:
            if rig.move(item.id, j.GONE, expect={"state": j.FAILED, "instance": ""}):
                out.lines.append(f"machine {item.id} gone: it never had an instance")
            continue
        if item.state not in (j.REQUESTED, j.PROVISIONING, j.READY, j.BUSY, j.FAILED):
            continue
        session = sessions.get(item.session)
        lease, idle = _when(item.lease_until), _when(item.idle_since)
        reason = ""
        if session is None or session.state != j.OPEN:
            why = session.reason if session is not None and session.reason else "session ended"
            if not (why == OFF_REASON and item.state == j.BUSY):   # off lets a running job end
                reason = why
        if not reason and (lease is None or now >= lease):
            reason = "lease expired"
        if not reason and item.state == j.READY and idle is not None and now - idle >= j.IDLE:
            reason = "idle 15 min"
        if not reason and item.state == j.FAILED:
            reason = item.reason or "failed"
        if reason and rig.move(item.id, j.DRAINING, reason=reason,
                               expect={"state": item.state, "lease_until": item.lease_until}):
            out.lines.append(f"machine {item.id} draining: {reason}")


def _ours(item: j.Machine, machine_key: str | None) -> bool:
    return machine_key is not None and key_of_label(item.label) == (machine_key, item.id)


def _destroy(rig: j.Rig, providers, listings: dict, machine_key: str | None, out: Outcome) -> None:
    for item in rig.machines().values():
        if item.state not in (j.DRAINING, j.STUCK):
            continue
        listed = listings.get(item.provider)
        if listed is not None and item.instance not in listed:
            rig.move(item.id, j.GONE, expect={"state": item.state})
            out.lines.append(f"machine {item.id} gone (instance {item.instance or 'none'} no longer listed)")
            continue
        attempts = item.attempts + 1
        if listed is not None and (not _ours(item, machine_key) or listed[item.instance].label != item.label):
            if machine_key is None:
                out.failed = True
                out.lines.append(f"machine {item.id}: no machine key, so instance {item.instance} is not destroyed")
                rig.move(item.id, item.state, attempts=attempts, expect={"state": item.state})
                continue
            rig.move(item.id, j.STUCK, reason="label mismatch", attempts=attempts, expect={"state": item.state})
            out.stuck.append(item.id)
            out.lines.append(f"machine {item.id} STUCK: instance {item.instance} does not carry this machine's label "
                             "for it - never destroyed; the journal is wrong or was written by hand")
            continue
        if listed is not None:
            try:
                providers(item.provider).destroy(item.instance)
                out.lines.append(f"machine {item.id}: destroy of {item.instance} sent ({attempts})")
            except ProviderError as err:
                out.failed = True
                out.lines.append(f"machine {item.id}: destroy of {item.instance} failed: {err}")
        state = j.STUCK if attempts > j.ATTEMPTS or item.state == j.STUCK else j.DRAINING
        rig.move(item.id, state, attempts=attempts, expect={"state": item.state})
        if state == j.STUCK:
            out.stuck.append(item.id)
            out.lines.append(f"machine {item.id} STUCK: still listed after {attempts - 1} destroys; instance "
                             f"{item.instance} may still cost money - check the service's console")


def _orphans(rig: j.Rig, providers, listings: dict, machine_key: str, out: Outcome) -> None:
    machines = rig.machines()
    for name, listed in listings.items():
        for entry in (listed or {}).values():
            parsed = key_of_label(entry.label)
            if not parsed or parsed[0] != machine_key:
                continue
            machine = machines.get(parsed[1])
            if machine is not None and machine.state != j.GONE and machine.provider == name \
                    and machine.instance in ("", entry.instance):
                continue
            try:
                providers(name).destroy(entry.instance)
                out.lines.append(f"orphan instance {entry.instance} ({entry.label}) destroyed")
            except ProviderError as err:
                out.failed = True
                out.lines.append(f"orphan instance {entry.instance} ({entry.label}): destroy failed: {err}")


def _prices(rig: j.Rig, listings: dict) -> None:
    for item in rig.machines().values():
        entry = (listings.get(item.provider) or {}).get(item.instance)
        if item.state in j.LIVE and entry is not None and entry.hourly is not None and entry.hourly != item.hourly:
            rig.note(item.id, hourly=entry.hourly)


def _finish_sessions(rig: j.Rig, now: dt.datetime, out: Outcome) -> None:
    machines = rig.machines().values()
    for session in rig.sessions().values():
        if session.state != j.CLOSING:
            continue
        if any(item.session == session.id and item.state in j.LIVE for item in machines):
            continue
        cost = rig.spent(session.id, now)
        rig.set_session(session.id, j.CLOSED)
        out.lines.append(f"session {session.id} closed: cost at least {cost:.2f} $")


def reap(rig: j.Rig, providers, *, machine_key: str | None, services=(), on: bool = True,
         alive=lambda pid, mark: True) -> Outcome:
    out = Outcome()
    listings = _listings(rig, providers, services, out)
    now = rig.now()
    _close_sessions(rig, now, on, out)
    _dead_runs(rig, alive, out)
    _drain(rig, now, out)
    _destroy(rig, providers, listings, machine_key, out)
    if machine_key is not None:
        _orphans(rig, providers, listings, machine_key, out)
    _prices(rig, listings)
    _finish_sessions(rig, now, out)
    return out


def emergency(providers, *, machine_key: str, services) -> Outcome:
    """The journal is damaged: it can no longer say what is wanted, and money running is the worse error."""
    out = Outcome(failed=True)
    out.lines.append("THE RIG JOURNAL IS DAMAGED: destroying every instance labelled with this machine's key")
    for name in services:
        try:
            provider = providers(name)
            for entry in provider.instances():
                parsed = key_of_label(entry.label)
                if parsed and parsed[0] == machine_key:
                    provider.destroy(entry.instance)
                    out.lines.append(f"{name} instance {entry.instance} ({entry.label}) destroyed")
        except ProviderError as err:
            out.lines.append(f"the emergency pass could not ask {name}: {err}")
    return out
