"""`flotilla spawn`, `flotilla retire` and `flotilla fleet`."""

from __future__ import annotations

import os
from pathlib import Path

from flotilla.core import config, paths, repo
from flotilla.core import platform as plat
from flotilla.core.census import CensusUnavailable, read_census
from flotilla.core.identity import find_calling_session
from flotilla.core.storage import LocalLogStore, StorageCorrupt
from flotilla.fleet import compose, launch, names, plugins, retire, spawn, strangers
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
            live = {item.name for item in census() if item.name}
            probe = census
        except CensusUnavailable as err:
            print(f"census: unknown ({err}); names may collide with live sessions")
            live, probe = set(), (lambda: [])  # noqa: E731
        seats, warnings = spawn.plan(ledger, counts, census=probe, store=store, reserve=False, strict=False)
        main = launch.main_checkout(ledger.root, run=ledger.run)
        narrow = plugins.narrowing(ledger.run, main)
        warnings = warnings + narrow.warnings([ledger.posts[seat.post] for seat in seats])
        for line in warnings:
            print(f"warning: {line}")
        taken = live | {name for row in ledger.rows().values() for name in (row.owner, row.reader) if name}
        for post_name in dict.fromkeys(seat.post for seat in seats):   # why a number is not 1 (F7)
            said = names.numbered_after(ledger.posts[post_name], taken=taken, live=live, store=store)
            if said:
                print(f"note: {said}")
        for seat in seats:
            post = ledger.posts[seat.post]
            model = launch.model_for(ledger.profile, post)
            print(f"{seat.name}  ({seat.post})  tree {seat.tree}  branch {seat.branch}")
            print(f"    claude --bg -n \"{seat.name}\" --add-dir {seat.tree} --permission-mode "
                  f"{launch.permission_mode(ledger.profile, post)}" + (f" --model {model}" if model else "")
                  + f"  (from {main})")
            off = ", ".join(narrow.turned_off(post)) or "none"
            print(f"    turns off: {off if narrow.entries is not None else 'unknown, ' + plugins.NOT_NARROWED}")
        print(f"dry run: {len(seats)} session(s) planned; nothing was changed")
        return 0
    try:
        sessions = census()
    except CensusUnavailable as err:
        raise spawn.SpawnRefused(f"spawning needs the census to check names, and it could not be asked: "
                                 f"{err}") from err
    try:
        raised, warnings = spawn.spawn(ledger, counts, census=census, store=store,
                                       caller=f"spawn {caller_line(sessions)}", anyway=args.anyway)
    except spawn.SpawnStopped as err:
        print("raised before the spawn stopped:")
        _print_raised(err.raised)
        raise
    for line in warnings:
        print(f"warning: {line}")
    _print_raised(raised)
    print("a closed terminal tab does not stop a session; `flotilla fleet` lists the fleet")
    return 0


def _print_raised(raised) -> None:
    for item in raised:
        address = f"claude attach {item.short_id}" if item.short_id else "no id yet"
        note = f"  ({item.note})" if item.note else ""
        print(f"{item.seat.name}  {address}  {item.seat.tree}{note}")


def _fleet(ledger, args) -> int:
    if getattr(args, "action", None) == "down":
        return _down(ledger)
    try:
        sessions = census()
    except CensusUnavailable as err:
        sessions = None
        print(f"census: unknown ({err}); liveness is not asserted")
    view = retire.fleet_view(ledger, sessions)
    if not view:
        print("no post rows: nobody was spawned, or everyone was retired")
    for item in view:
        if item["stranger"]:
            stop = f"; `claude stop {item['short_id']}` stops it" if item["short_id"] else ""
            print(f"{item['name']}  {strangers.label(item['stranger'])}{stop}")
            continue
        attach = f"claude attach {item['short_id']}" if item["short_id"] else "no id in the census"
        live = {True: f"alive ({item['state'] or 'no state'}), {attach}",
                False: "not running", None: "liveness unknown"}[item["live"]]
        dirty = {None: ", its state unknown", 0: ""}.get(item["dirty"], f", {item['dirty']} uncommitted")
        tree = item["tree"] if item["tree_exists"] else f"{item['tree']} (missing)"
        lock = "locked" if item["locked"] else "not locked"
        work = ", ".join(f"{row.branch} ({row.state})" for row in item["work"]) or "no open work"
        print(f"{item['name']}  ({item['post'] or 'no post'})  {live}\n    tree {tree}{dirty}, {lock}; {work}")
    return 0


def _down(ledger) -> int:
    try:
        sessions = census()
    except CensusUnavailable as err:
        raise retire.RetireRefused(f"the census could not be asked ({err}); nothing was stopped or "
                                   "released") from err
    source = plat.probe().parent_pid_source
    found = find_calling_session(sessions, parent_of=lambda pid: plat.parent_pid(pid, source))
    me = found.name if found is not None and found.name else ""
    if not me and os.environ.get("CLAUDECODE"):
        raise retire.RetireRefused("cannot tell which session runs this, and stopping it mid-command would cut the "
                                   "command short; nothing was stopped: run `flotilla fleet down` from a terminal")
    lines, refused = retire.down(ledger, caller=f"fleet down {caller_line(sessions)}", me=me, census=census)
    print("\n".join(lines))
    return 1 if refused else 0


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
