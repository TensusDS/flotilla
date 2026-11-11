"""Entry point for Claude Code hooks.

Installed is not active: until the project holds `.flotilla/project.toml`, every hook exits 0 with no output. That
path imports only the standard library and `flotilla.core.config`, because a project that never onboarded flotilla
must not pay for it. A hook never crashes or traps a session: what it cannot check it says, and the Stop guard does
not block on what it could not ask.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
import time
from pathlib import Path

#: When this process started: the permission hook's budget counts from here, not from its question.
STARTED = time.monotonic()

#: Seconds per external call inside a hook. The calls plus a margin must fit inside the timeouts declared in
#: hooks/hooks.json, or Claude Code kills the hook before it can say "unknown".
HOOK_CHECK_TIMEOUT = 3
EVENTS = ("session-start", "prompt", "stop", "guard", "permission", "ask", "edit")
CLI = Path(__file__).resolve().parent.parent / "bin" / "flotilla"
PEERS_SHOWN = 8
#: A Bash command naming none of these reaches no guard, so the hook answers before looking anything up. The last
#: group lets a long run reach the lane guard: the lane's default run patterns and the launchers that run them. A
#: tier whose program is none of these (`make test`, `./run-tests.sh`) is not warned about unless the line names one.
#: `flotilla` lets the person guard see its CLI however the word `approve` is quoted. `rm`, `worktree`, `branch` and
#: `update-ref` are the gc guard's door words (a test holds `flotilla.guards.gc.DOOR_WORDS` among these).
GUARD_TRIGGERS = ("checkout", "restore", "reset", "clean", "switch", "sed", "push", "gh", "approve", "flotilla",
                  "rm", "worktree", "branch", "update-ref",
                  "pytest", "py.test", "playwright", "vitest", "jest", "cargo", "go test", "npm", "pnpm", "yarn",
                  "npx", "bun", "tox", "nox", "headless")
#: The person's words that are too common as parts of other words (`origin`) to look for as text. As whole words -
#: or as parts, in a line that holds `$` or a backtick, where bash says where a word begins - they let code handed
#: to an interpreter (`bash -c 'x rig open'`) reach the guard that reads it.
GUARD_TRIGGER_PARTS = ("rig", "attention")
GUARD_TRIGGER_WORDS = re.compile(r"\b(?:%s)\b" % "|".join(GUARD_TRIGGER_PARTS))
_QUOTING = {ord(sign): None for sign in "'\"\\"}


def _unquoted(command: str) -> str:
    """`flotilla.guards.unquoted`, here so the common path imports nothing: quotes, backslashes and line
    continuations taken out, so a word split by them (`wor""ktree`) is still found (a test keeps the two equal)."""
    return command.replace("\\\n", "").translate(_QUOTING)


def _names_a_guarded_word(command: str) -> bool:
    text = _unquoted(command)
    if any(word in text for word in GUARD_TRIGGERS):
        return True
    if "$" in text or "`" in text:   # where a word begins is bash's to say once it expands something (`$A'r'ig`)
        return any(word in text for word in GUARD_TRIGGER_PARTS)
    return GUARD_TRIGGER_WORDS.search(text) is not None


def run_hook(event: str, stdin, out=sys.stdout, *, gather=None, now: dt.datetime | None = None) -> int:
    try:
        payload = json.load(stdin)
    except ValueError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    cwd = Path(payload.get("cwd") or ".")
    if event == "edit":   # any project and none - wherever the plugin is enabled (user scope: every session)
        try:
            from flotilla.core import paths as _paths
            from flotilla.guards.files import check as files_check
            tool_input = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
            text = files_check(tool_input, state=_paths.state_dir(), env=os.environ)
        except Exception as err:  # noqa: BLE001 - an edit guard that fails must not stop every edit
            print(f"flotilla: the edit guard failed and decides nothing: {err}", file=sys.stderr)
            return 0
        if text:
            out.write(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                                         "permissionDecisionReason": text}}))
        return 0
    if event == "guard":
        tool_input = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
        command = tool_input.get("command") if isinstance(tool_input.get("command"), str) else ""
        if not _names_a_guarded_word(command):
            return 0
        try:   # an exception here exits non-zero, which Claude Code does not read as a refusal: the push would run
            return _guard(command, cwd, payload, out, now)
        except Exception as err:  # noqa: BLE001 - the guards' own rule for a failure, decided here when they fell
            return _guard_failed(command, err, out)

    from flotilla.core.config import find_project
    root = find_project(cwd)
    if root is None:
        if event == "session-start" and _fresh(payload):   # before onboarding too: the copy may not be the project's
            _copy_note(cwd, out)
        return 0
    _trace(event, payload, root, now)

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


def _fresh(payload: dict) -> bool:
    """A start, not a resume, clear or compact: only then is the hook's cwd where the session started (second review
    of 67cebce..6e5b1b5); a payload without `source` is taken as a start."""
    return payload.get("source", "startup") == "startup"


def _copy_note(cwd: Path, out) -> None:
    """The doctor's plugin-copy line, without the doctor: outside a project the hook imports nothing heavy."""
    try:
        from flotilla.core import claude_state
        found = claude_state.copy_in_use(cwd)
    except Exception as err:  # noqa: BLE001 - a note that cannot be made is no reason to fail a session's start
        print(f"flotilla: could not compare the plugin copy with this project's: {err}", file=out)
        return
    if found is not None and found[0] != "ok":
        status, detail, fix = found
        print(f"flotilla: {status:<5} plugin copy: {detail}" + (f" (fix: {fix})" if fix else ""), file=out)
    _update_note(cwd, out)


