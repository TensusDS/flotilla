"""`flotilla watch --once`: what the fleet needs attention for, for a person or for any scheduler.

Exit codes: 0 nothing needs attention, 1 something does, 2 the census or the ledger could not be asked (printed,
never read as "nothing"). v1 has no schedule of its own (spec, section 8).
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path


def run_watch_command(args, *, gather=None, now: dt.datetime | None = None) -> int:
    if not args.once:
        print("refused: flotilla has no schedule of its own; run `flotilla watch --once` from cron, launchd or a loop")
        return 2
    from flotilla.core.config import find_project
    root = find_project(Path(args.root))
    if root is None:
        print(f"refused: not onboarded: no .flotilla/project.toml at or above {Path(args.root).resolve()}")
        return 2
    if gather is None:
        from flotilla.watch.context import gather
    from flotilla.watch import render
    ctx = gather(root, "")
    problems = []
    if ctx.sessions is None:
        problems.append(f"census: could not be asked ({ctx.census_error})")
    if ctx.ledger is None:
        problems.append(f"ledger: could not be read ({ctx.ledger_error})")
    if problems:
        print("\n".join(problems))
        return 2
    print(f"census: {len(ctx.live)} live session(s)")
    items = ctx.fleet()
    if not items:
        print("attention: none")
        return 0
    print("attention:")
    print("\n".join(render.lines(items, now or dt.datetime.now(dt.timezone.utc), limit=10_000)))
    return 1
