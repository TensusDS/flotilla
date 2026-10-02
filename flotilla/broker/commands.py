"""`flotilla permit list | next | answer`: the orchestrator's side of the permission broker.

Only the orchestrator, or a person in a terminal outside any session, may answer: the answer command is the
person's gate, so a worker session that runs it for a peer is refused by the census, not trusted by its word.
"""

from __future__ import annotations

import time
from pathlib import Path

POLL = 1.0


def _caller(root) -> tuple[str | None, str]:
    """The session running this command and its post; (None, "") outside any session the census lists."""
    from flotilla.core import platform as plat
    from flotilla.core.census import read_census
    from flotilla.core.identity import find_calling_session
    from flotilla.posts import PostError, load_posts, post_for_session
    sessions = read_census(timeout=10)
    source = plat.probe().parent_pid_source
    found = find_calling_session(sessions, parent_of=lambda pid: plat.parent_pid(pid, source))
    if found is None or not found.name:
        return None, ""
    try:
        from flotilla.ledger.commands import trunk_rules
        posts = trunk_rules(Path(root)).posts
    except Exception:  # noqa: BLE001 - before onboarding reaches trunk the tree's posts are the ones there are
        posts = load_posts(Path(root))
    try:
        post = post_for_session(posts, found.name)
    except PostError:
        post = None
    if post is None:   # the person's own session leads before its name shows (worldcore field test W13)
        from flotilla.core import paths
        from flotilla.core.storage import LocalLogStore
        from flotilla.fleet import lead
        import datetime as dt
        if found.session_id in lead.pending(LocalLogStore(paths.state_dir() / "fleet"),
                                            now=dt.datetime.now(dt.timezone.utc)):
            return found.name, "orchestrator"
    return found.name, post.name if post else ""


def run_permit_command(args, *, clock=time.time, sleep=time.sleep, caller=None) -> int:
    from flotilla.broker import present, queue
    from flotilla.core.text import visible
    from flotilla.core import config, paths, repo
    root = config.find_project(Path(args.root))
    if root is None:
        print(f"refused: not onboarded: no .flotilla/project.toml at or above {Path(args.root).resolve()}")
        return 2
    state, key = paths.state_dir(), repo.identify(root).key
    if args.action == "answer":
        try:
            name, post = (caller or _caller)(root)
        except Exception as err:  # noqa: BLE001 - who answers is unknown: not the gate's to guess
            print(f"refused: could not tell who answers ({err}); answer from the orchestrator's session, or from a "
                  "terminal outside any session")
            return 2
        if name and post != "orchestrator":
            print(f"refused: `{name}` holds {('the ' + post + ' post') if post else 'no post'}; only the "
                  "orchestrator, or a person outside any session, answers a permission question")
            return 2
        if not name:   # no fleet session: a person's own session, or a terminal - never a detached process
            from flotilla.core import caller as who
            refused = who.person_refusal("answers a permission question")
            if refused:
                print(f"refused: {refused}")
                return 2
        try:
            asked = queue.answer(state, key, args.id, args.choice, why=args.why or "", mark=args.mark or "",
                                 now=clock())
        except queue.QueueRefused as err:
            print(f"refused: {err}")
            return 2
        print(f"answered {args.id}: {args.choice}; {visible(asked.session)} is told at once")
        return 0
    if args.action == "list":
        waiting = queue.live(state, key, now=clock())
        if not waiting:
            print("no question waits")
        for asked in waiting:
            print(f"{asked.id}  {visible(asked.session)}: {present.summary(asked)} ({int(clock() - asked.at)} s)")
        return 0
    started = clock()
    while True:
        waiting = queue.live(state, key, now=clock())
        if waiting:
            print("\n".join(present.describe(waiting[0], clock())))
            if len(waiting) > 1:
                print(f"{len(waiting) - 1} more wait behind it, oldest first")
            return 0
        if clock() - started >= args.wait:
            print("no question waits")
            return 3
        sleep(POLL)
