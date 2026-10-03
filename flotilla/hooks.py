"""Entry point for Claude Code hooks.

Installed is not active: until the project holds `.flotilla/project.toml`, every hook exits 0 with no output. That
path imports only the standard library and `flotilla.core.config`, because a project that never onboarded flotilla
must not pay for it. A hook never crashes or traps a session: what it cannot check it says, and the Stop guard does
not block on what it could not ask.
"""

from __future__ import annotations

import datetime as dt
import json
import sys
import time
from pathlib import Path

#: When this process started: the permission hook's budget counts from here, not from its question.
STARTED = time.monotonic()

#: Seconds per external call inside a hook. The calls plus a margin must fit inside the timeouts declared in
#: hooks/hooks.json, or Claude Code kills the hook before it can say "unknown".
HOOK_CHECK_TIMEOUT = 3
EVENTS = ("session-start", "prompt", "stop", "guard", "permission", "ask")
CLI = Path(__file__).resolve().parent.parent / "bin" / "flotilla"
PEERS_SHOWN = 8
#: A Bash command naming none of these reaches no guard, so the hook answers before looking anything up. The last
#: group lets a long run reach the lane guard: the lane's default run patterns and the launchers that run them. A
#: tier whose program is none of these (`make test`, `./run-tests.sh`) is not warned about unless the line names one.
#: `flotilla` lets the person guard see its CLI however the word `approve` is quoted.
GUARD_TRIGGERS = ("checkout", "restore", "reset", "clean", "sed", "push", "gh", "approve", "flotilla",
                  "pytest", "py.test", "playwright", "vitest", "jest", "cargo", "go test", "npm", "pnpm", "yarn",
                  "npx", "bun", "tox", "nox")


def run_hook(event: str, stdin, out=sys.stdout, *, gather=None, now: dt.datetime | None = None) -> int:
    try:
        payload = json.load(stdin)
    except ValueError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    cwd = Path(payload.get("cwd") or ".")
    if event == "guard":
        tool_input = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
        command = tool_input.get("command") if isinstance(tool_input.get("command"), str) else ""
        if not any(word in command for word in GUARD_TRIGGERS):
            return 0

    from flotilla.core.config import find_project
    root = find_project(cwd)
    if root is None:
        if event == "guard":   # outside every project, a door into one is still that project's to judge (F18)
            from flotilla.guards.run import guard_hook
            return guard_hook(command, cwd, None, out, mode=str(payload.get("permission_mode") or ""),
                              session_id=str(payload.get("session_id") or ""), tool_input=payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else None)
        return 0
    _trace(event, payload, root, now)
    if event == "guard":
        from flotilla.guards.run import guard_hook
        return guard_hook(command, cwd, root, out, mode=str(payload.get("permission_mode") or ""),
                              session_id=str(payload.get("session_id") or ""), tool_input=payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else None)

    try:
        if gather is None:
            from flotilla.watch.context import gather as gather_context
        else:
            gather_context = gather
        ctx = gather_context(root, str(payload.get("session_id") or ""))
        handler = {"session-start": _session_start, "prompt": _prompt, "stop": _stop, "permission": _permission,
                   "ask": _ask}[event]
        return handler(ctx, payload, out, now or dt.datetime.now(dt.timezone.utc))
    except Exception as err:  # noqa: BLE001 - a hook must say what broke, never crash the session
        if event in ("stop", "permission", "ask"):   # their stdout is a decision in JSON, or nothing
            print(f"flotilla: the {event} hook failed and gives no decision: {err}", file=sys.stderr)
        else:
            print(f"flotilla: the {event} hook failed: {err}", file=out)
        return 0


def _trace(event: str, payload: dict, root: Path, now: dt.datetime | None) -> None:
    """Record that this hook ran for the session (F11); the guard reaches here only when a command named a guarded
    program, so its fast path stays free of writes."""
    try:
        from flotilla.core import paths
        from flotilla.watch import fired
        at = (now or dt.datetime.now(dt.timezone.utc)).isoformat(timespec="seconds")
        fired.record(paths.state_dir(), str(payload.get("session_id") or ""), event, root=root, at=at)
    except Exception:  # noqa: BLE001 - bookkeeping never costs a session its hook
        return


def _identity(ctx) -> list[str]:
    said = []
    if ctx.sessions is None:
        said.append(f"the census could not be asked ({ctx.census_error}); who you are and who is alive is unknown")
    elif ctx.me is None:
        said.append("this session is not in the census; a move from here needs --as <session name>")
    else:
        post = ctx.post(ctx.me.name)
        role = f"post {post.name}" if post else "no post matches this name, so it can make no ledger move"
        mine = ctx.project_live or set()
        peers = sorted(mine - {ctx.me.name})
        elsewhere = len((ctx.live or set()) - mine - {ctx.me.name})
        shown = ", ".join(peers[:PEERS_SHOWN])
        if len(peers) > PEERS_SHOWN:
            shown += f", and {len(peers) - PEERS_SHOWN} more"
        said.append(f"you are {ctx.me.name} ({role}); {len(peers)} live peer(s) in this project"
                    + (f": {shown}" if peers else "")
                    + (f"; {elsewhere} more elsewhere on this machine" if elsewhere else ""))
    if ctx.ledger is None:
        said.append(f"the ledger could not be read: {ctx.ledger_error}")
    return said