def _update_note(root: Path, out) -> None:
    """A pin that holds this machine on an older release, said once a day (spec 2026-11-04-plugin-updates)."""
    try:
        from flotilla.core import updates
        lines = updates.lines(root)
    except Exception as err:  # noqa: BLE001 - a note that cannot be made is no reason to fail a session's start
        lines = [f"could not compare this machine's flotilla with the newest release: {err}"]
    for line in lines:
        print(f"flotilla: {line}", file=out)


def _guard(command: str, cwd: Path, payload: dict, out, now) -> int:
    from flotilla.core.config import find_project
    from flotilla.guards.push import no_rewrites
    from flotilla.guards.run import guard_hook
    no_rewrites()   # every git the guards start reads what a push really sends (review of the scan of 0.7.0, C1)
    root = find_project(cwd)   # outside every project, a door into one is still that project's to judge (F18)
    if root is not None:
        _trace("guard", payload, root, now)
    tool_input = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else None
    return guard_hook(command, cwd, root, out, mode=str(payload.get("permission_mode") or ""),
                      session_id=str(payload.get("session_id") or ""), tool_input=tool_input)


#: `flotilla.guards.PUSH_WORDS`, for a failure in which even that module cannot be imported (a test keeps them equal)
LAST_RESORT_PUSH_WORDS = ("push", "gh")
#: `flotilla.guards.PERSON_WORDS`, kept equal the same way
LAST_RESORT_PERSON_WORDS = ("approve", "rig", "attention")


