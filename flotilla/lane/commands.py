"""`flotilla lane`, `lane take`, `lane release`, `lane run`, `lane sweep`, and the lane around a receipt."""

from __future__ import annotations

import contextlib
import os
import signal
from pathlib import Path

from flotilla.core import config, paths, repo
from flotilla.core.storage import LocalLogStore, StorageCorrupt
from flotilla.lane import acquire as acq
from flotilla.lane import book, machine
from flotilla.lane import run as runner
from flotilla.lane.procs import ProcessTable
from flotilla.ledger import gitq, runs
from flotilla.ledger.actor import resolve_actor
from flotilla.ledger.errors import ActorUnknown, MoveRefused
from flotilla.onboard.machine import read_machine
from flotilla.posts import PostError


def capacity() -> int:
    value = (read_machine(paths.state_dir()) or {}).get("lane_capacity", 1)
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 1 else 1


def _lanes(table) -> book.Book:
    return book.Book(LocalLogStore(paths.state_dir() / "lane"), table)


def _profile(root) -> tuple[dict, Path]:
    from flotilla.ledger.commands import trunk_rules
    try:
        return trunk_rules(Path(root)).profile, repo.identify(Path(root)).root
    except (config.ConfigError, repo.NotARepository, PostError):
        return {}, Path(root)


def _who(as_name) -> str:
    try:
        return resolve_actor({}, as_name=as_name).name
    except (ActorUnknown, MoveRefused):
        return as_name or "outside any listed session"


def _reader(lanes, table, profile, root):
    return lambda: machine.read(lanes, table, profile, own_pid=os.getpid(), root=root)


def _on_signals() -> None:
    def leave(number, _frame):
        raise SystemExit(128 + number)
    for number in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(number, leave)


@contextlib.contextmanager
def booked(root, *, note: str, wait: float, run_for: str = "", as_name=None):
    table = ProcessTable.for_machine()
    lanes = _lanes(table)
    profile, top = _profile(root)
    with acq.held(lanes, _reader(lanes, table, profile, top), who=_who(as_name), note=note, capacity=capacity(),
                  wait=wait, table=table, run_for=run_for) as grant:
        yield grant


def _status(args) -> int:
    table = ProcessTable.for_machine()
    lanes = _lanes(table)
    profile, top = _profile(args.root)
    print(f"capacity: {capacity()} long run(s) at a time (machine.toml lane_capacity)")
    held, waiting = lanes.holders(), lanes.waiters()
    print("held:" if held else "held: nobody")
    for item in held:
        state = "alive" if lanes.live(item) else "its process is gone - `flotilla lane sweep`"
        by = f"pid {item.pid}" if item.pid else "taken by hand - `flotilla lane release`"
        target = f", for {item.run_for}" if item.run_for else ""
        print(f"  {item.id} {item.who} ({item.note or 'no note'}) since {item.since}{target}; {by}; {state}")
    print("waiting:" if waiting else "waiting: nobody")
    for item in waiting:
        print(f"  {item.id} {item.who} ({item.note or 'no note'}) since {item.since}")
    print("machine:")
    for answer in _reader(lanes, table, profile, top)().answers:
        flag = {True: "busy", False: "ok", None: "unknown"}[answer.blocks]
        print(f"  {answer.question}: {flag} - {answer.text}")
    return 0


def _take(args) -> int:
    table = ProcessTable.for_machine()
    lanes = _lanes(table)
    profile, top = _profile(args.root)
    _on_signals()
    grant = acq.acquire(lanes, _reader(lanes, table, profile, top), who=_who(args.as_name), note=args.note,
                        capacity=capacity(), wait=args.wait)
    if grant.booking is None:
        print(f"refused: {grant.why}")
        return 2
    print(f"the lane is yours: booking {grant.booking.id}; release it with `flotilla lane release` when done")
    return 0


def _release(args) -> int:
    lanes = _lanes(ProcessTable.for_machine())
    who = _who(args.as_name)
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


def _record(args, result) -> None:
    from flotilla.ledger.commands import open_ledger
    try:
        ledger = open_ledger(Path(args.tree))
        who = resolve_actor(ledger.posts, as_name=args.as_name)
        revision = gitq.resolve(Path(args.tree), "HEAD") or ""
        runs.record_run(ledger, who, args.for_, verdict=result.verdict, summary=result.summary, revision=revision,
                        evidence={"exit": result.exit, "verdict": result.verdict, "summary": result.summary,
                                  "revision": revision, "signal": result.signal})
    except (MoveRefused, PostError, config.ConfigError, repo.NotARepository, StorageCorrupt) as err:
        print(f"note: the run was not recorded on the ledger: {err}")


def _run(args) -> int:
    command = list(args.run_command)
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        print("refused: name the command after --, for example `flotilla lane run -- uv run pytest`")
        return 2
    _on_signals()
    try:
        with booked(args.tree, note=args.note or " ".join(command)[:80], wait=args.wait, run_for=args.for_ or "",
                    as_name=args.as_name):
            result = runner.execute(command, cwd=args.tree)
    except acq.LaneRefused as err:
        print(f"refused: {err}")
        return 2
    print(f"lane run: {result.verdict} - {result.summary}")
    if args.for_:
        _record(args, result)
    return result.exit


def run_lane_command(args) -> int:
    try:
        return {None: _status, "take": _take, "release": _release, "run": _run, "sweep": _sweep}[args.action](args)
    except (StorageCorrupt, config.ConfigError) as err:
        print(f"refused: {err}")
        return 2
