"""`flotilla rig`, `rig reap`, `rig enable`, `rig disable` (rig design, stage 1).

Off unless the person turned it on - except the reaper, which drains a machine still alive after rig was turned off:
off stops new spending, never the cleanup of what already costs money. `enable` and `disable` are the person's moves:
the guard hook refuses them to Claude's tool calls, and this refuses them to a background session.
"""

from __future__ import annotations

import datetime as dt
import fcntl
import math
import subprocess
import time
from pathlib import Path

from flotilla import __version__
from flotilla.core import caller, paths
from flotilla.core.storage import LocalLogStore, StorageCorrupt
from flotilla.onboard.machine import read_machine, set_person_keys
from flotilla.rig import cron, health, provider, providers, reaper
from flotilla.rig import journal as j
from flotilla.rig import settings as rs

CRON_RUN = subprocess.run
PROVIDERS = provider.provider_for
CLOCK = None
ALIVE = None   # None: the machine's process table
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
    last = health.last_reap(state)
    ok, note = health.last_outcome(state)
    if live and (last is None or _now() - last > health.SILENT_AFTER):
        print(f"REAPER SILENT: last pass {_hhmm(last) if last else 'never'} while a machine lives - is cron running "
              "here? run `flotilla rig reap` now")
    elif last:
        print(f"reaper: last pass {_hhmm(last)}" + ("" if ok else f" FAILED: {note}"))
    return 0


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
    return 0


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
        if args.action == "allow-image":
            return _allow_image(state, settings, args)
        return _status(state, settings)
    except StorageCorrupt as err:
        print(f"the rig journal is damaged: {err}; `flotilla rig reap` destroys this machine's labelled instances")
        return 2
