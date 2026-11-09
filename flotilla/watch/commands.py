"""`flotilla watch --once` and `flotilla watch --wait <seconds>`: what the fleet needs attention for.

`--once` is for a person or any scheduler. `--wait` is for the orchestrator's one background command: it blocks
until an attention item appears that was not there when it started, so a question, a dropped ball or a break wakes
the orchestrator, while what it already saw does not (field test F23).

Exit codes: `--once` 0 nothing needs attention, 1 something does; `--wait` 0 whether it woke on news ("attention
(new):") or the time ran out ("nothing new") - a wake is not a failure, and Claude Code showed every one as failed
(alpha, 0.7.16); either 2 when the census or the ledger could not be asked (printed, never read as "nothing") - a
`--wait` asks a census that missed a look after its first again, and ends with 2 after CENSUS_MISSES in a row or
when its last looks missed. v1 has no schedule of its own
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
        from flotilla.watch import context
        gather = lambda root, sid: context.gather(root, sid, census_timeout=context.COMMAND_CENSUS_TIMEOUT,  # noqa: E731
                                                  held_seats=True)
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
    from flotilla.watch import inbox, inbox_command, shown
    entries = inbox.collect(ctx, fleet_items=items)
    if not items and not entries:
        print("attention: none")
        return 0
    moment = now or dt.datetime.now(dt.timezone.utc)
    print("attention:")
    if entries:   # the person's queue as `flotilla inbox` counts it, never one kind of item (final review, I5)
        first = shown.load(ctx.ledger.state_dir, ctx.ledger.repo_key)["first"]
        print(f"waits on the person ({len(entries)}):")
        print("\n".join(line for line in inbox_command.listing(entries, first, moment)[1:]))
    rest = [item for item in items if not inbox.covers(item)]
    if rest:
        print("\n".join(render.sections(rest, moment)))
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


def _confirmed(items, root, gather, confirm, sleep, problems=None):
    """Keep a dropped ball only if a second census sample, `confirm` seconds on, still shows it: a session caught
    once at `waiting` is often busy a moment later (field test F10). None when the second sample failed; its
    context goes into `problems`, when given, so the caller can tell a missed census from an unreadable ledger."""
    from flotilla.watch.fleet import DROPPED
    if not any(item.kind == DROPPED for item in items):
        return items
    sleep(confirm)
    again = gather(root, "")
    if _problems(again):
        if problems is not None:
            problems.append(again)
        return None
    still = {_key(item) for item in again.fleet()}
    return [item for item in items if item.kind != DROPPED or _key(item) in still]


#: Looks in a row the census may miss before a wait gives up: under load `claude agents` answers late, and one miss
#: must not blind the orchestrator's watch (field test of 2026-11-08, item 3).
CENSUS_MISSES = 3


def _wait(root, gather, ctx, seconds, interval, now, sleep, clock, confirm=5.0) -> int:
    """Block until an attention item appears that was not there at the start. What was there does not wake it,
    so a standing item cannot turn the wait into a loop. A census that misses a look is asked again at the next one;
    CENSUS_MISSES in a row, or a ledger that cannot be read, end the wait with 2."""
    from flotilla.watch import render
    seen = {_key(item) for item in ctx.fleet()}
    deadline = clock() + seconds
    misses, last = 0, []
    while True:
        left = deadline - clock()   # one reading per turn: the tests drive the clock one step per call
        if left <= 0:
            break
        sleep(min(interval, left))
        ctx = gather(root, "")
        problems = _problems(ctx)
        if problems and (ctx.ledger is None or misses + 1 >= CENSUS_MISSES):
            print("\n".join(problems))
            return 2
        if problems:
            misses, last = misses + 1, problems
            continue
        items = ctx.fleet()
        new = [item for item in items if _key(item) not in seen]
        failed: list = []
        confirmed = _confirmed(new, root, gather, confirm, sleep, failed) if new else new
        if confirmed is None:   # the second sample missed: nothing is news yet, and it is asked again next look
            missed = _problems(failed[0]) if failed else ["census: could not be asked for the second sample"]
            if (failed and failed[0].ledger is None) or misses + 1 >= CENSUS_MISSES:
                print("\n".join(missed))
                return 2
            misses, last = misses + 1, missed
            continue
        misses = 0
        seen = {_key(item) for item in items}   # what went away and came back is news again
        # a ball the second sample cleared was not seen: when it really drops at the next poll, it is news
        seen -= {_key(item) for item in new} - {_key(item) for item in confirmed}
        new = confirmed
        if new:
            print(f"census: {len(ctx.live)} live session(s)")
            print("attention (new):")
            print("\n".join(render.lines(new, now or dt.datetime.now(dt.timezone.utc), limit=10_000)))
            return 0   # the news is in the output; a wake is not a failure (alpha orchestrator, 0.7.16)
    if misses:   # its last looks saw nothing at all: that is "could not ask", never "nothing new" (review of 406a533)
        print("\n".join(last))
        return 2
    print(f"attention: nothing new in {seconds:g} s")
    return 0
