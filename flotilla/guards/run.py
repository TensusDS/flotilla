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


#: The origin URL shapes the push allow accepts: what git and the ledger's key read alike. Anything else - userinfo
#: with a colon (`evil.example:x@github.com:o/r` keyed as github, connected to evil.example), credentials, a
#: transport helper, a relative file URL, whitespace, a leading dash - is the classifier's to judge.
_URLS = (r"git@[A-Za-z0-9.-]+:[A-Za-z0-9._/-]+", r"https://[A-Za-z0-9.-]+(:[0-9]+)?/[A-Za-z0-9._/-]+",
         r"ssh://git@[A-Za-z0-9.-]+(:[0-9]+)?/[A-Za-z0-9._/-]+", r"/[A-Za-z0-9._/-]+")


def _plain_url(url: str) -> bool:
    import re
    return any(re.fullmatch(shape, url) for shape in _URLS)


def _git_out(at, *args, run):
    done = run(["git", "-C", str(at), *args], capture_output=True, text=True, check=False, timeout=8)   # the hook
    # itself has 30 s; a slow call fails safe - no allow - but should not take the whole budget (final review, I1)
    return done.stdout.strip() if done.returncode == 0 else None


def _caller_name(session_id: str) -> str:
    """The census's name for the session that made the call, or ""."""
    from flotilla.core.census import read_census
    if not session_id:
        return ""
    try:
        return next((item.name or "" for item in read_census(timeout=8) if item.session_id == session_id), "")
    except Exception:  # noqa: BLE001 - who calls could not be asked, so nothing is allowed on its behalf
        return ""


def _config_is_plain(tree: Path, run) -> bool:
    """The repository's own config holds only plain keys - judged by where an entry comes from, not by the scope git
    names: a global includeIf may read a file inside the repository (final review of 0.6.10, M2)."""
    import re
    listed = _git_out(tree, "config", "--list", "--show-scope", "--show-origin", run=run)
    common = _git_out(tree, "rev-parse", "--path-format=absolute", "--git-common-dir", run=run)
    if listed is None or not common:
        return False
    inside = (tree.resolve(), Path(common).resolve())
    for line in listed.splitlines():
        scope, _, rest = line.partition("\t")
        origin, _, entry = rest.partition("\t")
        source = Path(origin.split(":", 1)[1]) if origin.startswith("file:") else None
        if source is not None and not source.is_absolute():
            source = tree / source
        ours = source is not None and any(source.resolve().is_relative_to(place) for place in inside)
        if scope not in ("local", "worktree") and not ours:
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


def _origin_trunk(root: Path, trunk: str, run):
    """(origin's trunk revision, the rules read at it) - or None. What an allow grants is read here, never from a
    ref or a file a session can change (scan of 0.6.10, F1; review of the directory readiness work, I1): origin is
    asked, by the one plain URL this repository names and with no repository hook, for its default branch and that
    branch's revision; trunk must be the default branch, the local `origin/<trunk>` must be that revision, and the
    rules are read at it."""
    from flotilla.ledger import gitq
    from flotilla.ledger.commands import trunk_rules
    if not _config_is_plain(root, run):   # before any network call: nothing in the config runs on it
        return None
    urls = (_git_out(root, "config", "--show-scope", "--get-all", "remote.origin.url", run=run) or "").splitlines()
    if len(urls) != 1 or not urls[0].startswith("local\t") or not _plain_url(urls[0][len("local\t"):]):
        return None
    url = urls[0][len("local\t"):]
    listed = _git_out(root, "-c", f"core.hooksPath={_empty_hooks()}", "ls-remote", "--symref", url, "HEAD",
                      f"refs/heads/{trunk}", run=run) or ""
    default = base = ""
    for line in listed.splitlines():
        words = line.split()
        if len(words) == 3 and words[0] == "ref:" and words[2] == "HEAD":
            default = words[1]
        elif len(words) == 2 and words[1] == f"refs/heads/{trunk}":
            base = words[0]
    if default != f"refs/heads/{trunk}" or not base:   # a branch that names itself trunk is not origin's trunk
        return None
    if gitq.resolve(root, f"refs/remotes/origin/{trunk}^{{commit}}", run=run) != base:
        return None
    rules = trunk_rules(root, at=base)
    if str((rules.profile.get("trunk") or {}).get("branch") or "main") != trunk:
        return None
    return base, rules


