"""`flotilla rig`, `rig reap`, `rig enable`, `rig disable` (rig design, stage 1).

Off unless the person turned it on - except the reaper, which drains a machine still alive after rig was turned off:
off stops new spending, never the cleanup of what already costs money. `enable` and `disable` are the person's moves:
the guard hook refuses them to Claude's tool calls, and this refuses them to a background session.
"""

from __future__ import annotations

import datetime as dt
import fcntl
import math
import os
import signal
import subprocess
import time
from pathlib import Path

from flotilla import __version__
from flotilla.core import caller, paths
from flotilla.core.storage import LocalLogStore, StorageCorrupt
from flotilla.onboard.machine import read_machine, set_person_keys
from flotilla.ledger.errors import MoveRefused
from flotilla.rig import cron, health, provider, providers, reaper, remote, sshkey
from flotilla.rig import profile as rig_profile
from flotilla.rig import journal as j
from flotilla.rig import settings as rs

CRON_RUN = subprocess.run
PROVIDERS = provider.provider_for
CLOCK = None
ALIVE = None   # None: the machine's process table
SLEEP = time.sleep
POLL = 10.0
SSH_KEY = sshkey.ensure
LOG_LIMIT = 1_000_000
PLUGIN_ROOT = Path(__file__).resolve().parents[2]


def _now() -> dt.datetime:
    return CLOCK() if CLOCK else dt.datetime.now(dt.timezone.utc)


def _rig(state: Path) -> j.Rig:
    return j.Rig(LocalLogStore(state / "rig"), clock=CLOCK)


def _hhmm(moment) -> str:
    if not moment:
        return "?"
    when = dt.datetime.fromisoformat(moment) if isinstance(moment, str) else moment
    return when.astimezone().strftime("%H:%M")


def _alive():
    if ALIVE is not None:
        return ALIVE
    from flotilla.lane.procs import ProcessTable
    table = ProcessTable.for_machine()
    return lambda pid, mark: pid is not None and table.alive(pid, mark)


def _services() -> list[str]:
    """The services whose key is in place: their listings are where orphans are looked for."""
    return [name for name in providers.KNOWN if rs.key_path(name).exists()]


def _install(state: Path) -> Path:
    return cron.install_launcher(state, PLUGIN_ROOT, version=__version__, marketplace=cron.marketplace_of(PLUGIN_ROOT))


def _keep_reaper(rig: j.Rig, state: Path) -> str:
    """Install, refresh or remove the launcher's line as the journal needs; "" or why it could not. Called under the
    reaper's lock, so it never removes a line a session's open just needed (stage 2 takes the same lock)."""
    try:
        if reaper.needs_reaper(rig):
            line = cron.reaper_line(launcher=_install(state), python=cron.interpreter(state), state=state)
            cron.ensure(line, state, run=CRON_RUN)
        else:
            cron.remove(state, run=CRON_RUN)
    except (cron.CronError, OSError) as err:
        return str(err)
    return ""


def _status(state: Path, settings: rs.RigSettings) -> int:
    rig = _rig(state)
    machines, sessions = rig.machines(), rig.sessions()
    live = [item for item in machines.values() if item.state in j.LIVE]
    shown = [s for s in sessions.values() if s.state in (j.OPEN, j.CLOSING)]
    if not settings.on and not live and not shown:
        print(rs.OFF_LINE)
        return 0
    word = "on" if settings.on else "OFF (what is still live is drained by the reaper)"
    print(f"rig: {word}, provider {settings.provider or 'none'}; ceilings: {settings.max_machines} machine(s), "
          f"{settings.max_hourly:.2f} $/h, {settings.max_hours:g} h")
    for item in live:
        if item.state == j.STUCK:
            print(f"  STUCK machine {item.id}: instance {item.instance} may still cost money "
                  f"({item.reason or f'{item.attempts} passes'}) - check the provider's console")
    for session in shown:
        budget = f"{session.budget:.2f}" if session.budget is not None else "?"
        print(f"session {session.id} {session.state} ({session.who}: {session.why}) until {_hhmm(session.until)}, "
              f"spent at least {rig.spent(session.id):.2f} of {budget} $")
    if not shown:
        print("session: none open")
    for item in live:
        price = f"{item.hourly:.2f} $/h" if item.hourly is not None else "price unknown"
        print(f"  machine {item.id} {item.state}: instance {item.instance or 'none yet'}, {item.gpu or 'gpu ?'}, "
              f"{price}, lease until {_hhmm(item.lease_until)}{f' ({item.reason})' if item.reason else ''}")
    from flotilla.rig import surface
    for text in surface.run_lines(rig, _now()):
        print(text)
    last = health.last_reap(state)
    ok, note = health.last_outcome(state)
    if live and (last is None or _now() - last > health.SILENT_AFTER):
        print(f"REAPER SILENT: last pass {_hhmm(last) if last else 'never'} while a machine lives - is cron running "
              "here? run `flotilla rig reap` now")
    elif last:
        print(f"reaper: last pass {_hhmm(last)}" + ("" if ok else f" FAILED: {note}"))
    return 0


