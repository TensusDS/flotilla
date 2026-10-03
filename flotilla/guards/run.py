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
    from flotilla.guards import lane, line_edit, person, push, revert, shell
    from flotilla.guards.rules import rules_for
    segments = shell.segments(command, Path(cwd) if cwd else None)
    persons = [found for found in (person.check(segment) for segment in segments) if found]   # always on, no rules
    if root is None:   # the session stands in no project; a push into one is still judged by that project (F18)
        return persons + [found for found in (push.guard(segment, root=None, profile={}, env=env, run=run)
                                              for segment in segments if push.door(segment) is not None)
                          if found is not None]
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
        return persons + found or [Finding("rules", False, f"flotilla guards: the project's rules could not be read ({err}); "
                                                 "the revert and line-number guards did not run")]
    on = profile.get("guards") or {}
    findings: list[Finding] = list(persons)
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


def _own(command: str) -> bool:
    from flotilla.broker.decide import own_command
    return own_command({"tool_name": "Bash", "tool_input": {"command": command}})


def _senders_push(command: str, cwd, root, run) -> bool:
    """Whether this is the sender's push of accounted work, as every check flotilla has says (twosuns field test of
    0.6.7, W9; review of 0.6.10, I2, I3): one exact push of HEAD to trunk, no option; a direct-flow project with the
    receipt guard on; the tree is the home of an open row whose owner's post may land; and the ledger accounts for
    every commit the push carries. The push guard itself has already run and found nothing to say."""
    from flotilla.guards import shell
    from flotilla.guards.rules import rules_for
    from flotilla.ledger import batch, gitq
    from flotilla.ledger.commands import open_ledger
    from flotilla.posts import post_for_session
    if root is None:
        return False
    profile, _ = rules_for(Path(root), run=run)
    trunk = str((profile.get("trunk") or {}).get("branch") or "main")
    if (profile.get("flow") or {}).get("mode") != "direct" or not (profile.get("guards") or {}).get("push_receipt"):
        return False
    segments = shell.segments(command, Path(cwd) if cwd else None)
    if len(segments) != 1 or segments[0].assignments:
        return False
    words = list(segments[0].words)
    if words == ["git", "push", "origin", f"HEAD:{trunk}"]:
        tree = Path(cwd)
    elif len(words) == 6 and words[:2] == ["git", "-C"] and words[3:] == ["push", "origin", f"HEAD:{trunk}"]:
        tree = Path(words[2])
    else:
        return False
    tree = tree.resolve()
    ledger = open_ledger(Path(root))
    rows = ledger.rows()
    home = [row for row in rows.values() if row.is_open and row.tree and Path(row.tree).resolve() == tree]
    if not any((post := post_for_session(ledger.posts, row.owner)) is not None and "land" in post.may
               for row in home):
        return False
    head = gitq.resolve(tree, "HEAD", run=run)
    remote = gitq.resolve(tree, f"refs/remotes/origin/{trunk}", run=run) or ""
    loose = batch.unaccounted(ledger, rows, head, since=remote) if head else None
    return loose == []


def allowance(command: str, cwd, root, *, run=subprocess.run) -> str:
    """Why flotilla lets this call past Claude Code's permission check, or "": its own command, or the sender's push
    of accounted work. A PreToolUse allow passes auto mode's classifier (measured on Claude Code 2.1.288), so it is
    given only where flotilla's own checks are the whole story; anything else stays the classifier's or the
    person's."""
    try:
        if _own(command):
            return "flotilla's own command; flotilla checks the post, the state and the evidence itself"
        if _senders_push(command, cwd, root, run):
            return ("the sender's push of accounted work: the receipt is green over the pushed revision and the "
                    "ledger accounts for every commit it carries")
    except Exception:  # noqa: BLE001 - an allow that could not be justified is not given
        return ""
    return ""


def guard_hook(command: str, cwd, root, out, *, env=os.environ, run=subprocess.run, mode: str = "") -> int:
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
    # the allow answers auto mode's classifier only: in ask mode the person sees what they chose to see
    allowed = "" if findings or mode != "auto" else allowance(command, cwd, root, run=run)
    if refusals:
        body = {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                "permissionDecisionReason": "\n\n".join(refusals)}
    elif allowed:
        body = {"hookEventName": "PreToolUse", "permissionDecision": "allow", "permissionDecisionReason": allowed}
    elif findings:
        body = {"hookEventName": "PreToolUse", "additionalContext": "\n".join(f.text for f in findings)}
    else:
        return 0
    print(json.dumps({"hookSpecificOutput": body}), file=out)
    return 0
