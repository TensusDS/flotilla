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


def calling_session(sessions):
    """The live session this command runs in, or None."""
    source = plat.probe().parent_pid_source
    return find_calling_session(sessions, parent_of=lambda pid: plat.parent_pid(pid, source))


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
    if (args.default or args.fill) and flags:
        raise MoveRefused("either --default, --fill or counts, not both")
    if args.default or args.fill:
        return dict((profile.get("fleet") or {}).get("default") or {})
    return flags


def _lead(ledger, args) -> int:
    """Reserve the orchestrator's name for the person's own session (decision 191): only a person renames an
    interactive session, so flotilla can only issue the name - from the journal, never reissued (review of 0.5.0, I3)."""
    from flotilla.core import caller
    from flotilla.ledger import project
    if counts_from(args, {}) or args.default or args.fill or args.dry_run:
        raise MoveRefused("--lead takes nothing else: it names this session the orchestrator; "
                          "`flotilla spawn --fill` raises the rest afterwards")
    refused = caller.person_refusal("leads the fleet from their own session")
    if refused:
        raise MoveRefused(refused)
    post = next((p for p in ledger.posts.values() if p.name == "orchestrator"), None)
    if post is None:
        raise MoveRefused("this project has no orchestrator post in .flotilla/posts/")
    try:
        sessions = census()
    except CensusUnavailable as err:
        raise MoveRefused(f"--lead needs the census to keep the name unique ({err})") from err
    mine = project.members(sessions, ledger.rows(), project.roots(ledger.root))
    held = [item.name for item in mine if item.name and names.number_of(post, item.name) is not None]
    if held:
        print(f"note: {', '.join(held)} already lead(s) this project's fleet")
    taken = {item.name for item in sessions if item.name}
    taken |= {name for row in ledger.rows().values() for name in (row.owner, row.reader) if name}
    from flotilla.fleet import lead
    store = LocalLogStore(paths.state_dir() / "fleet")
    name = names.next_names(post, 1, taken=taken, store=store, reserve=True, now=ledger.now())[0]
    me = calling_session(sessions)
    if me is not None and me.session_id:   # the prompt hook names it on the person's next message (W9)
        lead.record(store, me.session_id, name, now=ledger.now())
        print(f"{name}: this session leads the fleet. Claude Code shows the name once you send your next message; "
              "raise the rest with `flotilla spawn --fill` after that message, so the seats learn this name and "
              "not the old one.")
        return 0
    print(f"{name}: reserved, but flotilla could not tell which session runs this, so it cannot name it. Type "
          f"`/rename {name}`, then send any message; run `flotilla spawn --fill` after that message.")
    return 0


def _unnamed_leads(ledger, mine) -> list[tuple[str, str]]:
    """(name now, name to come) of this project's live sessions that lead the fleet but whose new name the census
    does not show yet. Seats raised in that gap learn the old name, and their letters go nowhere once it changes
    (W14), so --fill waits for it (review of 0.6.7, I1)."""
    from flotilla.fleet import lead
    import datetime as dt
    waiting = lead.pending(LocalLogStore(paths.state_dir() / "fleet"), now=dt.datetime.now(dt.timezone.utc))
    return [(item.name or item.session_id, waiting[item.session_id]) for item in mine
            if item.session_id in waiting and item.name != waiting[item.session_id]]


def _spawn(ledger, args) -> int:
    from flotilla.ledger.commands import fleet_note
    said = fleet_note(ledger.root, ledger.profile)
    if said:
        print(f"note: {said}")
    if getattr(args, "lead", False):
        return _lead(ledger, args)
    counts = counts_from(args, ledger.profile)
    if args.fill:   # the default, less the posts this project's live sessions already hold
        from flotilla.ledger import project
        try:
            mine = project.members(census(), ledger.rows(), project.roots(ledger.root))   # the census is the
            held = dict(spawn._live_posts(ledger, mine))                                   # machine's (M3)
        except CensusUnavailable as err:
            raise MoveRefused(f"--fill needs the census to see who is alive ({err}); name the counts instead") from err
        unnamed = _unnamed_leads(ledger, mine)
        if unnamed:
            now_, coming = unnamed[0]
            raise MoveRefused(f"the leading session `{now_}` does not carry its name `{coming}` yet, and seats raised "
                              "now would learn the old one; run --fill after the person's next message, which "
                              f"brings the name (or after they type `/rename {coming}`)")
        counts = compose.fill(counts, held)
        if not counts:
            print("nothing to raise: the live sessions already hold the default composition")
            return 0
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


