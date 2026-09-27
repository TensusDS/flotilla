"""`flotilla spawn`, `flotilla retire` and `flotilla fleet`."""

from __future__ import annotations

import os
from pathlib import Path

from flotilla.core import config, paths, repo
from flotilla.core import platform as plat
from flotilla.core.census import CensusUnavailable, read_census
from flotilla.core.identity import find_calling_session
from flotilla.core.storage import LocalLogStore, StorageCorrupt
from flotilla.fleet import compose, launch, retire, spawn
from flotilla.ledger.actor import NO_CENSUS
from flotilla.ledger.commands import open_ledger
from flotilla.ledger.errors import MoveRefused
from flotilla.posts import PostError


def census():
    if os.environ.get(NO_CENSUS):
        raise CensusUnavailable(f"{NO_CENSUS} is set")
    return read_census()


def caller_line(sessions) -> str:
    source = plat.probe().parent_pid_source
    found = find_calling_session(sessions, parent_of=lambda pid: plat.parent_pid(pid, source))
    return f"by {found.name}" if found is not None and found.name else "outside any listed session"


def counts_from(args, profile: dict) -> dict:
    flags = {post: getattr(args, f"count_{post}") for post in compose.FLAGS if getattr(args, f"count_{post}")}
    for item in args.post:
        name, sep, value = item.partition("=")
        if not sep or not value.strip().isdigit():
            raise MoveRefused(f"--post {item}: write NAME=N, for example --post minor=2")
        flags[name.strip()] = flags.get(name.strip(), 0) + int(value)
    if args.default and flags:
        raise MoveRefused("either --default or counts, not both")
    if args.default:
        return dict((profile.get("fleet") or {}).get("default") or {})
    return flags


def _spawn(ledger, args) -> int:
    counts = counts_from(args, ledger.profile)
    store = LocalLogStore(paths.state_dir() / "fleet")
    if args.dry_run:
        try:
            census()
            probe = census
        except CensusUnavailable as err:
            print(f"census: unknown ({err}); names may collide with live sessions")
            probe = lambda: []  # noqa: E731
        seats, warnings = spawn.plan(ledger, counts, census=probe, store=store, reserve=False)
        for line in warnings:
            print(f"warning: {line}")
        main = launch.main_checkout(ledger.root, run=ledger.run)
        for seat in seats:
            post = ledger.posts[seat.post]
            model = launch.model_for(ledger.profile, post)
            print(f"{seat.name}  ({seat.post})  tree {seat.tree}  branch {seat.branch}")
            print(f"    claude --bg -n \"{seat.name}\" --add-dir {seat.tree} --permission-mode "
                  f"{launch.permission_mode(ledger.profile, post)}" + (f" --model {model}" if model else "")
                  + f"  (from {main})")
        print(f"dry run: {len(seats)} session(s) planned; nothing was changed")
        return 0
    try:
        sessions = census()
    except CensusUnavailable as err:
        raise spawn.SpawnRefused(f"spawning needs the census to check names, and it could not be asked: "
                                 f"{err}") from err
    raised, warnings = spawn.spawn(ledger, counts, census=census, store=store,
                                   caller=f"spawn {caller_line(sessions)}")
    for line in warnings:
        print(f"warning: {line}")
    for item in raised:
        address = f"claude attach {item.short_id}" if item.short_id else item.note
        print(f"{item.seat.name}  {address}  {item.seat.tree}")
    print("a closed terminal tab does not stop a session; `flotilla fleet` lists the fleet")
    return 0


def _fleet(ledger, args) -> int:
    try:
        sessions = census()
    except CensusUnavailable as err:
        sessions = None
        print(f"census: unknown ({err}); liveness is not asserted")
    view = retire.fleet_view(ledger, sessions)
    if not view:
        print("no post rows: nobody was spawned, or everyone was retired")
    for item in view:
        live = {True: f"alive ({item['state'] or 'no state'}), claude attach {item['short_id']}",
                False: "not running", None: "liveness unknown"}[item["live"]]
        tree = item["tree"] if item["tree_exists"] else f"{item['tree']} (missing)"
        lock = "locked" if item["locked"] else "not locked"
        work = ", ".join(f"{row.branch} ({row.state})" for row in item["work"]) or "no open work"
        print(f"{item['name']}  ({item['post'] or 'no post'})  {live}\n    tree {tree}, {lock}; {work}")
    return 0


def _retire(ledger, args) -> int:
    try:
        sessions = census()
        caller = f"retire {caller_line(sessions)}"
    except CensusUnavailable:
        caller = "retire, census unknown"
    for line in retire.retire(ledger, args.name, caller=caller, census=census):
        print(line)
    return 0


def run_fleet_command(args) -> int:
    try:
        ledger = open_ledger(Path(args.root))
        return {"spawn": _spawn, "fleet": _fleet, "retire": _retire}[args.command](ledger, args)
    except (MoveRefused, PostError, CensusUnavailable, launch.LaunchError, config.ConfigError,
            repo.NotARepository, StorageCorrupt) as err:
        print(f"refused: {err}")
        return 2