def _event_problems(ctx) -> list[str]:
    if ctx.ledger is None or not ctx.ledger.events:
        return []
    from flotilla.ledger import events
    found = events.check(ctx.ledger.events, cwd=ctx.ledger.root, samples=False)
    return [f"event script {name}: {status}: {text}; run `flotilla events check`"
            for name, status, text in found if status != events.OK]


def _session_start(ctx, payload, out, now) -> int:
    from flotilla.core import paths
    from flotilla.guards.githooks import refresh_link
    refresh_link(paths.state_dir(), CLI)
    from flotilla import doctor
    from flotilla.watch import render
    try:
        checks = doctor.render(doctor.collect(cwd=ctx.root, timeout=HOOK_CHECK_TIMEOUT, setup=False), quiet=True)
    except Exception as err:  # noqa: BLE001 - say what could not be checked, go on
        checks = [f"could not check this project: {err}"]
    if checks:
        print("flotilla: " + "; ".join(checks), file=out)
    for line in _identity(ctx) + _event_problems(ctx):
        print(f"flotilla: {line}", file=out)
    mine = ctx.mine()
    if mine:
        print("flotilla - inherited, and whose move it is:", file=out)
        print("\n".join(render.lines(mine, now)), file=out)
    fleet_items = ctx.fleet() if ctx.is_orchestrator else []
    if fleet_items:
        print("flotilla - the fleet (you are the orchestrator):", file=out)
        print("\n".join(render.lines(fleet_items, now)), file=out)
    return 0


def _prompt(ctx, payload, out, now) -> int:
    from flotilla.core import paths
    from flotilla.core.storage import LocalLogStore
    from flotilla.fleet import lead
    from flotilla.watch import render, throttle
    from flotilla.watch.whose import WAITING
    blocks, keys = [], []
    current = (ctx.me.name or "") if getattr(ctx, "me", None) is not None else None
    title = lead.due(LocalLogStore(paths.state_dir() / "fleet"), str(payload.get("session_id") or ""),
                     current=current, now=now)
    if ctx.sessions is None:
        blocks.append(f"flotilla: could not ask which session this is: {ctx.census_error}")
    elif ctx.ledger is None:
        blocks.append(f"flotilla: the ledger could not be read: {ctx.ledger_error}")
    elif ctx.me is not None:
        mine = [item for item in ctx.mine() if item.kind != WAITING]
        fleet_items = ctx.fleet() if ctx.is_orchestrator else []
        if mine:
            blocks += ["flotilla - your move:", *render.lines(mine, now)]
        if fleet_items:
            blocks += ["flotilla - the fleet (you are the orchestrator):", *render.lines(fleet_items, now)]
        keys = [f"{item.kind}|{item.branch}|{item.text}" for item in mine + fleet_items]
    keys = keys or blocks
    state = ctx.ledger.state_dir if ctx.ledger is not None else paths.state_dir()
    if title:   # the person's own session leads the fleet: it takes the orchestrator's name now (W9)
        unseen = lead.offered(LocalLogStore(paths.state_dir() / "fleet"),
                              str(payload.get("session_id"))) > lead.ASK_AFTER
        note = (f"flotilla: this session {'is to be' if unseen else 'is now'} `{title}` and holds the orchestrator "
                "post; follow .flotilla/posts/orchestrator.md on trunk and use the flotilla:flotilla skill for every "
                "ledger move. If the rest of the fleet is not raised yet, run `flotilla spawn --fill` first - the "
                "seats learn this name.")
        if unseen:
            note += (f"\nClaude Code has not shown this name after {lead.ASK_AFTER} messages - ask the person to type "
                     f"`/rename {title}` and then send any message; `--fill` waits for the name.")
        body = {"hookEventName": "UserPromptSubmit", "sessionTitle": title,
                "additionalContext": "\n".join([note, *blocks])}
        print(json.dumps({"hookSpecificOutput": body}), file=out)
        return 0
    if not throttle.due(state, ctx.session_id, throttle.digest(keys) if keys else "", now):
        return 0
    print("\n".join(blocks), file=out)
    return 0


def _stop(ctx, payload, out, now) -> int:
    from flotilla.ledger.model import now_iso
    from flotilla.watch import fleet, guard
    verdict = guard.stop_verdict(ctx, payload, cli=str(CLI))
    if verdict.block:
        print(json.dumps({"decision": "block", "reason": verdict.reason}), file=out)
    elif verdict.breaks:
        fleet.record_break(ctx.ledger.state_dir, ctx.ledger.repo_key, ctx.me.name, verdict.breaks,
                           at=now_iso(now))
    return 0


def _permission(ctx, payload, out, now) -> int:
    from flotilla.broker.decide import decide
    decision = decide(payload, ctx, started=STARTED)
    if decision is not None:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": decision}}),
              file=out)
    return 0


def _ask(ctx, payload, out, now) -> int:
    from flotilla.watch.ask import ask_verdict
    kind, text = ask_verdict(ctx, cli=str(CLI))
    if kind == "deny":
        body = {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": text}
    elif kind == "note":
        body = {"hookEventName": "PreToolUse", "additionalContext": text}
    else:
        return 0
    print(json.dumps({"hookSpecificOutput": body}), file=out)
    return 0
