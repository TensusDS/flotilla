"""`flotilla watch --once` and `flotilla watch --wait <seconds>`: what the fleet needs attention for.

`--once` is for a person or any scheduler. `--wait` is for the orchestrator's one background command: it blocks
until an attention item appears that was not there when it started, so a question, a dropped ball or a break wakes
the orchestrator, while what it already saw does not (field test F23).

Exit codes: 0 nothing needs attention (with `--wait`: nothing new before the time ran out), 1 something does, 2 the
census or the ledger could not be asked (printed, never read as "nothing"). v1 has no schedule of its own
(spec, section 8).
"""

from __future__ import annotations

import datetime as dt
import time
from pathlib import Path


def run_watch_command(args, *, gather=None, now: dt.datetime | None = None, sleep=time.sleep,
                      clock=time.monotonic) -> int:
    wait = getattr(args, "wait", 0.0) or 0.0
    if not args.once and not wait:
        print("refused: flotilla has no schedule of its own; run `flotilla watch --once` from cron, launchd or a "
              "loop, or keep `flotilla watch --wait <seconds>` running in the background")
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
    problems = _problems(ctx)
    if problems:
        print("\n".join(problems))
        return 2
    confirm = getattr(args, "confirm", 5.0)
    if wait:
        return _wait(root, gather, ctx, wait, getattr(args, "interval", 20.0) or 20.0, now, sleep, clock, confirm)
    items = _confirmed(ctx.fleet(), root, gather, confirm, sleep)
    if items is None:
        print("census: could not be asked for the second sample")
        return 2
    print(f"census: {len(ctx.live)} live session(s)")
    if not items:
        print("attention: none")
        return 0
    print("attention:")
    print("\n".join(render.lines(items, now or dt.datetime.now(dt.timezone.utc), limit=10_000)))
    return 1


def _key(item) -> tuple:
    """The same item across polls. The text is not it: a dropped ball's text carries the census word, which
    flickers between polls (field test F10)."""
    return (item.kind, item.branch, item.who or item.text)


def _problems(ctx) -> list[str]:
    problems = []
    if ctx.sessions is None:
        problems.append(f"census: could not be asked ({ctx.census_error})")
    if ctx.ledger is None:
        problems.append(f"ledger: could not be read ({ctx.ledger_error})")
    return problems


def _confirmed(items, root, gather, confirm, sleep):
    """Keep a dropped ball only if a second census sample, `confirm` seconds on, still shows it: a session caught
    once at `waiting` is often busy a moment later (field test F10). None when the second sample failed."""
    from flotilla.watch.fleet import DROPPED
    if not any(item.kind == DROPPED for item in items):
        return items
    sleep(confirm)
    again = gather(root, "")
    if _problems(again):
        return None
    still = {_key(item) for item in again.fleet()}
    return [item for item in items if item.kind != DROPPED or _key(item) in still]


def _wait(root, gather, ctx, seconds, interval, now, sleep, clock, confirm=5.0) -> int:
    """Block until an attention item appears that was not there at the start. What was there does not wake it,
    so a standing item cannot turn the wait into a loop."""
    from flotilla.watch import render
    seen = {_key(item) for item in ctx.fleet()}
    deadline = clock() + seconds
    while True:
        left = deadline - clock()   # one reading per turn: the tests drive the clock one step per call
        if left <= 0:
            break
        sleep(min(interval, left))
        ctx = gather(root, "")
        problems = _problems(ctx)
        if problems:
            print("\n".join(problems))
            return 2
        items = ctx.fleet()
        new = [item for item in items if _key(item) not in seen]
        seen = {_key(item) for item in items}   # what went away and came back is news again
        new = _confirmed(new, root, gather, confirm, sleep) if new else new
        if new is None:
            print("census: could not be asked for the second sample")
            return 2
        if new:
            print(f"census: {len(ctx.live)} live session(s)")
            print("attention (new):")
            print("\n".join(render.lines(new, now or dt.datetime.now(dt.timezone.utc), limit=10_000)))
            return 1
    print(f"attention: nothing new in {seconds:g} s")
    return 0
