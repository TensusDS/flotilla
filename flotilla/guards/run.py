"""`flotilla hook guard`: every enabled guard over one Bash command, in one process (spec, section 10).

The hook fires on every Bash call, so its common path is a text check in `flotilla.hooks` that imports nothing: a
command naming none of the guarded programs never reaches this module. Which guards are on is read from the
profile (`rules_for`). A refusal is a `deny` the session reads; a warning is context beside the result. A guard's
own failure is decided by reversibility: revert and line-number let the command through and say so, the push
receipt refuses.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from flotilla.guards import Finding

PUSH_WORDS = ("push", "gh")


def _safely(name: str, check) -> list[Finding]:
    try:
        found = check()
    except Exception as err:  # noqa: BLE001 - a reversible guard that fails lets the command through, and says so
        return [Finding(name, False, f"flotilla {name} guard failed and let the command through: {err}")]
    return [found] if found else []


def _override(segment, env) -> str:
    from flotilla.guards.push import OVERRIDE
    return (segment.assignments.get(OVERRIDE) or env.get(OVERRIDE) or "").strip()


def evaluate(command: str, cwd, root, *, env=os.environ, run=subprocess.run) -> list[Finding]:
    from flotilla.guards import lane, line_edit, push, revert, shell
    from flotilla.guards.rules import rules_for
    segments = shell.segments(command, Path(cwd) if cwd else None)
    if root is None:   # the session stands in no project; a push into one is still judged by that project (F18)
        return [found for found in (push.guard(segment, root=None, profile={}, env=env, run=run)
                                    for segment in segments if push.door(segment) is not None) if found is not None]
    try:
        profile, _ = rules_for(Path(root), run=run)
    except Exception as err:  # noqa: BLE001 - which guards are on is unknown
        found = []
        for segment in segments:
            if push.door(segment) is None:
                continue
            if _override(segment, env):
                found.append(Finding(push.GUARD, False, f"flotilla push receipt: the rules could not be read "
                                                        f"({err}); the override lets it through, unrecorded"))
            else:
                found.append(Finding(push.GUARD, True, f"flotilla push receipt: `{segment.text}`: the project's "
                                                       f"rules could not be read ({err}), so whether it needs a "
                                                       f"receipt is unknown; fix the profile, or, knowingly: "
                                                       f'{push.OVERRIDE}="<why>"'))
        return found or [Finding("rules", False, f"flotilla guards: the project's rules could not be read ({err}); "
                                                 "the revert and line-number guards did not run")]
    on = profile.get("guards") or {}
    findings: list[Finding] = []
    for segment in segments:
        if on.get(revert.GUARD):
            findings += _safely(revert.GUARD, lambda: revert.check(segment, run=run))
        if on.get(line_edit.GUARD):
            findings += _safely(line_edit.GUARD, lambda: line_edit.check(segment))
        found = push.guard(segment, root=Path(root), profile=profile, env=env, run=run)   # it asks the door's
        if found is not None:                                                            # own project (F18)
            findings.append(found)
        if lane.is_on(profile):
            findings += _safely(lane.GUARD, lambda: lane.check(segment, profile, command))
    return findings


def guard_hook(command: str, cwd, root, out, *, env=os.environ, run=subprocess.run) -> int:
    try:
        findings = evaluate(command, cwd, root, env=env, run=run)
    except Exception as err:  # noqa: BLE001 - decided by reversibility: a command that may push is refused
        may_push = any(word in command for word in PUSH_WORDS)
        knowingly = "FLOTILLA_GATE_OVERRIDE=" in command or bool(env.get("FLOTILLA_GATE_OVERRIDE", "").strip())
        findings = [Finding("guards", may_push and not knowingly,
                            f"flotilla guards failed ({err}); "
                            + ("a command that may push is refused on failure. Knowingly: "
                               'FLOTILLA_GATE_OVERRIDE="<why>"' if may_push and not knowingly
                               else "the command runs unchecked"))]
    refusals = [finding.text for finding in findings if finding.refuse]
    if refusals:
        body = {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                "permissionDecisionReason": "\n\n".join(refusals)}
    elif findings:
        body = {"hookEventName": "PreToolUse", "additionalContext": "\n".join(f.text for f in findings)}
    else:
        return 0
    print(json.dumps({"hookSpecificOutput": body}), file=out)
    return 0
