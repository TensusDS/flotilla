"""Every flotilla install on this machine, the channel it follows, and one plan to bring them up (spec
2026-11-04-plugin-updates, part C).

`report` reads and changes nothing. `plan` turns the person's choices into steps and a mark; a working fleet has to
be stopped or deferred before there is a mark at all. `apply` carries a plan out, only with the mark of the plan the
person saw, only for a person (`caller.person_refusal`), and only through settings the documentation names and
`claude plugin` commands - never Claude Code's own records, never `marketplace remove`, never an uninstall.
Measured on Claude Code 2.1.318 (spec, "Measured"): a user entry's ref moves by editing it and then running
`claude plugin marketplace add <repo>#<ref>`, which drops `autoUpdate`, so it is written back afterwards.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from flotilla.core import claude_state, paths

CHANNEL = claude_state.CHANNEL
REPO = claude_state.REPO
DEFERRED = "updates-deferred.json"
TIMEOUT = 180


class UpdateRefused(Exception):
    pass


@dataclass(frozen=True)
class Install:
    id: str
    scope: str
    version: str
    project: Path | None
    behind: bool


@dataclass(frozen=True)
class Fleet:
    project: Path
    seats: list[str]
    interactive: list[str]
    on_user_copy: bool


@dataclass(frozen=True)
class Report:
    entry: dict | None
    foreign: str
    newest: str | None
    stable: str | None
    installs: list[Install]
    pins: list[tuple[Path, str]]
    fleets: list[Fleet]
    error: str = ""


@dataclass(frozen=True)
class Plan:
    ref: str | None                 # what the user entry is moved to; None leaves it
    stop: list[Path]
    defer: list[Path]
    updates: list[Install]
    undecided: list[Path] = field(default_factory=list)
    mark: str = ""


def _behind(version: str, newest: str | None) -> bool:
    ours, theirs = claude_state._version(version), claude_state._version(newest or "")
    return bool(ours and theirs and ours < theirs)


def _main_of(cwd: str, run) -> Path | None:
    try:
        done = run(["git", "-C", cwd, "rev-parse", "--path-format=absolute", "--git-common-dir"],
                   capture_output=True, text=True, check=False, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    out = (done.stdout or "").strip()
    return Path(out).parent.resolve() if done.returncode == 0 and out else None


def _pin(project: Path) -> str:
    declared = claude_state._declared(claude_state._settings(project), "flotilla") or {}
    return str(declared.get("ref") or "") if declared.get("source") == "github" else ""


def report(*, run=subprocess.run, census, env=os.environ, home: Path | None = None, releases=None) -> Report:
    from flotilla.core import releases as releases_mod
    from flotilla.core.census import CensusUnavailable
    asked = releases() if releases else releases_mod.cached(paths.state_dir(env), run, fresh=True)
    entry = claude_state.user_entry(env, home)
    source = (entry or {}).get("source") or {}
    foreign = "" if entry is None or (source.get("source") == "github" and
                                      str(source.get("repo") or "").lower() == REPO.lower()) \
        else claude_state._following(source)
    installs, seen = [], set()
    for name, item in claude_state.installs(env, home):
        scope, version = str(item.get("scope") or ""), str(item.get("version") or "")
        project = None
        if scope in ("project", "local"):
            path = Path(str(item.get("projectPath") or ""))
            if not path.is_absolute() or not (path / ".git").is_dir():   # gone, or a worktree: its main decides
                continue
            project = path.resolve()
        elif scope != "user":
            continue
        if (scope, project) in seen:
            continue
        seen.add((scope, project))
        installs.append(Install(name, scope, version, project, _behind(version, asked.newest)))
    installs.sort(key=lambda item: (item.project is not None, str(item.project or "")))
    pins = [(item.project, ref) for item in installs if item.project is not None
            for ref in [_pin(item.project)] if ref and ref != CHANNEL]
    error = asked.error if not asked.newest else ""
    try:
        sessions = list(census())
    except CensusUnavailable as err:
        return Report(entry, foreign, asked.newest, asked.stable, installs, pins, [],
                      f"the census could not be asked ({err}): who works where is unknown")
    owned = {item.project for item in installs if item.project is not None}
    grouped: dict[Path, tuple[list[str], list[str]]] = {}
    for item in sessions:
        main = _main_of(item.cwd, run) if item.cwd else None
        if main is None:
            continue
        seats, interactive = grouped.setdefault(main, ([], []))
        (seats if item.kind == "background" else interactive).append(item.name or item.short_id or "?")
    fleets = [Fleet(main, sorted(seats), sorted(interactive), main not in owned)
              for main, (seats, interactive) in sorted(grouped.items()) if seats]
    return Report(entry, foreign, asked.newest, asked.stable, installs, pins, fleets, error)


def _mark(report_: Report, ref, stop, defer, updates) -> str:
    body = json.dumps({"entry": report_.entry, "ref": ref, "stop": [str(p) for p in stop],
                       "defer": [str(p) for p in defer],
                       "updates": [[item.scope, str(item.project or ""), item.version] for item in updates],
                       "newest": report_.newest}, sort_keys=True)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:12]


def plan(report_: Report, *, pin: str | None = None, keep_channel: bool = False, stop=(), defer=(),
         skip=()) -> Plan:
    stop = sorted({Path(p).resolve() for p in stop})
    skip = {Path(p).resolve() for p in skip}
    defer = sorted({Path(p).resolve() for p in defer} - set(stop))
    ref = None
    if not report_.foreign and not keep_channel and report_.newest:
        wanted = pin or CHANNEL
        current = ((report_.entry or {}).get("source") or {}).get("ref")
        auto = (report_.entry or {}).get("autoUpdate") is True
        if wanted != current or (wanted == CHANNEL and not auto):
            ref = wanted
    undecided = [fleet.project for fleet in report_.fleets if fleet.project not in stop and fleet.project not in defer]
    user_held = any(fleet.on_user_copy for fleet in report_.fleets if fleet.project in defer)
    updates = [item for item in report_.installs if item.behind
               and not (item.scope == "user" and user_held)
               and not (item.project is not None and (item.project in defer or item.project in skip))]
    if report_.error and "census" in report_.error:
        return Plan(ref, stop, defer, updates, undecided, "")
    if not report_.newest or undecided:
        return Plan(ref, stop, defer, updates, undecided, "")
    if ref is None and not updates and not stop and not defer:
        return Plan(ref, stop, defer, updates, undecided, "")
    return Plan(ref, stop, defer, updates, undecided, _mark(report_, ref, stop, defer, updates))


def render(report_: Report, chosen: Plan) -> list[str]:
    out = []
    entry = report_.entry
    if report_.foreign:
        out.append(f"channel: the user settings take flotilla from {report_.foreign}; flotilla does not change that")
    elif entry is None:
        out.append("channel: the user settings declare no flotilla marketplace")
    else:
        source = entry["source"]
        out.append(f"channel: the user settings follow {REPO}"
                   + (f"#{source['ref']}" if source.get("ref") else " (default branch)")
                   + (", automatic updates on" if entry.get("autoUpdate") is True else ", automatic updates off"))
    out.append(f"newest release: v{report_.newest}" + (f", `{CHANNEL}` at {report_.stable[:12]}" if report_.stable
                                                        else f", no `{CHANNEL}` channel yet")
               if report_.newest else f"newest release: unknown ({report_.error})")
    out.append("installs:")
    for item in report_.installs:
        where = "user" if item.project is None else f"{item.scope} {item.project}"
        out.append(f"  {where}: {item.version or 'unknown'}" + ("  (behind)" if item.behind else ""))
    for project, ref in report_.pins:
        out.append(f"pinned off the channel: {project} pins {ref} in {project / '.claude' / 'settings.json'}; to "
                   f"follow the channel, its owner sets \"ref\": \"{CHANNEL}\" under extraKnownMarketplaces.flotilla."
                   f"source there and commits it")
    for fleet in report_.fleets:
        rides = " (on the user's copy)" if fleet.on_user_copy else ""
        out.append(f"fleet working in {fleet.project}{rides}: {', '.join(fleet.seats)}"
                   + (f"; the person's own: {', '.join(fleet.interactive)}" if fleet.interactive else ""))
    if report_.error and "census" in report_.error:
        out.append(f"no plan: {report_.error}")
        return out
    out.append("plan:")
    if chosen.ref:
        out.append(f"  move the user entry to {REPO}#{chosen.ref}" +
                   (" with automatic updates" if chosen.ref == CHANNEL else "") + " (settings backed up first)")
    for project in chosen.stop:
        out.append(f"  stand the fleet in {project} down, then update it")
    for project in chosen.defer:
        out.append(f"  defer {project}: its install stays as it is")
    for item in chosen.updates:
        out.append(f"  claude plugin update {item.id} --scope {item.scope}"
                   + (f"  (in {item.project})" if item.project else ""))
    for project in chosen.undecided:
        out.append(f"  undecided: a fleet works in {project} - choose --stop {project} or --defer {project}")
    out.append(f"mark: {chosen.mark}" if chosen.mark else "no mark: nothing to do, or a choice is missing")
    return out


def stand_down(project: Path, *, keep: frozenset, census) -> list[str]:
    from flotilla.fleet import retire
    from flotilla.ledger.commands import open_ledger
    ledger = open_ledger(project)
    lines, _ = retire.down(ledger, caller="update", me="", keep=keep, census=census)
    return lines


def _deferrals(state: Path) -> dict:
    try:
        data = json.loads((state / DEFERRED).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def deferred(state: Path, project: Path) -> str:
    found = _deferrals(Path(state)).get(str(Path(project).resolve()))
    if not isinstance(found, dict):
        return ""
    return (f"the update of flotilla to v{found.get('to')} waits here (deferred {found.get('at', '')[:10]}): run "
            "/flotilla:update once the fleet is down")


def _write_deferrals(state: Path, data: dict) -> None:
    state.mkdir(parents=True, exist_ok=True)
    (state / DEFERRED).write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def _claude(run, args: list[str], cwd: Path) -> tuple[bool, str]:
    try:
        done = run(["claude", *args], cwd=str(cwd), capture_output=True, text=True, check=False, timeout=TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as err:
        return False, str(err)
    text = ((done.stdout or "") + (done.stderr or "")).strip().splitlines()
    return done.returncode == 0, (text[-1] if text else f"exit {done.returncode}")


def _move_channel(ref: str, *, run, env, home, state: Path, now: dt.datetime) -> list[str]:
    settings = claude_state.config_dir(env, home) / "settings.json"
    backups = state / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    backup = backups / f"settings.json.{now.strftime('%Y%m%dT%H%M%S')}"
    try:
        data = json.loads(settings.read_text(encoding="utf-8")) if settings.exists() else {}
        if settings.exists():
            shutil.copy2(settings, backup)
    except (OSError, ValueError) as err:
        raise UpdateRefused(f"{settings} could not be read ({err}); nothing was changed") from err
    if not isinstance(data, dict):
        raise UpdateRefused(f"{settings} is not a JSON object; nothing was changed")
    known = data.setdefault("extraKnownMarketplaces", {})
    if not isinstance(known, dict):
        raise UpdateRefused(f"extraKnownMarketplaces in {settings} is not an object; nothing was changed")
    entry = known.get("flotilla") if isinstance(known.get("flotilla"), dict) else {}
    source = dict(entry.get("source") or {"source": "github", "repo": REPO})
    source["ref"] = ref
    known["flotilla"] = {**entry, "source": source}

    def write():
        tmp = settings.with_name(settings.name + ".flotilla-tmp")
        tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, settings)
    write()
    said = f"(your settings before this are in {backup})" if backup.exists() else "(there was no settings file)"
    ok, last = _claude(run, ["plugin", "marketplace", "add", f"{REPO}#{ref}"], Path(home or Path.home()))
    if not ok:
        raise UpdateRefused(f"`claude plugin marketplace add {REPO}#{ref}` failed: {last}; no install was updated "
                            f"{said}")
    if ref == CHANNEL:   # the add rewrote the entry and dropped autoUpdate (measured 5)
        data = json.loads(settings.read_text(encoding="utf-8"))
        data["extraKnownMarketplaces"]["flotilla"]["autoUpdate"] = True
        write()
    back = claude_state.user_entry(env, home) or {}
    ok, listing = _claude(run, ["plugin", "marketplace", "list"], Path(home or Path.home()))
    if (back.get("source") or {}).get("ref") != ref or (ok and f"{REPO}@{ref}" not in listing):
        raise UpdateRefused(f"the user entry or the machine's marketplace does not name {ref} after the move; no "
                            f"install was updated {said}")
    return [f"the user entry follows {REPO}#{ref}" + (" with automatic updates" if ref == CHANNEL else "")
            + f" {said}"]


def apply(report_: Report, chosen: Plan, mark: str, *, run=subprocess.run, census, env=os.environ,
          home: Path | None = None, state: Path | None = None, now: dt.datetime | None = None) -> list[str]:
    from flotilla.core import caller
    refused = caller.person_refusal("updates flotilla's installs on this machine")
    if refused:
        raise UpdateRefused(refused)
    if not chosen.mark or mark != chosen.mark:
        raise UpdateRefused("the plan changed since it was shown (or has no mark): run `flotilla update` again and "
                            "confirm what it prints")
    state = Path(state or paths.state_dir(env))
    now = now or dt.datetime.now(dt.timezone.utc)
    lines = []
    if chosen.ref:
        lines += _move_channel(chosen.ref, run=run, env=env, home=home, state=state, now=now)
    fleets = {fleet.project: fleet for fleet in report_.fleets}
    stopped = []
    for project in chosen.stop:
        fleet = fleets.get(project)
        keep = frozenset(fleet.interactive if fleet else ())
        try:
            lines += stand_down(project, keep=keep, census=census)
            stopped.append(project)
        except Exception as err:  # noqa: BLE001 - one fleet that could not be stood down holds only its own update
            lines.append(f"{project}: the fleet could not be stood down ({err}); its install was not updated")
        for name in sorted(keep):
            lines.append(f"{name} is the person's own session in {project}: it was not stopped and keeps the old "
                         "release until it is started again")
    failed_stops = set(chosen.stop) - set(stopped)
    data = _deferrals(state)
    for project in chosen.defer:
        data[str(project)] = {"to": report_.newest, "at": now.isoformat()}
        lines.append(f"deferred {project}: its fleet keeps working; its next session start and /flotilla:down say "
                     "the update waits")
    for item in chosen.updates:
        if item.project is not None and item.project in failed_stops:
            continue
        where = item.project or Path(home or Path.home())
        ok, last = _claude(run, ["plugin", "update", item.id, "--scope", item.scope], where)
        lines.append(f"{'updated' if ok else 'refused'}: {item.scope}" + (f" {item.project}" if item.project else "")
                     + f": {last}")
        if ok and item.project is not None:
            data.pop(str(item.project), None)
    _write_deferrals(state, data)
    for project in stopped:
        lines.append(f"raise the fleet in {project} again when you want it: /flotilla:spawn there")
    lines.append("running sessions keep the release they loaded; a session started from now on runs the new one")
    return lines


def run_update(args) -> int:
    import re
    from flotilla.fleet.commands import census
    if args.pin and not re.fullmatch(r"v\d+\.\d+\.\d+", args.pin):
        print(f"--pin takes a release tag, vX.Y.Z; got {args.pin!r}")
        return 2
    found = report(census=census)
    chosen = plan(found, pin=args.pin, keep_channel=args.keep_channel, stop=args.stop, defer=args.defer,
                  skip=args.skip)
    for line in render(found, chosen):
        print(line)
    if args.confirm is None:
        return 0 if chosen.mark or not chosen.undecided else 1
    try:
        lines = apply(found, chosen, args.confirm, census=census)
    except UpdateRefused as err:
        print(f"refused: {err}")
        return 1
    for line in lines:
        print(line)
    return 1 if any(line.startswith("refused:") for line in lines) else 0
