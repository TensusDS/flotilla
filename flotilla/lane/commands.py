"""`flotilla lane`, `lane take`, `lane release`, `lane run`, `lane sweep`, and the lane around a receipt."""

from __future__ import annotations

import contextlib
import datetime as dt
import os
import signal
import subprocess
import time
from pathlib import Path

from flotilla.core import config, paths, repo
from flotilla.core.storage import LocalLogStore, StorageCorrupt
from flotilla.lane import acquire as acq
from flotilla.lane import book, machine
from flotilla.lane import run as runner
from flotilla.lane.procs import ProcessTable
from flotilla.ledger import gitq, runs
from flotilla.ledger.actor import resolve_actor
from flotilla.ledger.errors import ActorMismatch, ActorUnknown, MoveRefused
from flotilla.ledger.model import LedgerVersionError
from flotilla.onboard.machine import read_machine
from flotilla.posts import PostError


def capacity() -> int:
    value = (read_machine(paths.state_dir()) or {}).get("lane_capacity", 1)
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 1 else 1


def _lanes(table) -> book.Book:
    return book.Book(LocalLogStore(paths.state_dir() / "lane"), table)


def _profile(root) -> tuple[dict, Path, str]:
    """The profile, the repository root, and why the profile could not be read ("" when it was, or there is none)."""
    from flotilla.ledger.commands import trunk_rules
    try:
        top = repo.identify(Path(root)).root
    except repo.NotARepository:
        return {}, Path(root), ""
    if config.find_project(Path(root)) is None:
        return {}, top, ""
    try:
        return trunk_rules(Path(root)).profile, top, ""
    except (config.ConfigError, PostError) as err:
        return {}, top, str(err)


#: A run's ceiling when nothing was measured and the profile names none.
CEILING_UNMEASURED = 1800
#: Never under ten minutes: a ceiling exists for a run that will not end, not for a slow one.
CEILING_FLOOR = 600


def ceiling(profile: dict, measured: dict, *, tiers: bool = False) -> int:
    """Seconds a run in the lane may take (worldcore field test W21): the profile's `[lane] max_run_seconds`; else ten
    times the slowest tier measured on this machine - never under ten minutes for a receipt, whose commands are those
    tiers, and never under half an hour for any other run, which no measurement describes; else half an hour."""
    named = (profile.get("lane") or {}).get("max_run_seconds")
    if isinstance(named, (int, float)) and not isinstance(named, bool) and named > 0:
        return int(named)
    slowest = max((float(v) for v in measured.values() if isinstance(v, (int, float))), default=0.0)
    if not slowest:
        return CEILING_UNMEASURED
    return max(CEILING_FLOOR if tiers else CEILING_UNMEASURED, int(slowest * 10))


def ceiling_for(root, profile: dict, *, tiers: bool = False) -> int:
    from flotilla.onboard.firstrun import load_measurements
    try:
        measured = load_measurements(paths.state_dir(), repo.identify(Path(root)).key)
    except (repo.NotARepository, OSError, ValueError):
        measured = {}
    return ceiling(profile, measured, tiers=tiers)


def _who(as_name) -> str:
    try:
        return resolve_actor({}, as_name=as_name).name
    except ActorMismatch as err:   # the census names someone else: never their word (review of 0.6.2, I2)
        raise MoveRefused(str(err)) from err
    except (ActorUnknown, MoveRefused):
        return as_name or "outside any listed session"


def _reader(lanes, table, profile, root, problem=""):
    return lambda: machine.read(lanes, table, profile, own_pid=os.getpid(), root=root, problem=problem)


def _on_signals() -> None:
    def leave(number, _frame):
        raise SystemExit(128 + number)
    for number in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(number, leave)


@contextlib.contextmanager
def booked(root, *, note: str, wait: float, run_for: str = "", as_name=None):
    table = ProcessTable.for_machine()
    lanes = _lanes(table)
    profile, top, problem = _profile(root)
    with acq.held(lanes, _reader(lanes, table, profile, top, problem), who=_who(as_name), note=note,
                  capacity=capacity(), wait=wait, table=table, run_for=run_for, say=print) as grant:
        yield grant


