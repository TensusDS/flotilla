"""`flotilla inbox`: everything that waits on the person, grouped, numbered and aged, with when flotilla first showed
each to them or "never shown" (person inbox design, goal 1).

The person answers by number. Exit codes as `watch --once`: 0 nothing waits, 1 something does, 2 the census or the
ledger could not be asked.

A run records "shown" only from a terminal outside every Claude Code session. Measured on Claude Code 2.1.317: a
model's Bash call and the person's `!` run with the same environment and the same parent process, so inside a session
nothing tells the person's run from the model's, and a model's run counted as shown would be the claim this design
refuses. A hook-side note tried and failed two security reviews (a word test, then a time window). The listing prints
either way; the line flotilla puts on the person's screen records "shown" in the person's interactive session.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from flotilla.watch import inbox, shown
from flotilla.core.text import visible
from flotilla.watch.render import _cut, age


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def listing(entries, shown_first: dict, now: dt.datetime) -> list[str]:
    counted = inbox.counts(entries)
    head = f"waits on you: {counted['total']}"
    extras = [f"{counted['urgent']} urgent" if counted["urgent"] else "",
              f"{counted['blocker']} holding work up" if counted["blocker"] else ""]
    extras = [part for part in extras if part]
    out = [head + (f" ({', '.join(extras)})" if extras else "") + " - answer by number"]
    # urgent rows first, whatever they ask (the design's "Decided"), then each group, oldest first
    sections = [("urgent", [entry for entry in entries if entry.urgent])]
    sections += [(inbox.LABELS[group][0], [entry for entry in entries if entry.group == group and not entry.urgent])
                 for group in inbox.GROUPS]
    number = 0
    for heading, mine in sections:
        if not mine:
            continue
        out.append(f"{heading} ({len(mine)}):")
        for entry in sorted(mine, key=lambda entry: entry.since or "~"):
            number += 1
            first = shown_first.get(entry.key)
            seen = f"first shown {age(first, now)} ago" if first else "never shown"
            asked = f"by {entry.by}, " if entry.by else ""
            when = f"{age(entry.since, now)} ago" if entry.since else "age unknown"
            kind = f" [{inbox.LABELS[entry.group][0]}]" if entry.urgent else ""
            where = f"{entry.branch}: " if entry.branch else ""
            # every word here may be another session's - a name `--as` took, a why: one safe line (security review)
            out.append(_cut(visible(f"  {number}. {where}{entry.why} ({asked}{when}; {seen}){kind}")))
    return out


def _persons_run() -> bool:
    """A terminal outside every session: what a person's own shell has, and no session's tool call does."""
    from flotilla.core import caller
    try:
        if caller.calling_sessions():
            return False
    except Exception:  # noqa: BLE001 - who calls is unknown: nothing is recorded as shown
        return False
    return caller.has_terminal()


def run_inbox_command(args) -> int:
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
    if _persons_run():
        shown.mark(state, repo, [entry.key for entry in entries], now.isoformat(timespec="seconds"))
    return 1
