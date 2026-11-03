"""`flotilla inbox`: everything that waits on the person, grouped, numbered and aged, with when flotilla first showed
each to them or "never shown" (person inbox design, goal 1).

The person answers by number. Exit codes as `watch --once`: 0 nothing waits, 1 something does, 2 the census or the
ledger could not be asked. A run counts as shown only when it is the person's own (see `toolcall`).
"""

from __future__ import annotations

import datetime as dt
import time
from pathlib import Path

from flotilla.watch import inbox, shown, toolcall
from flotilla.watch.render import age


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def listing(entries, shown_first: dict, now: dt.datetime) -> list[str]:
    counted = inbox.counts(entries)
    head = f"waits on you: {counted['total']}"
    extras = [f"{counted['urgent']} urgent" if counted["urgent"] else "",
              f"{counted['blocker']} holding work up" if counted["blocker"] else ""]
    extras = [part for part in extras if part]
    out = [head + (f" ({', '.join(extras)})" if extras else "") + " - answer by number"]
    number = 0
    for group in inbox.GROUPS:
        mine = [entry for entry in entries if entry.group == group]
        if not mine:
            continue
        out.append(f"{inbox.LABELS[group][0]} ({len(mine)}):")
        for entry in sorted(mine, key=lambda entry: (not entry.urgent, entry.since or "~")):
            number += 1
            first = shown_first.get(entry.key)
            seen = f"first shown {age(first, now)} ago" if first else "never shown"
            asked = f"by {entry.by}, " if entry.by else ""
            when = f"{age(entry.since, now)} ago" if entry.since else "age unknown"
            flag = " [urgent]" if entry.urgent else ""
            where = f"{entry.branch}: " if entry.branch else ""
            out.append(f"  {number}. {where}{entry.why} ({asked}{when}; {seen}){flag}")
    return out


def _persons_run(state) -> bool:
    from flotilla.core import caller
    if caller.person_refusal("reads the inbox as shown to them"):
        return False
    try:
        ids = [session.session_id for session in caller.calling_sessions()]
    except Exception:  # noqa: BLE001 - who calls is unknown: nothing is recorded as shown
        return False
    return not toolcall.by_tool(state, ids, time.time())


def run_inbox_command(args) -> int:
    from flotilla.core import paths
    from flotilla.core.config import find_project
    from flotilla.watch import context
    root = find_project(Path(args.root))
    if root is None:
        print(f"refused: not onboarded: no .flotilla/project.toml at or above {Path(args.root).resolve()}")
        return 2
    ctx = context.gather(root, "")
    problems = [f"census: could not be asked ({ctx.census_error})"] if ctx.sessions is None else []
    if ctx.ledger is None:
        problems.append(f"ledger: could not be read ({ctx.ledger_error})")
    if problems:
        print("\n".join(problems))
        return 2
    entries = inbox.collect(ctx)
    now = _now()
    state, repo = ctx.ledger.state_dir, ctx.ledger.repo_key
    if not entries:
        print("nothing waits on you")
        return 0
    print("\n".join(listing(entries, shown.load(state, repo)["first"], now)))
    if _persons_run(paths.state_dir()):
        shown.mark(state, repo, [entry.key for entry in entries], now.isoformat(timespec="seconds"))
    return 1