def _senders_push(command: str, cwd, root, session_id: str, run) -> str:
    """The sender's push of accounted work, rewritten so it pushes exactly what was checked, or "" (twosuns field test
    of 0.6.7, W9; three reviews of 0.6.10). The typed command is exactly `git -C <absolute tree> push origin
    HEAD:<trunk>`; the project's flow is direct with the receipt guard on; the tree is a checkout of the project's
    repository and the home of an open row owned by the caller, whose post may land; the repository's own config
    holds nothing that runs code or redirects the push; origin's trunk - asked of origin - is the base, the local
    `origin/<trunk>` is that revision, the rules that grant (flow, posts, approval) are read at it, and the ledger
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
    if not caller or not _config_is_plain(tree, run):   # before any network call: nothing in its config runs on it
        return ""
    # the accounting reads history through replace refs and grafts; a push sends the real objects (review after the
    # final one): with any of them, or a shallow history, what is counted may not be what is pushed
    common = _git_out(tree, "rev-parse", "--path-format=absolute", "--git-common-dir", run=run)
    if not common or _git_out(tree, "for-each-ref", "--count=1", "refs/replace/", run=run) != "" \
            or (Path(common) / "info" / "grafts").exists() \
            or _git_out(tree, "rev-parse", "--is-shallow-repository", run=run) != "false":
        return ""
    # one origin URL, the project's own: a second one received the push too, and the first answered ls-remote (C1)
    urls = (_git_out(tree, "config", "--show-scope", "--get-all", "remote.origin.url", run=run) or "").splitlines()
    own = (_git_out(root, "config", "--get-all", "remote.origin.url", run=run) or "").splitlines()
    if len(urls) != 1 or len(own) != 1 or urls[0] != f"local\t{own[0]}":
        return ""
    url = own[0]
    got = _origin_trunk(root, trunk, run)   # origin's trunk and the rules there: what grants (F1)
    head = gitq.resolve(tree, "HEAD", run=run)
    if got is None or head is None or gitq.resolve(tree, got[0], run=run) is None:
        return ""
    base, rules = got
    if not opted_in(rules.profile) or (rules.profile.get("flow") or {}).get("mode") != "direct" \
            or not (rules.profile.get("guards") or {}).get("push_receipt"):
        return ""
    ledger = open_ledger(root, rules=rules)
    rows = ledger.rows()
    owned = [row for row in rows.values() if row.is_open and row.tree and Path(row.tree).is_absolute()
             and Path(row.tree).resolve() == tree and row.owner == caller]
    if not any((post := post_for_session(rules.posts, row.owner)) is not None and "land" in post.may
               for row in owned):
        return ""
    if not rules.tree or batch.unaccounted(ledger, rows, head, since=base, rules_tree=rules.tree) != []:
        return ""
    # the push guard judged the receipt by the rules it read from the local ref; the allow asks again under origin's
    from flotilla.core import paths, repo
    from flotilla.guards import push
    if push._failures([(trunk, head)], directory=tree, profile=rules.profile, state_dir=paths.state_dir(),
                      repo_key=repo.identify(root).key, run=run):
        return ""
    return (f"git -C {shlex.quote(str(tree))} -c core.hooksPath={shlex.quote(str(_empty_hooks()))} push "
            f"{shlex.quote(url)} {head}:refs/heads/{trunk}")


#: The person's opt-in, in `[permissions]` of the profile on trunk. Off unless set: the Software Directory Policy says
#: software must not "evade or enable users to circumvent Claude's safety guardrails", so letting checked commands
#: past auto mode's classifier is the person's own choice, never flotilla's default (directory readiness).
OPT_IN = "skip_classifier_for_checked"


def opted_in(profile: dict) -> bool:
    return (profile.get("permissions") or {}).get(OPT_IN) is True


NOT_OPTED = ("flotilla: this is one of flotilla's own checked commands. In this project flotilla does not let them "
             "past auto mode's classifier: `[permissions] skip_classifier_for_checked` is off in the profile on "
             "trunk (README, \"Letting checked commands past auto mode's classifier\"). If the classifier refuses "
             "one, tell the person that; turning it on is the person's choice, never yours.")


def not_opted_note(command: str, root, session_id: str, run=subprocess.run) -> str:
    """Said once per session, where only the missing opt-in keeps flotilla from allowing a command (review of the
    directory readiness work, M6): after updating, the classifier's refusals come back and nothing else says why."""
    import re
    from flotilla.core import paths
    from flotilla.guards.rules import rules_for
    if root is None or not session_id:
        return ""
    try:
        profile = rules_for(Path(root), run=run)[0]
        if opted_in(profile):
            return ""
        trunk = str((profile.get("trunk") or {}).get("branch") or "main")
        push = re.fullmatch(rf"git -C ({_PATH}) push origin HEAD:{re.escape(trunk)}", command)
        if not (_own(command, root) or push):
            return ""
        told = paths.state_dir() / "opt-in-told" / re.sub(r"[^A-Za-z0-9-]", "_", session_id)
        if told.exists():
            return ""
        told.parent.mkdir(parents=True, exist_ok=True)
        told.touch()
    except Exception:  # noqa: BLE001 - a note that cannot be worked out is not said
        return ""
    return NOT_OPTED


def allowance(command: str, cwd, root, *, session_id: str = "", run=subprocess.run) -> tuple[str, str]:
    """(why flotilla lets this call past Claude Code's permission check, the command to run instead) - or ("", "").
    A PreToolUse allow passes auto mode's classifier (measured on Claude Code 2.1.288), so it is given only where the
    person opted in and flotilla's own checks are the whole story; anything else stays the classifier's or the
    person's."""
    from flotilla.guards.rules import rules_for
    try:
        if root is None:
            return "", ""
        profile = rules_for(Path(root), run=run)[0]
        if not opted_in(profile):   # where nobody opted in, origin is not even asked
            return "", ""
        if _own(command, root):
            got = _origin_trunk(Path(root), str((profile.get("trunk") or {}).get("branch") or "main"), run)
            if got is None or not opted_in(got[1].profile):   # the opt-in that counts is on origin's trunk
                return "", ""
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
    sandboxed = not (tool_input or {}).get("dangerouslyDisableSandbox")   # its prompt stays (final review, M1)
    allowed, rewritten = ("", "") if findings or mode != "auto" or not sandboxed else \
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
    elif mode == "auto" and sandboxed and (note := not_opted_note(command, root, session_id, run)):
        body = {"hookEventName": "PreToolUse", "additionalContext": note}
    else:
        return 0
    print(json.dumps({"hookSpecificOutput": body}), file=out)
    return 0
