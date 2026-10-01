"""`flotilla onboard …` — the deterministic half of onboarding; the skill asks the questions."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from flotilla.core import config, paths, repo
from flotilla.onboard import answers as store
from flotilla.onboard import machine
from flotilla.onboard.check import check_drift, exit_code
from flotilla.onboard.detect import detect
from flotilla.onboard.firstrun import load_measurements, run_tier, save_measurements
from flotilla.onboard.profile import HEADER, ProfileExists, ProfileUnsafe, build_profile, write_profile
from flotilla.onboard.tomlw import render_toml
from flotilla.onboard.questions import AnswerError, all_questions, next_questions, validate_answer
from flotilla.posts import PostError, install_templates


def _machine() -> int:
    data = machine.measure_machine()
    path = machine.write_machine(paths.state_dir(), data)
    print(f"machine profile written: {path}")
    python3 = data["python3"]
    if not python3.get("ok"):
        detail = python3.get("error") or f"python3 on PATH is {python3.get('version')} ({python3.get('path')})"
        print(f"fail  python3: {detail}; hooks need 3.11+ (`brew install python` or `uv python install 3.11`)")
        return 1
    print(f"ok    python3 {python3['version']} at {python3['path']}; gh {data['gh']}")
    return 0


def commands_of(data: dict, where: str = "") -> list[tuple[str, str]]:
    """Every shell command a profile runs or stores, with where it sits: each tier's, the gate's, the revision's."""
    found = []
    for key, value in data.items():
        here = f"{where}.{key}" if where else key
        if isinstance(value, dict):
            found += commands_of(value, here)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    found += commands_of(item, f"{here}[{item.get('name', '')}]")
        elif key.endswith("command") and isinstance(value, str) and value:
            found.append((here, value))
    return found


def confirmation_mark(text: str) -> str:
    """Over the whole profile as it will be written: a changed command, mode or guard changes the mark."""
    return hashlib.sha256(text.encode()).hexdigest()[:12]


def _write(det: dict, given: dict, args, state: Path) -> int:
    remaining = next_questions(det, given)
    if remaining:
        print(f"questions remain: {', '.join(q['id'] for q in remaining)}; run `flotilla onboard next`")
        return 2
    root = Path(det["root"])
    target = root / config.PROJECT_DIR / config.PROJECT_FILE
    if target.exists() and not args.force:
        print(f"{target} already exists; run `flotilla onboard check`, or re-onboard with --force")
        return 2
    data = build_profile(det, given)
    text = render_toml(data, header=HEADER)
    mark = confirmation_mark(text)
    if args.confirm != mark:   # the answers are a plain file a session can edit: the whole profile is shown here
        from flotilla.core.text import visible
        print(f"the profile it writes, {config.PROJECT_DIR}/{config.PROJECT_FILE}:")
        for line in text.splitlines():
            print(f"  | {visible(line)}")
        commands = commands_of(data)
        if commands:
            print("of which these are shell commands it runs or stores:")
            for where, command in commands:
                print(f"  {visible(where)}: {visible(command)}")
        print("show the person the profile; when the person agrees, run "
              f"`flotilla onboard write --confirm {mark}` (with the same other options)")
        return 5
    tiers = (data.get("tests") or {}).get("tier") or []
    if tiers and not args.no_run:
        from flotilla.core.text import visible
        for tier in tiers:   # named before it runs: a person reading this sees what the shell is given
            print(f"will run {tier['name']}: {visible(tier['command'])}")
        runs = [run_tier(t["name"], t["command"], root, timeout=args.timeout) for t in tiers]
        for run in runs:
            timing = f"{run.seconds:.1f}s" if run.seconds is not None else "no time recorded"
            print(f"{run.status:<9} {run.name}: {run.summary or ''} ({timing})")
            if run.status != "green":
                print(f"--- last lines of {run.name} ---\n{run.tail}\n---")
        if any(run.status != "green" for run in runs) and not args.keep_unmeasured:
            print("not written: a tier is not green. Fix the command (`flotilla onboard answer tiers …`) "
                  "or the project, or pass --keep-unmeasured")
            return 4
        save_measurements(state, det["repo_key"], runs)
    try:
        path = write_profile(root, data, force=args.force)
    except (ProfileExists, ProfileUnsafe) as err:
        print(err)
        return 2
    try:
        installed = install_templates(root)
    except PostError as err:
        print(f"profile written, but the posts were not: {err}")
        return 2
    store.reset(state, det["repo_key"])
    print(f"project profile written: {path}")
    print(f"posts: {len(installed)} template(s) installed in .flotilla/posts/ (existing posts are kept)")
    print("next: `flotilla onboard publish` commits .flotilla/ and pushes it to origin's trunk - every session reads "
          "its rules from there, so until then every ledger move is refused")
    return 0


