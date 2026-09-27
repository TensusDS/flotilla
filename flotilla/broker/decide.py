"""The broker's decision for one `PermissionRequest` (spec, section 8; decisions log entries 40 and 70).

Only a background session in a project whose sessions run in `ask` mode is brokered; an interactive session keeps
its dialog, and so does one the census cannot place. The question goes to the queue and the hook waits for the
person's answer through the orchestrator. It waits at most `wait_seconds`, which stays under the hook's own
timeout of 600 s, because a hook that is killed at its timeout leaves the session hanging on a prompt nobody sees
(measured 2026-09-27): the hook withdraws the question and denies with a reason instead.
"""

from __future__ import annotations

import time

from flotilla.broker import queue

DEFAULT_WAIT, LEAST_WAIT, MOST_WAIT = 540, 30, 590
POLL = 0.5


def enabled(profile: dict) -> bool:
    return ((profile.get("permissions") or {}).get("mode") == "ask"
            and (profile.get("broker") or {}).get("enabled") is not False)


def wait_seconds(profile: dict) -> float:
    value = (profile.get("broker") or {}).get("wait_seconds", DEFAULT_WAIT)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        value = DEFAULT_WAIT
    return float(min(max(value, LEAST_WAIT), MOST_WAIT))


def session_rules(tool: str, tool_input: dict, suggestions: list) -> list[dict]:
    """What "allow for this session" applies: Claude Code's own suggestions kept in memory, else an exact rule."""
    rules = [dict(item, destination="session") for item in suggestions or [] if isinstance(item, dict)]
    if rules:
        return rules
    command = (tool_input or {}).get("command")
    if tool == "Bash" and isinstance(command, str) and command:
        return [{"type": "addRules", "rules": [{"toolName": "Bash", "ruleContent": command}], "behavior": "allow",
                 "destination": "session"}]
    return [{"type": "addRules", "rules": [{"toolName": tool}], "behavior": "allow", "destination": "session"}]


def _deny(text: str) -> dict:
    return {"behavior": "deny", "message": f"flotilla broker: {text}"}


def _timed_out(wait: float) -> dict:
    return _deny(f"nobody answered in {int(wait)} s, so this call is refused; ask again later, or record whom you "
                 "wait on (`flotilla work wait`)")


def _decision(got: dict, asked: queue.Question, wait: float) -> dict:
    choice = got.get("choice")
    if choice == queue.ALLOW:
        return {"behavior": "allow"}
    if choice == queue.SESSION:
        return {"behavior": "allow",
                "updatedPermissions": session_rules(asked.tool, asked.tool_input, asked.suggestions)}
    if choice == queue.DENY:
        return _deny(f"the person said no: {got.get('why') or 'no reason given'}")
    return _timed_out(wait)


def decide(payload: dict, ctx, *, clock=time.time, sleep=time.sleep, poll: float = POLL,
           pid: int | None = None) -> dict | None:
    """The decision for this permission request, or None to leave the dialog to decide."""
    if ctx.ledger is None or not enabled(ctx.profile):
        return None
    if ctx.sessions is None or ctx.me is None or ctx.me.kind != "background":
        return None
    me = ctx.me.name
    if ctx.post_of(me) == "orchestrator":
        handle = ctx.me.short_id or ctx.me.session_id[:8]
        return _deny(f"the orchestrator's own question cannot be put to itself; the person answers it with "
                     f"`claude attach {handle}`")
    if not any(s.name and s.name != me and ctx.post_of(s.name) == "orchestrator" for s in ctx.sessions):
        return _deny("no live orchestrator to put this question to; spawn one (`flotilla spawn -o 1`), or give this "
                     "session a rule that allows the call")
    tool_input = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
    suggestions = payload.get("permission_suggestions") if isinstance(payload.get("permission_suggestions"),
                                                                      list) else []
    state, key, wait = ctx.ledger.state_dir, ctx.ledger.repo_key, wait_seconds(ctx.profile)
    asked = queue.ask(state, key, session=me, session_id=ctx.me.session_id, tool=str(payload.get("tool_name") or ""),
                      tool_input=tool_input, suggestions=suggestions, wait=wait, now=clock(), pid=pid)
    while True:
        got = queue.answer_of(state, key, asked.id)
        if got is not None:
            return _decision(got, asked, wait)
        if clock() >= asked.deadline:
            if queue.withdraw(state, key, asked.id, "no answer in time", now=clock()):
                return _timed_out(wait)
            continue   # an answer was written in the same moment: read it
        sleep(poll)
