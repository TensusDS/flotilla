"""The Stop guard: a background session does not fall silent while it holds a move (spec, section 6.9 layer 2).

It blocks only when it knows all of it: a background session (an interactive one has a person in front of it), a
move it holds with no recorded wait, and nothing in flight that will wake it (`background_tasks` and
`session_crons` both present and empty; absent means the registry was unreachable). Anything it could not ask lets
the session stop: a guard that cannot ask must not trap a session. A second stop in a row is let through and
returned as a break, so the orchestrator hears of it.
"""

from __future__ import annotations

from dataclasses import dataclass

from flotilla.watch.whose import HELD_BY_ME


@dataclass(frozen=True)
class Verdict:
    block: bool = False
    reason: str = ""
    breaks: tuple = ()


def stop_verdict(ctx, payload: dict, cli: str = "flotilla") -> Verdict:
    if ctx.me is None or ctx.ledger is None or ctx.me.kind != "background":
        return Verdict()
    if (ctx.profile.get("watch") or {}).get("stop_guard") is False:
        return Verdict()
    tasks, crons = payload.get("background_tasks"), payload.get("session_crons")
    if not isinstance(tasks, list) or not isinstance(crons, list) or tasks or crons:
        return Verdict()
    held = [item for item in ctx.mine() if item.kind in HELD_BY_ME]
    if not held:
        return Verdict()
    if payload.get("stop_hook_active"):
        return Verdict(breaks=tuple(sorted({item.branch for item in held})))
    lines = [f"flotilla: you hold {len(held)} move(s), nothing in flight will wake you, and no wait is recorded. "
             "Stopping now leaves the work standing with nobody on it:"]
    lines += [f"  {item.branch}: {item.text}" for item in held]
    lines.append(f'Make the move, or record whom you wait on: {cli} work wait <branch> --on "<whom>" --why "<why>"')
    return Verdict(block=True, reason="\n".join(lines))