def _forget_hosts(rig: j.Rig, state: Path) -> None:
    """A machine that is gone takes its host key with it: vast reuses proxy ports, and a key kept for a port would
    read as a changed host for the next machine there."""
    folder = state / "rig" / "hosts"
    if not folder.is_dir():
        return
    for item in rig.machines().values():
        if item.state == j.GONE:
            (folder / item.id).unlink(missing_ok=True)


def _reap(state: Path, settings: rs.RigSettings) -> int:
    folder = state / "rig"
    folder.mkdir(parents=True, exist_ok=True)
    try:
        if (folder / "reap.log").stat().st_size > LOG_LIMIT:
            (folder / "reap.log").write_text("", encoding="utf-8")
    except OSError:
        pass
    with open(folder / "reap.lock", "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("another reaper pass is running; this one steps aside")
            return 0
        try:
            stamp = _now().isoformat(timespec="seconds")
            try:
                key = rs.machine_key(state)
            except rs.KeyRefused as err:
                key = None   # known machines still drain and are recorded; nothing is destroyed
                print(f"{stamp} {err}")
            rig = _rig(state)
            try:
                out = reaper.reap(rig, PROVIDERS, machine_key=key, services=_services(), on=settings.on, alive=_alive())
            except StorageCorrupt as err:
                if key is None:
                    print(f"{stamp} THE RIG JOURNAL IS DAMAGED ({err}) and the machine key cannot be read; check the "
                          "rental services' consoles by hand")
                    health.stamp(state, _now(), ok=False, note="journal damaged, machine key unreadable")
                    return 1
                out = reaper.emergency(PROVIDERS, machine_key=key, services=_services())
                out.lines.insert(0, f"journal: {err}")
                for line in out.lines:
                    print(f"{stamp} {line}")
                health.stamp(state, _now(), ok=False, note="journal damaged: emergency pass")
                return 1
            for line in out.lines:
                print(f"{stamp} {line}")
            _forget_hosts(rig, state)
            problem = _keep_reaper(rig, state)
            if problem:
                print(f"{stamp} the reaper's crontab line: {problem}")
            trouble = [line for line in out.lines if "could not" in line or "failed" in line or "STUCK" in line]
            ok = not (out.stuck or out.failed or problem or key is None)
            note = "" if ok else (trouble[0] if trouble else problem or "no machine key")
            health.stamp(state, _now(), ok=ok, note=note)
            return 0 if ok else 1
        except Exception as err:  # noqa: BLE001 - a pass that ran and crashed must say so: the launcher reads an
            # unmarked pass as "no flotilla here" and would fall to its last resort (final review of 0.8.0, I-1)
            said = f"{type(err).__name__}: {err}"
            print(f"{_now().isoformat(timespec='seconds')} the reaper pass failed: {said}")
            health.stamp(state, _now(), ok=False, note=said)
            return 1


def _enable(state: Path, name: str) -> int:
    refused = caller.person_refusal("turns rented machines on")
    if refused:
        print(f"refused: {refused}")
        return 2
    try:
        found = PROVIDERS(name)
    except provider.ProviderError as err:
        print(f"refused: {err}")
        return 2
    try:
        set_person_keys(state, rig="on", rig_provider=name)
        if "rig_images" not in (read_machine(state) or {}):
            set_person_keys(state, rig_images=list(rs.DEFAULT_IMAGES))
    except ValueError as err:
        print(f"refused: machine.toml cannot be read ({err}); repair it or run `flotilla onboard machine` first")
        return 2
    _install(state)
    print(f"rig is on, provider {name}; nothing is rented until the person opens a session")
    print("allowed images: " + ", ".join(rs.settings(state).images))
    try:
        rs.read_key(found.key)
    except rs.KeyRefused as err:
        print(f"but the provider key cannot be used yet: {err}")
    if not health.user_scope():
        print("note: flotilla is installed per project, so the guards on `rig enable` and the person's files run only "
              "in those projects' sessions; `claude plugin install flotilla@flotilla --scope user` closes that")
    return 0


def _disable(state: Path) -> int:
    refused = caller.person_refusal("turns rented machines off")
    if refused:
        print(f"refused: {refused}")
        return 2
    try:
        set_person_keys(state, rig="off")
    except ValueError as err:
        print(f"refused: machine.toml cannot be read ({err}); repair it first - the reaper keeps draining meanwhile")
        return 2
    print("rig is off; the reaper drains what is still live, letting a running job end")
    return 0


def _locked(state: Path, fn, wait: float = 90.0):
    """Run fn holding the reaper's lock, so no crontab edit, session check or ceiling count races a pass."""
    folder = state / "rig"
    folder.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + wait
    with open(folder / "reap.lock", "a") as lock:
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("the reaper's lock stayed held; try again in a minute") from None
                time.sleep(0.5)
        return fn()


def _number(text: str, *, what: str) -> float:
    try:
        value = float(text)
    except ValueError:
        raise ValueError(f"{what} is not a number: {text!r}") from None
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{what} must be a number more than 0")
    return value


def _caller_name() -> str:
    try:
        chain = caller.calling_sessions()
    except Exception:  # noqa: BLE001 - a name for the record, never a gate
        chain = []
    return next((s.name for s in chain if getattr(s, "name", "")), "the person")


def _asked(rig: j.Rig, request_id: str, kind: str):
    found = rig.requests().get(request_id)
    return found if found is not None and found.state == j.ASKED and found.kind == kind else None


def _open(state: Path, settings: rs.RigSettings, args) -> int:
    refused = caller.person_refusal("opens a rig session, which spends money")
    if refused:
        print(f"refused: {refused}")
        return 2
    if not settings.on:
        print(f"refused: {rs.OFF_LINE}")
        return 2
    try:
        hours, budget = _number(args.hours, what="hours"), _number(args.budget, what="budget")
    except ValueError as err:
        print(f"refused: {err}")
        return 2
    if hours > settings.max_hours:
        print(f"refused: {hours:g} hours is over the {settings.max_hours:g}-hour ceiling (machine.toml rig_max_hours)")
        return 2
    rig = _rig(state)

    def opened():
        open_ = [s for s in rig.sessions().values() if s.state == j.OPEN]
        if open_:
            return None, f"session {open_[0].id} is open until {_hhmm(open_[0].until)}; `flotilla rig close` first"
        why = args.why
        if args.for_:
            asked = _asked(rig, args.for_, "machine")
            if asked is None:
                return None, f"no open machine request {args.for_}"
            why = asked.why
        if not why:
            return None, "say why (--why) or name the request (--for rN)"
        made = rig.open_session(_caller_name(), why, hours=hours, budget=budget)
        rig.answer_requests(f"session {made.id} opened", kind="machine")
        return made, _keep_reaper(rig, state)
    try:
        session, problem = _locked(state, opened)
    except StorageCorrupt as err:
        print(f"refused: the rig journal is damaged ({err}); `flotilla rig reap` cleans up first")
        return 2
    except TimeoutError as err:
        print(f"refused: {err}")
        return 2
    if session is None:
        print(f"refused: {problem}")
        return 2
    print(f"session {session.id} open until {_hhmm(session.until)}, budget {budget:.2f} $")
    if problem:
        print(f"but the reaper's crontab line could not be kept: {problem} - nothing may be rented until it is")
        return 1
    return 0


def _close(state: Path) -> int:
    refused = caller.person_refusal("closes a rig session")
    if refused:
        print(f"refused: {refused}")
        return 2
    rig = _rig(state)
    try:
        open_ = [s for s in rig.sessions().values() if s.state == j.OPEN]
        for session in open_:
            rig.set_session(session.id, j.CLOSING, reason="closed by the person")
    except StorageCorrupt as err:
        print(f"refused: the rig journal is damaged ({err}); `flotilla rig reap` cleans up")
        return 2
    if not open_:
        print("no session is open")
        return 0
    print(f"session {', '.join(s.id for s in open_)} closing; the reaper drains its machines within 5 minutes - "
          "`flotilla rig reap` does it now")
    return 0


def _allow_image(state: Path, settings: rs.RigSettings, args) -> int:
    refused = caller.person_refusal("allows an image for rented machines")
    if refused:
        print(f"refused: {refused}")
        return 2
    rig = _rig(state)
    image = args.image
    if not rs.IMAGE.fullmatch(image or ""):
        print(f"refused: name the image in full (`flotilla rig allow-image <image>`); {image!r} is not one")
        return 2
    if args.for_:
        asked = _asked(rig, args.for_, "image")
        if asked is None or asked.image != image:
            print(f"refused: request {args.for_} does not ask for {image}"
                  + (f" (it asks for {asked.image})" if asked is not None else ""))
            return 2
    if "@sha256:" not in image:
        print(f"note: {image} is a tag, which its owner can point at other contents later; a digest "
              "(name@sha256:...) pins what runs")
    images = list(settings.images)
    if image not in images:
        images.append(image)
        try:
            set_person_keys(state, rig_images=images)
        except ValueError as err:
            print(f"refused: machine.toml cannot be read ({err})")
            return 2
    rig.answer_requests(f"image {image} allowed", kind="image",
                        ids=[r.id for r in rig.requests().values() if r.kind == "image" and r.image == image])
    print(f"image allowed: {image} ({len(images)} allowed)")
    print("note: the machine's watchdog needs curl, node or python3 in the image; without them only the reaper "
          "gives the machine back")
    return 0


def _who(as_name) -> str:
    from flotilla.lane.commands import _who as lane_who
    return lane_who(as_name)


def _project(root) -> tuple[str, rig_profile.RigProfile]:
    """The repository's key - one for every worktree of it, so a seat's request reaches its fleet's orchestrator,
    whose ledger carries the same key - and the profile read from the repository's top."""
    from flotilla.core import config, repo
    try:
        found = repo.identify(Path(root))
        key, top = found.key, found.root
    except Exception:  # noqa: BLE001 - outside a repository: the directory itself names the project
        key, top = f"dir:{Path(root).resolve()}", Path(root)
    try:
        return key, rig_profile.read(config.load_project(top).data)
    except Exception:  # noqa: BLE001 - no profile, or one that cannot be read: the defaults
        return key, rig_profile.read({})


def _wait_up(rig: j.Rig, found, machine: j.Machine, deadline: dt.datetime, who: str, why: str) -> int:
    while True:
        rig.renew([machine.id])
        if not machine.keyed:   # every call that waits attaches, so a call that resumed does too (final review, I1)
            try:
                found.attach_ssh(machine.instance, SSH_KEY())
                machine = rig.note(machine.id, keyed=_now().isoformat(timespec="seconds")) or machine
            except (provider.ProviderError, sshkey.KeyError_) as err:
                print(f"note: the rig's ssh key is not on the machine yet: {err}")
        try:
            listed = {entry.instance: entry for entry in found.instances()}
        except provider.ProviderError as err:
            print(f"note: {err}")
            listed = {}
        entry = listed.get(machine.instance)
        if entry is not None and entry.status == "running" and entry.address and machine.keyed:
            if rig.move(machine.id, j.READY, address=entry.address, expect={"state": j.PROVISIONING}) is None:
                current = rig.machines()[machine.id]
                print(f"machine {machine.id} is {current.state} ({current.reason}); not ready")
                return 2
            print(f"machine {machine.id} ready at {entry.address} (for {who}: {why})")
            return 0
        if _now() >= deadline:
            print(f"machine {machine.id} is still coming up; call `flotilla rig up` again to keep waiting - "
                  "the reaper gives it back after 15 minutes")
            return 3
        SLEEP(POLL)


def _up(state: Path, settings: rs.RigSettings, args) -> int:
    try:
        who = _who(args.as_name)
    except MoveRefused as err:
        print(f"refused: {err}")
        return 2
    return raise_machine(state, settings, who=who, why=args.why, root=args.root, wait=args.wait,
                         watchdog=args.watchdog)


def _stop(state: Path, args) -> int:
    """A seat stops its own run (field feedback from twosuns, 0.10.2): its `rig run` is signalled and ends the run on
    the machine itself; when that process is gone, the run is stopped on the machine here, by its tag."""
    rig = _rig(state)
    run = rig.runs().get(args.run_id)
    if run is None:
        print(f"refused: no run {args.run_id}")
        return 2
    from flotilla.ledger.actor import resolve_actor
    from flotilla.ledger.errors import ActorMismatch, ActorUnknown
    try:   # the census confirms who calls; outside it `--as` is only a word, and a word is no one's own run
        who = resolve_actor({}, as_name=args.as_name).name
    except ActorMismatch as err:
        print(f"refused: {err}")
        return 2
    except (ActorUnknown, MoveRefused):
        who = None
    if run.who != who:
        refused = caller.person_refusal("stops another seat's run")
        if refused:
            print(f"refused: run {run.id} is {run.who}'s; a seat stops only its own" if who else
                  f"refused: the census does not confirm who calls, so `--as` proves no run is yours; {refused}")
            return 2
    if run.state == j.DONE:
        print(f"run {run.id} already ended: {run.verdict}")
        return 0
    if run.pid and _alive()(run.pid, run.mark):
        try:
            os.kill(run.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        else:
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                done = rig.runs()[run.id]
                if done.state == j.DONE:
                    print(f"run {run.id} stopped: {done.verdict}")
                    return 0
                time.sleep(0.5)
    machine = rig.machines().get(run.machine) if run.machine else None
    if machine is not None and machine.address and run.tag:
        from flotilla.rig import run as rig_run
        box = rig_run.Box(state, machine)
        box.call(remote.STOP, f"{remote.BASE}/runs/{run.tag}.gone", run.tag)
    rig.finish_run(run.id, "stopped", reason=f"stopped by {who or 'the person'}")
    print(f"run {run.id} stopped")
    return 0


def raise_machine(state: Path, settings: rs.RigSettings, *, who: str, why: str, root, wait: float,
                  watchdog: int) -> int:
    """Raise one machine in the person's open session, or resume the one coming up: 0 ready, 2 refused, 3 still
    coming (call again). `rig up` and `rig run` both come here."""
    if not settings.on:
        print(f"refused: {rs.OFF_LINE}")
        return 2
    if not 5 <= watchdog <= 45:
        print("refused: --watchdog-minutes is 5 to 45; it may only lower the watchdog's limit")
        return 2
    deadline = _now() + dt.timedelta(seconds=min(wait, 100.0))   # the whole call: a seat's Bash cut is 120 s
    try:
        key = rs.machine_key(state)
        rig = _rig(state)
        open_ = [s for s in rig.sessions().values() if s.state == j.OPEN]
    except (MoveRefused, rs.KeyRefused, StorageCorrupt) as err:
        print(f"refused: {err}")
        return 2
    project, wanted = _project(root)
    if not open_:
        asked = rig.ask(who, why, project=project)
        print(f"refused: no rig session is open; request {asked.id} is recorded for the orchestrator, who asks the "
              f"person to paste: ! flotilla rig open --hours 2 --budget 1 --for {asked.id}")
        return 2
    session = open_[0]
    found = PROVIDERS(settings.provider)
    mine = [m for m in rig.machines().values()
            if m.session == session.id and m.state in (j.REQUESTED, j.PROVISIONING, j.READY)]
    if mine:   # resume: a call cut by its caller's timeout, or a seat asking again
        machine = mine[0]
        if machine.state == j.READY:
            print(f"machine {machine.id} already ready at {machine.address}")
            return 0
        if machine.state == j.REQUESTED:
            try:
                entry = next((e for e in found.instances() if e.label == machine.label), None)
            except provider.ProviderError as err:
                entry = None
                print(f"note: {err}")
            moved = None if entry is None else rig.move(
                machine.id, j.PROVISIONING, instance=entry.instance, hourly=entry.hourly,
                created=_now().isoformat(timespec="seconds"), expect={"state": j.REQUESTED, "instance": ""})
            if moved is None:
                print(f"machine {machine.id} is being created by another call; call `flotilla rig up` again shortly")
                return 3
            machine = moved
        return _wait_up(rig, found, machine, deadline, who, why)
    for problem in wanted.problems:
        print(f"note: {problem}")
    if wanted.image not in settings.images:
        asked = rig.ask(who, why, kind="image", project=project, image=wanted.image)
        print(f"refused: the project asks for image {wanted.image}, which the person has not allowed; request "
              f"{asked.id} is recorded - the person pastes: ! flotilla rig allow-image {wanted.image} --for {asked.id}")
        return 2
    now = _now()
    one_pass = reaper.PASS.total_seconds() / 3600
    ahead = rig.spent(session.id, now) + (rig.rate(session.id) + settings.max_hourly) * one_pass
    if session.budget is not None and ahead >= reaper.DRAIN_AT * session.budget - reaper.CENT:
        print(f"refused: session {session.id} would pass 90 % of its budget ({session.budget:.2f} $) within one pass")
        return 2
    try:
        machine = _locked(state, lambda: rig.add_machine(session.id, settings.provider, lambda mid: rs.label(key, mid),
                                                         limit=settings.max_machines), wait=10.0)
    except (j.RigError, TimeoutError) as err:
        print(f"refused: {err}")
        return 2
    try:
        want = {"gpus": list(wanted.gpus), "max_hourly": settings.max_hourly, "min_reliability": 0.98,
                "disk_gb": wanted.disk_gb, "limit": 64}
        offers = found.offers(want)
        if not offers:
            rig.move(machine.id, j.FAILED, reason="no offer within the ceilings")
            print(f"refused: no offer within the ceilings ({settings.max_hourly:.2f} $/h, datacenter, "
                  f"{', '.join(wanted.gpus) or 'any GPU'}); machine {machine.id} is failed and will be reaped")
            return 2
        offer = offers[0]
        SSH_KEY()   # a key that cannot be made fails before money is spent
        instance = found.create(offer.offer, image=wanted.image, disk_gb=wanted.disk_gb,
                                env={"FLOTILLA_WATCHDOG_MINUTES": str(watchdog),
                                     "NVIDIA_DRIVER_CAPABILITIES": "all"},
                                onstart=found.onstart(), label=machine.label)
    except (provider.ProviderError, sshkey.KeyError_) as err:
        rig.move(machine.id, j.FAILED, reason=f"create failed: {str(err)[:200]}")
        print(f"refused: {err}; machine {machine.id} is failed - if the service made it anyway, the reaper finds it "
              "by its label and destroys it")
        return 2
    try:
        moved = rig.move(machine.id, j.PROVISIONING, instance=instance, gpu=offer.gpu, hourly=offer.hourly,
                         created=_now().isoformat(timespec="seconds"), expect={"state": j.REQUESTED})
    except j.RigError as err:
        moved = None
        print(f"note: {err}")
    if moved is None:
        print(f"machine {machine.id} changed while it was created (instance {instance}); the reaper finds the "
              "instance by its label and destroys it")
        return 2
    print(f"machine {machine.id}: instance {instance}, {offer.gpu}, {offer.hourly:.3f} $/h - coming up")
    return _wait_up(rig, found, moved, deadline, who, why)


def run_rig_command(args) -> int:
    state = paths.state_dir()
    settings = rs.settings(state)
    try:
        if args.action == "reap":
            return _reap(state, settings)
        if args.action == "enable":
            return _enable(state, args.provider)
        if args.action == "disable":
            return _disable(state)
        if args.action == "open":
            return _open(state, settings, args)
        if args.action == "close":
            return _close(state)
        if args.action == "up":
            return _up(state, settings, args)
        if args.action == "stop":
            return _stop(state, args)
        if args.action == "run":
            from flotilla.rig import run as rig_run
            return rig_run.main(state, settings, args)
        if args.action == "allow-image":
            return _allow_image(state, settings, args)
        return _status(state, settings)
    except StorageCorrupt as err:
        print(f"the rig journal is damaged: {err}; `flotilla rig reap` destroys this machine's labelled instances")
        return 2