def _for_how_long(since: str) -> str:
    try:
        began = dt.datetime.fromisoformat(since)
    except (TypeError, ValueError):
        return ""
    minutes = int((dt.datetime.now(dt.timezone.utc) - began).total_seconds() // 60)
    return f" ({minutes} min)"


def _status(args) -> int:
    table = ProcessTable.for_machine()
    lanes = _lanes(table)
    profile, top, problem = _profile(args.root)
    print(f"capacity: {capacity()} long run(s) at a time (machine.toml lane_capacity)")
    held, waiting = lanes.holders(), lanes.waiters()
    print("held:" if held else "held: nobody")
    for item in held:
        state = "alive" if lanes.live(item) else "its process is gone - `flotilla lane sweep`"
        by = f"pid {item.pid}" if item.pid else "taken by hand - `flotilla lane release`"
        target = f", for {item.run_for}" if item.run_for else ""
        print(f"  {item.id} {item.who} ({item.note or 'no note'}) since {item.since}{_for_how_long(item.since)}"
              f"{target}; {by}; {state}")
    print("waiting:" if waiting else "waiting: nobody")
    for item in waiting:
        gone = "" if lanes.live(item) else "; its process is gone - `flotilla lane sweep`"
        print(f"  {item.id} {item.who} ({item.note or 'no note'}) since {item.since}{gone}")
    print("machine:")
    for answer in _reader(lanes, table, profile, top, problem)().answers:
        flag = {True: "busy", False: "ok", None: "unknown"}[answer.blocks]
        print(f"  {answer.question}: {flag} - {answer.text}")
    return 0


def _take(args) -> int:
    table = ProcessTable.for_machine()
    lanes = _lanes(table)
    profile, top, problem = _profile(args.root)
    _on_signals()
    pid = os.getpid()
    grant = acq.acquire(lanes, _reader(lanes, table, profile, top, problem), who=_who(args.as_name), note=args.note,
                        capacity=capacity(), wait=args.wait, pid=pid, mark=table.start_mark(pid) or "",
                        by_hand=True, say=print)
    if grant.booking is None:
        print(f"refused: {grant.why}")
        return 2
    print(f"the lane is yours: booking {grant.booking.id}; release it with `flotilla lane release` when done")
    return 0


def _release(args) -> int:
    lanes = _lanes(ProcessTable.for_machine())
    who = _who(args.as_name)
    if args.booking is not None:
        item = lanes.bookings().get(args.booking)
        if item is not None and item.state == book.WAITING:
            lanes.leave(item.id)
            print(f"ended {item.id}: {item.who} no longer waits")
            return 0
    held = [item for item in lanes.holders() if args.booking in (None, item.id)]
    if args.booking is None:
        held = [item for item in held if item.who == who]
    if not held:
        print(f"refused: no booking held by {who}" if args.booking is None else f"refused: {args.booking} is not held")
        return 2
    if len(held) > 1:
        print("refused: several bookings match; name one with --booking: " + ", ".join(item.id for item in held))
        return 2
    lanes.release(held[0].id, why=f"released by {who}")
    print(f"released {held[0].id}")
    return 0


def _sweep(args) -> int:
    swept = _lanes(ProcessTable.for_machine()).sweep()
    if not swept:
        print("nothing to sweep: every booking has its process, or was taken by hand")
    for item in swept:
        print(f"swept {item.id} {item.who} ({item.note or 'no note'}): no process behind it")
    return 0


def _where(tree: Path) -> tuple[str, str, bool | None]:
    """The tree's branch, its HEAD and whether it is clean, asked before the command runs."""
    done = subprocess.run(["git", "-C", str(tree), "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True,
                          text=True, check=False)
    return (done.stdout.strip() if done.returncode == 0 else "", gitq.resolve(tree, "HEAD") or "",
            gitq.is_clean(tree))


def _record(args, result, where, started: str) -> None:
    from flotilla.ledger.commands import open_ledger
    branch, revision, clean = where
    if branch != args.for_:
        print(f"note: the run was not recorded on the ledger: the tree is on {branch or 'no branch'}, not "
              f"{args.for_}, so the result is not about that branch")
        return
    try:
        ledger = open_ledger(Path(args.tree))
        who = resolve_actor(ledger.posts, as_name=args.as_name)
        runs.record_run(ledger, who, args.for_, verdict=result.verdict, summary=result.summary, revision=revision,
                        dirty=clean is not True,
                        evidence={"exit": result.exit, "verdict": result.verdict, "summary": result.summary,
                                  "revision": revision, "clean": clean, "signal": result.signal,
                                  "started": started})
    except (MoveRefused, PostError, config.ConfigError, repo.NotARepository, StorageCorrupt,
            LedgerVersionError) as err:
        print(f"note: the run was not recorded on the ledger: {err}")


def _run(args) -> int:
    command = list(args.run_command)
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        print("refused: name the command after --, for example `flotilla lane run -- uv run pytest`")
        return 2
    _on_signals()
    where, started = None, None
    try:
        with booked(args.tree, note=args.note or " ".join(command)[:80], wait=args.wait, run_for=args.for_ or "",
                    as_name=args.as_name):
            where = _where(Path(args.tree))
            started = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
            limit = args.max if args.max else ceiling_for(args.tree, _profile(args.tree)[0])
            result = runner.execute(command, cwd=args.tree, ceiling=limit)
    except acq.LaneRefused as err:
        print(f"refused: {err}")
        return 2
    except SystemExit as stop:   # `lane stop`, or the session ending: the command's group was stopped first
        if started is None:
            raise
        result = runner.RunResult(stop.code if isinstance(stop.code, int) else 143, "killed",
                                  "stopped by a signal (`flotilla lane stop`, or the session ending) - no verdict",
                                  15)
    print(f"lane run: {result.verdict} - {result.summary}")
    if args.for_:
        _record(args, result, where, started)
    return result.exit


def _stop_run(args) -> int:
    """Stop the caller's own run in the lane by the pid flotilla recorded, so nobody has to find it with `pkill`
    (worldcore field test W22: a seat's `pkill -f` matched its own command line and killed itself first)."""
    lanes = _lanes(ProcessTable.for_machine())
    who = _who(args.as_name)
    held = [item for item in lanes.holders() if item.pid and args.booking in (None, item.id)]
    mine = [item for item in held if item.who == who]
    if not mine:
        others = ", ".join(f"{item.id} ({item.who})" for item in held)
        print(f"refused: no run in the lane is {who}'s" + (f"; held: {others} - ask its holder" if others else ""))
        return 2
    if len(mine) > 1:
        print("refused: several runs are yours; name one with --booking: " + ", ".join(item.id for item in mine))
        return 2
    item = mine[0]
    if not lanes.live(item):
        print(f"{item.id}'s process is already gone; `flotilla lane sweep` clears the booking")
        return 0
    os.kill(item.pid, signal.SIGTERM)   # the run stops its command's whole group and releases the lane itself
    for _ in range(100):
        if not lanes.live(item):
            print(f"stopped {item.id} ({item.note or 'no note'}) with everything it started: a `lane run` is recorded "
                  "as killed, with no verdict; a receipt issues none")
            return 0
        time.sleep(0.1)
    print(f"sent the stop to {item.id} (pid {item.pid}); it has not ended within 10 s - `flotilla lane` shows it")
    return 1


def run_lane_command(args) -> int:
    try:
        return {None: _status, "status": _status, "take": _take, "release": _release, "run": _run, "sweep": _sweep,
                "stop": _stop_run}[args.action](args)
    except (StorageCorrupt, config.ConfigError, MoveRefused) as err:
        print(f"refused: {err}")
        return 2
