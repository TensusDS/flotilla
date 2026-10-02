"""The broker's decision for one `PermissionRequest` (spec, section 8; decisions log entries 40 and 70).

Only a background session in a project whose sessions run in `ask` mode is brokered; an interactive session keeps
its dialog, and so does one the census cannot place. The question goes to the queue and the hook waits for the
person's answer through the orchestrator. It waits at most `wait_seconds`, which stays under the hook's own
timeout of 600 s, because a hook that is killed at its timeout leaves the session hanging on a prompt nobody sees
(measured 2026-09-27): the hook withdraws the question and denies with a reason instead.
"""

from __future__ import annotations

import os
import time

from flotilla.broker import queue

DEFAULT_WAIT, LEAST_WAIT, MOST_WAIT = 540, 30, 570
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
    """What "allow for this session" applies, kept in memory: an exact rule, and the directories it needs.

    Claude Code's suggestions are candidates, not one dialog option (hooks reference), so they are not applied
    wholesale: rules and directories are kept, a mode switch never is (entry 70 saw `setMode: acceptEdits` offered
    for a `touch`, which would approve every later edit). A Bash command holding `*` gets no rule at all: in rule
    syntax `*` is a wildcard, so the "exact" rule would allow more than was asked.
    """
    items = [item for item in suggestions or [] if isinstance(item, dict)]
    rules = [dict(item, destination="session") for item in items if item.get("type") == "addRules"]
    if not rules:
        command = (tool_input or {}).get("command")
        if tool == "Bash":
            if not isinstance(command, str) or not command or "*" in command:
                return []
            rules = [{"type": "addRules", "rules": [{"toolName": "Bash", "ruleContent": command}],
                      "behavior": "allow", "destination": "session"}]
        else:
            rules = [{"type": "addRules", "rules": [{"toolName": tool}], "behavior": "allow",
                      "destination": "session"}]
    return rules + [dict(item, destination="session") for item in items if item.get("type") == "addDirectories"]


def _deny(text: str) -> dict:
    return {"behavior": "deny", "message": f"flotilla broker: {text}"}


def _timed_out(wait: float) -> dict:
    return _deny(f"nobody answered in {int(wait)} s, so this call is refused; ask again later, or record whom you "
                 "wait on (`flotilla work wait`)")


def _decision(got: dict, asked: queue.Question, wait: float) -> dict:
    choice = got.get("choice")
    if choice in (queue.ALLOW, queue.SESSION) and got.get("mark") != queue.mark_of(asked):
        return _deny("the answer was given for another question than this call (the question on disk no longer "
                     "matches it), so this call is refused; ask again")
    if choice == queue.ALLOW:
        return {"behavior": "allow"}
    if choice == queue.SESSION:
        rules = session_rules(asked.tool, asked.tool_input, asked.suggestions)
        return {"behavior": "allow", "updatedPermissions": rules} if rules else {"behavior": "allow"}
    if choice == queue.DENY:
        return _deny(f"the person said no: {got.get('why') or 'no reason given'}")
    return _timed_out(wait)


#: flotilla's own subcommands a seat runs without asking the person: reading the fleet, and ledger moves flotilla
#: itself checks - post, evidence, revision (worldcore field test W16). A second word listed with a subcommand is
#: refused even so: standing the fleet down, a long run of any command, the person's approval.
OWN_SUBCOMMANDS = {"status": (), "fleet": ("down",), "watch": (), "brief": (), "metrics": (), "doctor": (),
                   "lane": ("run",), "work": ("approve",), "tree": (), "receipt": (), "helper": ()}
#: Any of these in the command line, quoted or not, and it is not passed: what the shell would read as another
#: command, a redirect or a substitution.
SHELL_SIGNS = set(";&|<>$`\\\n(){}*?!~")


def own_command(payload: dict) -> bool:
    """Whether a Bash call is one of flotilla's own safe commands: the plugin's command line by its real path, one
    command, no assignment, redirect or substitution, a listed subcommand (decision 199)."""
    import shlex
    from flotilla.hooks import CLI
    tool_input = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
    command = tool_input.get("command") if payload.get("tool_name") == "Bash" else None
    if not isinstance(command, str) or SHELL_SIGNS & set(command):
        return False
    try:
        words = shlex.split(command)
    except ValueError:
        return False
    if len(words) < 2 or words[0] != str(CLI):
        return False
    refused = OWN_SUBCOMMANDS.get(words[1])
    if refused is None:
        return False
    return not any(word in refused for word in words[2:])   # anywhere: an option's value would hide the word