def _guard_failed(command: str, err: Exception, out) -> int:
    try:
        from flotilla.guards import on_failure
        finding = on_failure(command, err)
        refuse, ask, text = finding.refuse, finding.ask, finding.text
    except Exception:  # noqa: BLE001 - the plugin cannot even read its own rule; refuse what may push
        # no override: nothing was judged, so neither the command's text nor the environment waives anything
        bare = _unquoted(command)
        refuse = any(word in bare for word in LAST_RESORT_PUSH_WORDS)
        if "$" in bare or "`" in bare:   # as `flotilla.guards.may_be_persons`: the word anywhere is enough
            persons = any(word in bare for word in LAST_RESORT_PERSON_WORDS)
        else:
            words = re.compile(r"\b(?:%s)\b" % "|".join(LAST_RESORT_PERSON_WORDS))
            # an escape the code it is handed to may make a blank of (`\t`, `\x20`)
            blanked = _unquoted(re.sub(r"\\(?:x[0-9a-fA-F]{2}|u[0-9a-fA-F]{4}|[0-7]{1,3}|[\s\S])", " ", command))
            persons = "flotilla" in bare and (words.search(bare) or words.search(blanked)) is not None
        ask = not refuse and persons
        text = f"flotilla guards failed ({err}); " + (
            "a command that may push is refused on failure. Knowingly: the person runs the command themselves - "
            "with `!` in front, or in a terminal. Where flotilla's pre-push hook is installed, it judges a "
            "`git push` again" if refuse
            else "this line may hold a move only the person makes (`work approve`, a `rig` move, `attention`) and "
                 "it could not be read, so the person decides whether it runs" if ask
            else "the command runs unchecked")
    body = ({"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": text}
            if refuse else {"hookEventName": "PreToolUse", "permissionDecision": "ask",
                            "permissionDecisionReason": text}
            if ask else {"hookEventName": "PreToolUse", "additionalContext": text})
    print(json.dumps({"hookSpecificOutput": body}), file=out)
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
        checks = doctor.render(doctor.collect(cwd=ctx.root, timeout=HOOK_CHECK_TIMEOUT, setup=False,
                                              started_in=Path(payload.get("cwd") or ctx.root) if _fresh(payload)
                                              else ctx.root), quiet=True)
    except Exception as err:  # noqa: BLE001 - say what could not be checked, go on
        checks = [f"could not check this project: {err}"]
    if checks:
        print("flotilla: " + "; ".join(checks), file=out)
    if _fresh(payload):
        _update_note(ctx.root, out)
        if (Path(ctx.root) / ".git").is_dir():   # the main checkout, where the person works; seats run in trees
            from flotilla import update
            waits = update.deferred(paths.state_dir(), Path(ctx.root))
            if waits:
                print(f"flotilla: {waits}", file=out)
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


def _fleet_key(item) -> str:
    """The same item across turns, as the watch keys it: an age or a count in its wording is not news (review of
    0.7.17)."""
    return f"{item.kind}|{item.branch}|{item.who or item.text}"


