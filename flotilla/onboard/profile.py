"""From detection and answers to `.flotilla/project.toml` (schema 1).

Only project facts go here. Measured tier times are machine facts and live in the state directory
(plan decision 1). A written file is read back through `config.load_project`, the same door every
other module uses, so a profile this module cannot load is never left behind silently.
"""

from __future__ import annotations

from pathlib import Path

from flotilla.core import config
from flotilla.onboard.questions import suggest_composition
from flotilla.onboard.tomlw import render_toml

HEADER = ("Written by `flotilla onboard write`. Edit by hand freely; `flotilla onboard check` reports drift.")
FLOW_MODES = {"pr-sender": "pr", "pr-human": "pr", "direct": "direct", "local": "local"}
KNOWN_TRACKERS = {"nowhere": None, "github-issues": "^#\\d+$", "pattern": "^[A-Z][A-Z0-9]+-\\d+$",
                  "own-register": ""}
GUARDS = ("revert", "line_edit", "push_receipt")


class ProfileExists(RuntimeError):
    """A project profile is already there; onboarding does not overwrite it without --force."""


def _tiers(det: dict, chosen: list[str]) -> list[dict]:
    detected = {t["name"]: t for t in det.get("tests") or []}
    tiers, custom = [], 0
    for value in chosen:
        if value in ("none", "later"):
            continue
        if value in detected:
            tiers.append({"name": value, "command": detected[value]["command"], "required_for": ["handover", "push"]})
        else:
            custom += 1
            tiers.append({"name": f"custom-{custom}", "command": value, "required_for": ["handover", "push"]})
    return tiers


def _ci(det: dict, answers: dict) -> dict:
    provider = answers.get("ci", "none")
    section: dict = {"provider": provider}
    if provider in ("github", "command"):
        section["runs_on"] = answers.get("ci_where", "cloud")
    if provider == "github":
        found = det.get("ci") or {}
        section["required_jobs"] = list(found.get("jobs") or [])
        section["jobs_source"] = found.get("jobs_source", "unknown")
        if found.get("fingerprint"):
            section["workflow_fingerprint"] = found["fingerprint"]
    if provider == "command":
        command = answers.get("gate_command", "later")
        section["gate_command"] = "" if command in ("ask-human", "later") else command
    return section


def build_profile(det: dict, answers: dict) -> dict:
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
    siblings = signals.get("multi_repo") or []
    data["repos"] = [{"name": root.name, "path": ".",
                      "push_after": list(siblings) if answers.get("repos") == "siblings-first" else []}]
    tiers = _tiers(det, answers.get("tiers") or [])
    if tiers:
        data["tests"] = {"tier": tiers}
    data["ci"] = _ci(det, answers)
    data["review"] = {"depth": answers.get("review", "every")}
    data["permissions"] = {"mode": answers.get("permissions", "ask")}
    if answers.get("release") == "sender-semver":
        data["release"] = {"version_files": list((det.get("release") or {}).get("version_files") or []),
                           "tag": "v{version}", "annotated": True}
    data["fleet"] = {"default": suggest_composition(answers), "model": answers.get("model", "one")}
    chosen_guards = answers.get("guards") or []
    data["guards"] = {guard: guard in chosen_guards for guard in GUARDS}
    tracker = answers.get("tracker", "nowhere")
    pattern = KNOWN_TRACKERS.get(tracker, tracker)
    if pattern is not None:
        close = {"field": "ref", "required": False}
        if pattern:
            close = {"field": "ref", "pattern": pattern, "required": False}
        data["evidence"] = {"close": close}
    if answers.get("deploy") in ("web", "cli", "api"):
        data["deploy"] = {"surface": answers["deploy"], "revision_command": ""}
        data["judge"] = {"required": False}
    if answers.get("shared") == "reserve":
        data["reservation"] = {"files": list(signals.get("shared_files") or [])}
    if answers.get("sequential") == "claim":
        data["numbering"] = {"directories": list(signals.get("sequential") or [])}
    return data


def write_profile(root: Path, data: dict, *, force: bool = False) -> Path:
    path = Path(root) / config.PROJECT_DIR / config.PROJECT_FILE
    if path.exists() and not force:
        raise ProfileExists(f"{path} already exists; run `flotilla onboard check`, or re-onboard with --force")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_toml(data, header=HEADER), encoding="utf-8")
    config.load_project(Path(root))
    return path
