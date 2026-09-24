"""`flotilla onboard …` — the deterministic half of onboarding; the skill asks the questions."""

from __future__ import annotations

import json
from pathlib import Path

from flotilla.core import config, paths, repo
from flotilla.onboard import answers as store
from flotilla.onboard import machine
from flotilla.onboard.check import check_drift, exit_code
from flotilla.onboard.detect import detect
from flotilla.onboard.firstrun import load_measurements, run_tier, save_measurements
from flotilla.onboard.profile import ProfileExists, ProfileUnsafe, build_profile, write_profile
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
    tiers = (data.get("tests") or {}).get("tier") or []
    if tiers and not args.no_run:
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
    if args.action == "reset":
        store.reset(state, det["repo_key"])
        print("answers forgotten")
        return 0
    return 2