def _git(root: Path, *args) -> "subprocess.CompletedProcess":
    import subprocess
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=False)


def _publish(root: Path, det: dict, state: Path) -> int:
    """The last step of onboarding: every session reads its rules from origin's trunk, so the profile is committed,
    the tiers run over that commit - the push receipt a guarded push asks for - and the commit pushed to trunk
    (field test W5: onboarding said "commit" and stopped)."""
    from flotilla.ledger import receipts
    try:
        profile = config.load_project(root).data
    except config.ConfigError as err:
        print(f"not published: {err}")
        return 2
    trunk = (profile.get("trunk") or {}).get("branch", "main")
    branch = _git(root, "symbolic-ref", "-q", "--short", "HEAD").stdout.strip()
    if branch != trunk:
        print(f"not published: this tree is on `{branch or 'a detached HEAD'}`; publish from `{trunk}`, the trunk the "
              "profile names")
        return 2
    paths = [path for path in (".flotilla", ".claude/settings.json") if (root / path).exists()]
    _git(root, "add", "--", *paths)
    if _git(root, "diff", "--cached", "--quiet").returncode != 0:
        done = _git(root, "commit", "-q", "-m", "chore: onboard flotilla (profile and posts)")
        if done.returncode != 0:
            print(f"not published: git commit failed: {(done.stderr or done.stdout).strip()[:300]}")
            return 2
        print(f"committed {', '.join(paths)}")
    try:
        receipt = receipts.run_receipt(root, state=state, repo_key=det["repo_key"], purpose="push", profile=profile,
                                       timeout=1800.0)
    except receipts.ReceiptRefused as err:
        print(f"not pushed: {err}. The profile is committed; commit or stash your own changes, then run "
              "`flotilla onboard publish` again")
        return 2
    red = [tier["name"] for tier in receipt.get("tiers") or [] if tier.get("status") != "green"]
    if red:
        print(f"not pushed: the tiers are not green over {receipt.get('sha', '')[:7]} ({', '.join(red)}); fix the "
              "project or the tier commands and run `flotilla onboard publish` again - the commit stays")
        return 4
    if not det.get("remote"):
        print("committed; this repository has no origin, so the profile is on its trunk already")
        return 0
    done = _git(root, "push", "origin", trunk)
    if done.returncode != 0:
        print(f"not pushed: git push origin {trunk} failed: {(done.stderr or done.stdout).strip()[:300]}. If trunk "
              "only takes pull requests, open one with the commit above and merge it; the fleet reads the profile "
              "once it is on origin's trunk")
        return 2
    print(f"pushed to origin/{trunk}: the fleet reads its rules from there")
    return 0


def run_onboard(args) -> int:
    if args.action == "machine":
        return _machine()
    state = paths.state_dir()
    try:
        det = detect(Path(args.root))
    except repo.NotARepository as err:
        print(f"{err}; run onboarding from inside the repository, or pass --root")
        return 2
    given = store.load(state, det["repo_key"])
    if args.action in ("answer", "write", "reset", "quick", "publish"):   # what is recorded runs through a shell
        from flotilla.core import caller
        refused = caller.person_refusal("records, runs or forgets onboarding answers")
        if refused:
            print(f"refused: {refused}")
            return 2
    if args.action == "detect":
        print(json.dumps(det, indent=2, sort_keys=True))
        return 0
    if args.action == "next":
        page = next_questions(det, given)
        print(json.dumps({"questions": page, "done": not page}, indent=2))
        return 0
    if args.action == "answer":
        question = next((q for q in all_questions(det, given) if q["id"] == args.question), None)
        if question is None:
            print(f"no question `{args.question}` applies now; run `flotilla onboard next`")
            return 2
        try:
            given[args.question] = validate_answer(question, args.values)
        except AnswerError as err:
            print(err)
            return 2
        store.save(state, det["repo_key"], given)
        print(f"recorded {args.question}")
        return 0
    if args.action == "write":
        return _write(det, given, args, state)
    if args.action == "check":
        try:
            profile = config.load_project(Path(det["root"])).data
        except config.ConfigError as err:
            print(err)
            return 2
        findings = check_drift(profile, det, load_measurements(state, det["repo_key"]))
        for finding in findings:
            print(finding)
        return exit_code(findings)
    if args.action == "publish":
        return _publish(Path(det["root"]), det, state)
    if args.action == "quick":
        from flotilla.onboard.questions import quick_answers
        given = quick_answers(det, given)
        store.save(state, det["repo_key"], given)
        for question in all_questions(det, given):
            value = given.get(question["id"])
            print(f"{question['id']}: {', '.join(value) if isinstance(value, list) else value}")
        return 0
    if args.action == "reset":
        store.reset(state, det["repo_key"])
        print("answers forgotten")
        return 0
    return 2
