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


def _own(command: str, root) -> bool:
    """One of flotilla's own commands, in this project, that runs nothing the profile names: not `lane`, whose bare
    form may run the profile's queue command (third review of 0.6.10, C1); never outside a project."""
    import shlex
    from flotilla.broker.decide import own_command
    if root is None or not own_command({"tool_name": "Bash", "tool_input": {"command": command}}, root):
        return False
    return shlex.split(command)[1] != "lane"


#: The one push form the guard lets past the classifier: the tree named absolutely, nothing the shell would read.
_PATH = r"/[^\s'\"\\$`;&|<>()*?\[\]{}~!#]+"
#: The repository's own config (local and worktree scope) may hold only these keys for the push to be allowed: any
#: other - a push URL, receive/upload pack, ssh command, hooks path, credential helper, include, url rewriting,
#: submodule pushing - may run code or send the push elsewhere (third review of 0.6.10, I2). The person's global and
#: system config are theirs, and trusted.
_SAFE_CONFIG = (r"core\.(repositoryformatversion|filemode|bare|logallrefupdates|ignorecase|precomposeunicode|symlinks)",
                r"remote\.origin\.(url|fetch)", r"branch\..+\.(remote|merge|rebase)", r"user\.(name|email)",
                r"extensions\.(worktreeconfig|objectformat)")


def _git_out(at, *args, run):
    done = run(["git", "-C", str(at), *args], capture_output=True, text=True, check=False, timeout=30)
    return done.stdout.strip() if done.returncode == 0 else None


def _caller_name(session_id: str) -> str:
    """The census's name for the session that made the call, or ""."""
    from flotilla.core.census import read_census
    if not session_id:
        return ""
    try:
        return next((item.name or "" for item in read_census() if item.session_id == session_id), "")
    except Exception:  # noqa: BLE001 - who calls could not be asked, so nothing is allowed on its behalf
        return ""


def _config_is_plain(tree: Path, run) -> bool:
    import re
    listed = _git_out(tree, "config", "--list", "--show-scope", run=run)
    if listed is None:
        return False
    for line in listed.splitlines():
        scope, _, entry = line.partition("\t")
        if scope not in ("local", "worktree"):
            continue
        key = entry.split("=", 1)[0].lower()
        if not any(re.fullmatch(pattern, key) for pattern in _SAFE_CONFIG):
            return False
    return True


def _empty_hooks() -> Path:
    from flotilla.core import paths
    empty = paths.state_dir() / "no-hooks"
    empty.mkdir(parents=True, exist_ok=True)
    return empty


def _senders_push(command: str, cwd, root, session_id: str, run) -> str:
    """The sender's push of accounted work, rewritten so it pushes exactly what was checked, or "" (twosuns field test
    of 0.6.7, W9; three reviews of 0.6.10). The typed command is exactly `git -C <absolute tree> push origin
    HEAD:<trunk>`; the project's flow is direct with the receipt guard on; the tree is a checkout of the project's
    repository and the home of an open row owned by the caller, whose post may land; the repository's own config
    holds nothing that runs code or redirects the push; origin's trunk - asked of origin - is the base, and the ledger
    accounts for every commit past it. The command run is then `git -C <tree> -c core.hooksPath=<an empty dir> push
    origin <the checked commit>:refs/heads/<trunk>`: no repository hook runs, and a HEAD that moved after the check
    pushes nothing else. The push guard itself has already run and found nothing to say. The origin URL anchors
    itself: the ledger is keyed by it, so a repointed origin finds no row."""
    import re
    import shlex
    from flotilla.broker.decide import _same_repository
    from flotilla.guards.rules import rules_for
    from flotilla.ledger import batch, gitq
    from flotilla.ledger.commands import open_ledger
    from flotilla.posts import post_for_session
    if root is None:
        return ""
    root = Path(root)
    profile, _ = rules_for(root, run=run)
    trunk = str((profile.get("trunk") or {}).get("branch") or "main")
    if (profile.get("flow") or {}).get("mode") != "direct" or not (profile.get("guards") or {}).get("push_receipt"):
        return ""
    match = re.fullmatch(rf"git -C ({_PATH}) push origin HEAD:{re.escape(trunk)}", command)
    if match is None:
        return ""
    tree = Path(match.group(1)).resolve()
    if not _same_repository(tree, root):
        return ""
    caller = _caller_name(session_id)
    ledger = open_ledger(root)
    rows = ledger.rows()
    owned = [row for row in rows.values() if row.is_open and row.tree and Path(row.tree).is_absolute()
             and Path(row.tree).resolve() == tree and caller and row.owner == caller]
    if not any((post := post_for_session(ledger.posts, row.owner)) is not None and "land" in post.may
               for row in owned):
        return ""
    if not _config_is_plain(tree, run):   # before any network call: nothing in the repo's config runs on it
        return ""
    listed = _git_out(tree, "-c", f"core.hooksPath={_empty_hooks()}", "ls-remote", "origin", f"refs/heads/{trunk}",
                      run=run)
    base = listed.split()[0] if listed else ""
    head = gitq.resolve(tree, "HEAD", run=run)
    if not base or head is None or gitq.resolve(tree, base, run=run) is None:
        return ""
    if batch.unaccounted(ledger, rows, head, since=base) != []:
        return ""
    return (f"git -C {shlex.quote(str(tree))} -c core.hooksPath={shlex.quote(str(_empty_hooks()))} push origin "
            f"{head}:refs/heads/{trunk}")


def allowance(command: str, cwd, root, *, session_id: str = "", run=subprocess.run) -> tuple[str, str]:
    """(why flotilla lets this call past Claude Code's permission check, the command to run instead) - or ("", "").
    A PreToolUse allow passes auto mode's classifier (measured on Claude Code 2.1.288), so it is given only where
    flotilla's own checks are the whole story; anything else stays the classifier's or the person's."""
    try:
        if _own(command, root):
            return "flotilla's own command; flotilla checks the post, the state and the evidence itself", ""
        rewritten = _senders_push(command, cwd, root, session_id, run)
        if rewritten:
            return ("the sender's push of accounted work, pinned to the checked commit with no repository hook: the "
                    "receipt is green over it and the ledger accounts for every commit past origin's trunk"), rewritten
    except Exception:  # noqa: BLE001 - an allow that could not be justified is not given
        return "", ""
    return "", ""


def guard_hook(command: str, cwd, root, out, *, env=os.environ, run=subprocess.run, mode: str = "",
               session_id: str = "", tool_input: dict | None = None) -> int:
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
    allowed, rewritten = ("", "") if findings or mode != "auto" else \
        allowance(command, cwd, root, session_id=session_id, run=run)
    if refusals:
        body = {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                "permissionDecisionReason": "\n\n".join(refusals)}
    elif allowed:
        body = {"hookEventName": "PreToolUse", "permissionDecision": "allow", "permissionDecisionReason": allowed}
        if rewritten:   # the allow covers the pinned command, never the typed one
            body["updatedInput"] = {**(tool_input or {}), "command": rewritten}
    elif findings:
        body = {"hookEventName": "PreToolUse", "additionalContext": "\n".join(f.text for f in findings)}
    else:
        return 0
    print(json.dumps({"hookSpecificOutput": body}), file=out)
    return 0
