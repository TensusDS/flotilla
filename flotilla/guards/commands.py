"""`flotilla guard check | status | install | githook`."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from flotilla.core import config, paths


def run_githook(name: str, root, stdin_text: str, *, env=os.environ, run=subprocess.run) -> int:
    if name == "pre-commit":
        from flotilla.guards import reserve
        try:
            code, text = reserve.check(root, env=env, run=run)
        except Exception as err:  # noqa: BLE001 - a commit can be amended: the reservation lets it through
            code, text = 0, f"flotilla reservation: the check failed and lets the commit through: {err}"
    else:
        from flotilla.guards import push
        try:
            code, text = push.pre_push(root, stdin_text, env=env, run=run)
        except Exception as err:  # noqa: BLE001 - a push cannot be taken back: refused on failure
            if env.get(push.OVERRIDE, "").strip():
                code, text = 0, f"flotilla pre-push: the check failed ({err}); the override lets it through, unrecorded"
            else:
                code, text = 1, (f"flotilla pre-push: the check failed ({err}), and a push is refused on failure. "
                                 f'Knowingly: {push.OVERRIDE}="<why>" git push ...')
    if text:
        print(text, file=sys.stderr)
    return code


def run_guard_command(args) -> int:
    from flotilla.guards import githooks
    if args.action == "githook":
        return run_githook(args.name, Path.cwd(), sys.stdin.read() if args.name == "pre-push" else "")
    if args.action == "check":
        root = config.find_project(Path(args.cwd))
        if root is None:
            print("not onboarded here: no guard is on")
            return 0
        from flotilla.guards.run import evaluate
        findings = evaluate(args.guarded_command, Path(args.cwd).resolve(), root)
        if not findings:
            print("allowed: no guard has anything to say")
        for finding in findings:
            print(f"{'refused' if finding.refuse else 'warning'}  {finding.guard}: {finding.text}")
        return 1 if any(finding.refuse for finding in findings) else 0
    root = config.find_project(Path(args.root))
    if root is None:
        print(f"refused: not onboarded: no .flotilla/project.toml at or above {Path(args.root).resolve()}")
        return 2
    from flotilla import hooks
    from flotilla.guards.rules import rules_for
    if args.action == "install":
        names = [name for name, chosen in (("pre-commit", args.pre_commit), ("pre-push", args.pre_push)) if chosen]
        if not names:
            print("name the hooks to install: --pre-commit, --pre-push")
            return 2
        ok, said = githooks.install(root, names, state_dir=paths.state_dir(), cli=hooks.CLI)
        print("\n".join(said))
        return 0 if ok else 1
    try:
        profile, note = rules_for(root)
    except Exception as err:  # noqa: BLE001 - say what could not be read
        profile, note = {}, f"the rules could not be read: {err}"
    if note:
        print(f"rules: {note}")
    on = profile.get("guards") or {}
    for name in ("revert", "line_edit", "push_receipt"):
        print(f"guard {name}: {'on' if on.get(name) else 'off'}")
    asked = githooks.wanted(profile)
    for name, state in githooks.status(root):
        print(f"git hook {name}: {state}{' (the profile asks for it)' if name in asked else ''}")
    link = githooks.link_path(paths.state_dir())
    print(f"link {link}: {'-> ' + os.readlink(link) if link.is_symlink() else 'missing (made at session start)'}")
    return 0
