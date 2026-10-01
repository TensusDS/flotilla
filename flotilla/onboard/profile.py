"""From detection and answers to `.flotilla/project.toml` (schema 1).

Only project facts go here. Measured tier times are machine facts and live in the state directory
(plan decision 1). A written file is read back through `config.load_project`, the same door every
other module uses, so a profile this module cannot load is never left behind silently.
"""

from __future__ import annotations

import os
import shlex
from pathlib import Path

from flotilla.core import config
from flotilla.onboard.questions import effective_answers, suggest_composition
from flotilla.onboard.tomlw import render_toml

HEADER = ("Written by `flotilla onboard write`. Edit by hand freely; `flotilla onboard check` reports drift.")
FLOW_MODES = {"pr-sender": "pr", "pr-human": "pr", "direct": "direct", "local": "local"}
KNOWN_TRACKERS = {"nowhere": None, "github-issues": "^#\\d+$", "pattern": "^[A-Z][A-Z0-9]+-\\d+$",
                  "own-register": ""}
GUARDS = ("revert", "line_edit", "push_receipt")


class ProfileExists(RuntimeError):
    """A project profile is already there; onboarding does not overwrite it without --force."""


class ProfileUnsafe(RuntimeError):
    """The profile path leads outside the repository (a symlink); nothing is written through it."""


RUNNERS = ("pytest", "vitest", "jest", "playwright", "cargo", "go", "npm", "pnpm", "yarn", "make", "tox", "nox",
           "ruff", "mypy")


def _tier_name(command: str, taken: set[str]) -> str:
    """A name a person reads in every receipt: the runner the command calls, else its first word (F6)."""
    words = [word.rsplit("/", 1)[-1] for word in command.split()]
    base = next((word for word in words if word in RUNNERS), words[0] if words else "") or "tier"
    name, number = base, 1
    while name in taken:
        number += 1
        name = f"{base}-{number}"
    return name


def _tiers(det: dict, chosen: list[str]) -> list[dict]:
    detected = {t["name"]: t for t in det.get("tests") or []}
    tiers, taken = [], {value for value in chosen if value in detected}
    for value in chosen:
        if value in ("none", "later"):
            continue
        if value in detected:
            tiers.append({"name": value, "command": detected[value]["command"], "required_for": ["handover", "push"]})
        else:
            name = _tier_name(value, taken)
            taken.add(name)
            tiers.append({"name": name, "command": value, "required_for": ["handover", "push"]})
    return tiers


def _ci(det: dict, answers: dict) -> dict:
    provider = answers.get("ci", "none") if det.get("remote") else "none"
    section: dict = {"provider": provider}
    if provider in ("github", "command"):
        section["runs_on"] = answers.get("ci_where", "cloud")
    if provider == "github":
        found = det.get("ci") or {}
        if found.get("jobs") is not None:
            section["required_jobs"] = list(found["jobs"])
        section["jobs_source"] = found.get("jobs_source", "unknown")
        if found.get("fingerprint"):
            section["workflow_fingerprint"] = found["fingerprint"]
    if provider == "command":
        command = answers.get("gate_command", "later")
        section["gate_command"] = "" if command in ("ask-human", "later") else command
        if section.get("runs_on") == "this-machine":
            section["queue_command"] = ""   # found in the file; the lane names it until it is filled in
    return section


def build_profile(det: dict, answers: dict) -> dict:
    answers = effective_answers(det, answers)
    root = Path(det["root"])
    flow = answers.get("flow", "local")
    signals = det.get("signals") or {}
    data: dict = {"schema": 1, "trunk": {"branch": det.get("trunk", "main")},
                  "flow": {"mode": FLOW_MODES.get(flow, "local")}}
    if "merge_auth" in answers:
        data["flow"]["merge_authorized_by"] = answers["merge_auth"]
    if flow in ("pr-sender", "pr-human"):
        methods = (det.get("ci") or {}).get("merge_methods") or []
        pr = {"opened_by": "sender", "merged_by": "sender" if flow == "pr-sender" else "human"}
        method = answers.get("merge_method") or (methods[0] if len(methods) == 1 else None)
        if method:
            pr["merge_method"] = method
        data["pr"] = pr
    siblings = [(Path(path).name, path) for path in signals.get("multi_repo") or []]
    order = answers.get("repos")
    data["repos"] = [{"name": root.name, "path": ".",
                      "push_after": [name for name, _ in siblings] if order == "siblings-first" else []}]
    data["repos"] += [{"name": name, "path": path, "push_after": [root.name] if order == "this-first" else []}
                      for name, path in siblings]
    tiers = _tiers(det, answers.get("tiers") or [])
    if tiers:
        data["tests"] = {"tier": tiers}
    data["ci"] = _ci(det, answers)
    data["review"] = {"depth": answers.get("review", "every")}
    data["permissions"] = {"mode": answers.get("permissions", "ask")}
    if answers.get("release") == "sender-semver":
        data["release"] = {"version_files": list((det.get("release") or {}).get("version_files") or []),
                           "tag": "v{version}", "annotated": True}
    from flotilla.fleet.project_name import clean
    # the word every seat name starts with (`worldcore-orchestrator 1`): numbers then run per project (W11)
    data["fleet"] = {"name": clean(Path(det["root"]).name), "default": suggest_composition(answers),
                     "model": answers.get("model", "one")}
    chosen_guards = answers.get("guards") or []
    data["guards"] = {guard: guard in chosen_guards and "none" not in chosen_guards for guard in GUARDS}
    tracker = answers.get("tracker", "nowhere")
    pattern = KNOWN_TRACKERS.get(tracker, tracker)
    if pattern is not None:
        close = {"field": "ref", "required": False}
        if pattern:
            close = {"field": "ref", "pattern": pattern, "required": False}
        data["evidence"] = {"close": close}
    if answers.get("deploy") in ("web", "cli", "api") and data["flow"]["mode"] != "local":
        # no deployment found: the build a person runs is trunk on origin, whose first word `walked` compares (F4)
        trunk = data["trunk"]["branch"]
        revision = "" if signals.get("deployment") else f"git ls-remote origin {shlex.quote('refs/heads/' + trunk)}"
        data["deploy"] = {"surface": answers["deploy"], "revision_command": revision}
        data["judge"] = {"required": True}
    if answers.get("shared") == "reserve":
        data["reservation"] = {"files": list(signals.get("shared_files") or [])}
    if answers.get("sequential") == "claim":
        data["numbering"] = {"directories": list(signals.get("sequential") or [])}
    return data


def write_profile(root: Path, data: dict, *, force: bool = False) -> Path:
    folder = Path(root) / config.PROJECT_DIR
    path = folder / config.PROJECT_FILE
    for candidate in (folder, path):
        if candidate.is_symlink():
            raise ProfileUnsafe(f"{candidate} is a symlink; flotilla writes the profile only inside the repository")
    if path.exists() and not force:
        raise ProfileExists(f"{path} already exists; run `flotilla onboard check`, or re-onboard with --force")
    folder.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(path, flags, 0o644), "w", encoding="utf-8") as handle:
        handle.write(render_toml(data, header=HEADER))
    config.load_project(Path(root))
    return path
