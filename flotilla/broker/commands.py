"""`flotilla permit list | next | answer`: the orchestrator's side of the permission broker."""

from __future__ import annotations

import time
from pathlib import Path

POLL = 1.0


def run_permit_command(args, *, clock=time.time, sleep=time.sleep) -> int:
    from flotilla.broker import present, queue
    from flotilla.core import config, paths, repo
    root = config.find_project(Path(args.root))
    if root is None:
        print(f"refused: not onboarded: no .flotilla/project.toml at or above {Path(args.root).resolve()}")
        return 2
    state, key = paths.state_dir(), repo.identify(root).key
    if args.action == "answer":
        try:
            asked = queue.answer(state, key, args.id, args.choice, why=args.why or "", now=clock())
        except queue.QueueRefused as err:
            print(f"refused: {err}")
            return 2
        print(f"answered {args.id}: {args.choice}; {asked.session} is told at once")
        return 0
    if args.action == "list":
        waiting = queue.live(state, key, now=clock())
        if not waiting:
            print("no question waits")
        for asked in waiting:
            print(f"{asked.id}  {asked.session}: {present.summary(asked)} ({int(clock() - asked.at)} s)")
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