def _prompt(ctx, payload, out, now) -> int:
    from flotilla.core import paths
    from flotilla.core.storage import LocalLogStore
    from flotilla.fleet import lead
    from flotilla.watch import inbox, render, throttle
    from flotilla.watch.whose import WAITING
    blocks, keys, entries = [], [], []
    current = (ctx.me.name or "") if getattr(ctx, "me", None) is not None else None
    title = lead.due(LocalLogStore(paths.state_dir() / "fleet"), str(payload.get("session_id") or ""),
                     current=current, now=now)
    if ctx.sessions is None:
        blocks.append(f"flotilla: could not ask which session this is: {ctx.census_error}")
    elif ctx.ledger is None:
        blocks.append(f"flotilla: the ledger could not be read: {ctx.ledger_error}")
    elif ctx.me is not None:
        mine = [item for item in ctx.mine() if item.kind != WAITING]
        from flotilla.watch.shown import persons_prompt
        # the queue asks git per row awaiting approval: only where a line or the orchestrator's count can follow
        # (final review of 0.26.0, M9 - a hook past its 10 s budget loses all its output)
        wanted = ctx.is_orchestrator or persons_prompt(payload.get("prompt"))
        everything = ctx.fleet() if wanted else []
        entries = inbox.collect(ctx, fleet_items=everything) if wanted else []
        # what waits on the person is counted in its own line on every prompt, never folded as standing (0.26.0)
        fleet_items = [item for item in everything if not inbox.covers(item)] if ctx.is_orchestrator else []
        if mine:
            blocks += ["flotilla - your move:", *render.lines(mine, now)]
        fleet_keys = [_fleet_key(item) for item in fleet_items]
        state = ctx.ledger.state_dir
        new, standing = throttle.fresh(state, ctx.session_id, fleet_keys) if fleet_items else (set(), 0)
        news = [item for item, key in zip(fleet_items, fleet_keys) if key in new]
        if news or standing:
            blocks += ["flotilla - the fleet (you are the orchestrator):", *render.lines(news, now)]
            if standing:
                blocks.append(f"  and {standing} standing item(s), said before and unchanged; "
                              "`flotilla watch --once` lists them all")
        keys = [f"{item.kind}|{item.branch}|{item.text}" for item in mine] + sorted(new) + \
            ([f"standing|{standing}"] if standing else [])
    keys = keys or blocks
    state = ctx.ledger.state_dir if ctx.ledger is not None else paths.state_dir()
    shown_line = _persons_line(ctx, payload, entries, now) if ctx.ledger is not None and ctx.me is not None else ""
    model_line = inbox.model_line(entries) if ctx.is_orchestrator else ""
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
                "additionalContext": "\n".join([note, *blocks, *([model_line] if model_line else [])])}
        print(json.dumps({"hookSpecificOutput": body, **({"systemMessage": shown_line} if shown_line else {})}),
              file=out)
        return 0
    said = blocks if throttle.due(state, ctx.session_id, throttle.digest(keys) if keys else "", now) else []
    if not shown_line and not model_line:
        if said:
            print("\n".join(said), file=out)
        return 0
    told = "\n".join([*said, *([model_line] if model_line else [])])
    answer = {"systemMessage": shown_line} if shown_line else {}
    if told:   # the model's context; the person's line goes past the model, as the hook's systemMessage
        answer["hookSpecificOutput"] = {"hookEventName": "UserPromptSubmit", "additionalContext": told}
    print(json.dumps(answer), file=out)
    return 0


def _persons_line(ctx, payload, entries, now) -> str:
    """The person's line when it is due on this prompt (`watch/shown.py`). Nothing is counted here: the turn is left
    pending and the Stop hook counts it only if the transcript records the prompt as typed by a human. A record that
    cannot be written never costs the person the line."""
    from flotilla.core import paths
    from flotilla.watch import attention, inbox, shown
    if not shown.persons_prompt(payload.get("prompt")):
        return ""
    state, repo = ctx.ledger.state_dir, ctx.ledger.repo_key
    interactive = getattr(ctx.me, "kind", "") == "interactive"
    # the person's windows share one line per project; a background session keeps its own, so a seat's launch
    # prompt or a firing nobody reads never uses up the person's line (final review of 0.26.0, I1)
    throttle = repo if interactive else f"{repo}\x00{ctx.session_id}"
    try:
        mode, every = attention.mode(paths.state_dir())
        due = shown.line_due(shown.load(state, throttle), entries, mode=mode, every=every, now=now)
    except Exception:  # noqa: BLE001 - a schedule that cannot be read shows the line rather than hiding it
        due = bool(entries)
    try:
        if not entries:
            shown.clear_line(state, throttle)
        shown.hold_turn(state, ctx.session_id, {
            "repo": repo, "throttle": throttle, "interactive": interactive, "at": now.isoformat(timespec="seconds"),
            "prompt_id": str(payload.get("prompt_id") or ""),
            "line": shown.line_record(entries) if due else None})
    except Exception:  # noqa: BLE001 - security review of 5f65ad5, I1: a record that fails never drops the line
        pass
    return inbox.person_line(entries) if due else ""


def _stop(ctx, payload, out, now) -> int:
    from flotilla.ledger.model import now_iso
    from flotilla.watch import fleet, guard
    if ctx.ledger is not None:
        try:   # the turn's prompt is in the transcript now: counted only if a human typed it (watch/shown.py)
            from flotilla.watch import shown
            shown.settle(ctx.ledger.state_dir, ctx.session_id, payload.get("transcript_path"))
        except Exception:  # noqa: BLE001 - a record that cannot be settled counts nothing, and stops nothing
            pass
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