def _leaders(ledger, sessions, listed: set) -> list[str]:
    """The person's own session leading the fleet: it holds the orchestrator post without a seat row, so the rows do
    not list it (worldcore field test W15)."""
    import datetime as dt
    from flotilla.fleet import lead
    from flotilla.ledger import project
    from flotilla.posts import post_for_session
    waiting = lead.pending(LocalLogStore(paths.state_dir() / "fleet"), now=dt.datetime.now(dt.timezone.utc))
    lines = []
    for item in project.members(sessions, ledger.rows(), project.roots(ledger.root)):
        if not item.name or item.name in listed:
            continue
        try:
            post = post_for_session(ledger.posts, item.name)
        except PostError:
            post = None
        coming = waiting.get(item.session_id, "")
        if (post is not None and post.name == "orchestrator") or coming:
            later = f"; named `{coming}` with the person's next message" if coming and coming != item.name else ""
            whose = "the person's own session" if item.kind == "interactive" else "a session with no seat row"
            lines.append(f"{item.name} (orchestrator) leads the fleet: {whose}, in the main checkout{later}")
    return lines


def _fleet(ledger, args) -> int:
    if getattr(args, "action", None) == "down":
        return _down(ledger)
    if getattr(args, "action", None) == "clean":
        return _clean(ledger, act=args.yes)
    try:
        sessions = census()
    except CensusUnavailable as err:
        sessions = None
        print(f"census: unknown ({err}); liveness is not asserted")
    view = retire.fleet_view(ledger, sessions)
    leaders = _leaders(ledger, sessions, {item["name"] for item in view}) if sessions is not None else []
    for line in leaders:
        print(line)
    if not view and not leaders:
        print("no post rows: nobody was spawned, or everyone was retired")
    for item in view:
        if item["stranger"]:
            stop = f"; `claude stop {item['short_id']}` stops it" if item["short_id"] else ""
            print(f"{item['name']}  {strangers.label(item['stranger'], item.get('started', ''))}{stop}")
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


def _clean(ledger, *, act: bool) -> int:
    """Trees and branches whose work is surely on trunk go; everything else stays, with why (decision 206)."""
    from flotilla.fleet import cleanup
    try:
        sessions = census()
    except CensusUnavailable as err:
        raise MoveRefused(f"cleaning needs the census to see which trees a live session works in ({err}); "
                          "nothing was removed") from err
    for line in cleanup.sweep(ledger, sessions=sessions, act=act):
        print(line)
    if not act:
        print("plan only: nothing was changed; `flotilla fleet clean --yes` removes what it marks `would`")
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
    if any("its tree is kept" in line for line in lines):
        print("some trees were kept; `flotilla fleet clean` names why, and removes them once their work is on trunk")
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


def _helper(ledger, args) -> int:
    from flotilla.fleet import helpers
    from flotilla.ledger.actor import resolve_actor
    me = resolve_actor(ledger.posts, as_name=args.as_name)
    if args.helper_action == "done":
        released, letter = helpers.done(ledger, me, summary=args.summary)
        print(f"released {released.branch}: {me.name} is done")
        print(letter)
        return 0
    raised = helpers.raise_helper(ledger, me, args.branch, task=args.task, census=census,
                                  store=LocalLogStore(paths.state_dir() / "fleet"),
                                  caller=f"helper raise by {me.name}", anyway=args.anyway)
    note = f" ({raised.note})" if raised.note else ""
    print(f"raised {raised.seat.name} in {raised.seat.tree} on {raised.seat.branch}{note}")
    print(f"it will finish with `flotilla helper done`; merge {raised.seat.branch} into {args.branch} then, and "
          f"retire it: `flotilla retire \"{raised.seat.name}\"`")
    return 0


def run_fleet_command(args) -> int:
    try:
        ledger = open_ledger(Path(args.root))
        return {"spawn": _spawn, "fleet": _fleet, "retire": _retire, "helper": _helper}[args.command](ledger, args)
    except (MoveRefused, PostError, CensusUnavailable, launch.LaunchError, config.ConfigError,
            repo.NotARepository, StorageCorrupt) as err:
        print(f"refused: {err}")
        return 2