def _leads(ctx) -> set:
    from flotilla.core.storage import LocalLogStore
    from flotilla.fleet import lead
    try:
        return set(lead.pending(LocalLogStore(ctx.ledger.state_dir / "fleet")))
    except Exception:  # noqa: BLE001 - an unreadable record of leads only hides one, it never invents one
        return set()


def decide(payload: dict, ctx, *, clock=time.time, sleep=time.sleep, timer=time.monotonic, poll: float = POLL,
           started: float | None = None, parent=os.getppid, pid: int | None = None) -> dict | None:
    """The decision for this permission request, or None to leave the dialog to decide.

    The budget counts on `timer` (monotonic) from `started`, the hook's own start, because everything before the
    question — interpreter start, the census, reading the rules — spends the same timeout. Once the session is known
    to be a background one, every failure is a deny with its reason: no decision there is the measured hang.
    """
    started = timer() if started is None else started
    if ctx.sessions is None or ctx.me is None or ctx.me.kind != "background":
        return None   # a person may be in front of it, or who asks is unknown: the dialog decides
    if ctx.ledger is None:
        return _deny(f"the project's rules could not be read ({ctx.ledger_error}), so this question cannot be put "
                     "to anyone")
    if not enabled(ctx.profile):
        return None
    if own_command(payload):   # nothing a person's yes would add: flotilla checks each of these itself (W16)
        return {"behavior": "allow"}
    me = ctx.me.name
    if ctx.post_of(me) == "orchestrator":
        handle = ctx.me.short_id or ctx.me.session_id[:8]
        return _deny(f"the orchestrator's own question cannot be put to itself; the person answers it with "
                     f"`claude attach {handle}`")
    mine = ctx.project if ctx.project is not None else ctx.sessions   # an orchestrator of another project never reads
    leading = _leads(ctx)   # the person's own session leads before its name shows (worldcore field test W13)
    if not any(s.name and s.name != me and (ctx.post_of(s.name) == "orchestrator" or s.session_id in leading)
               for s in mine):
        return _deny("no live orchestrator to put this question to; spawn one (`flotilla spawn -o 1`), or give this "
                     "session a rule that allows the call")
    try:
        return _wait(payload, ctx, clock=clock, sleep=sleep, timer=timer, poll=poll, started=started, parent=parent,
                     pid=pid)
    except Exception as err:  # noqa: BLE001 - a background session with no decision hangs (entry 70)
        return _deny(f"the broker failed ({err}), so this call is refused")


def _wait(payload, ctx, *, clock, sleep, timer, poll, started, parent, pid) -> dict:
    tool_input = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
    suggestions = payload.get("permission_suggestions") if isinstance(payload.get("permission_suggestions"),
                                                                      list) else []
    state, key, wait = ctx.ledger.state_dir, ctx.ledger.repo_key, wait_seconds(ctx.profile)
    ends = started + wait
    home = parent()
    asked = queue.ask(state, ctx.ledger.repo_key, session=ctx.me.name, session_id=ctx.me.session_id,
                      tool=str(payload.get("tool_name") or ""), tool_input=tool_input, suggestions=suggestions,
                      wait=max(0.0, ends - timer()), now=clock(), pid=pid,
                      cwd=str(payload.get("cwd") or ctx.me.cwd or ""))
    while True:
        got = queue.answer_of(state, key, asked.id)
        if got is not None:
            return _decision(got, asked, wait)
        if parent() != home:
            queue.withdraw(state, key, asked.id, "the asking session is gone", now=clock())
            return _deny("the asking session is gone")
        if timer() >= ends:
            if queue.withdraw(state, key, asked.id, "no answer in time", now=clock()):
                return _timed_out(wait)
            got = queue.answer_of(state, key, asked.id)   # an answer was written in the same moment: read it
            if got is not None:
                return _decision(got, asked, wait)
            if timer() >= ends + 2:
                return _timed_out(wait)   # an answer that cannot be read must not keep the hook past its timeout
        sleep(poll)
