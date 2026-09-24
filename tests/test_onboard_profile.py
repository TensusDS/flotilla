import tomllib

import pytest

from flotilla.core import config
from flotilla.onboard.profile import ProfileExists, build_profile, write_profile


def detection(root, **overrides):
    base = {
        "root": str(root), "remote": True, "trunk": "main",
        "tests": [{"name": "python", "command": "uv run pytest", "source": "pyproject.toml"}],
        "ci": {"provider": "github", "jobs": ["test", "lint"], "jobs_source": "last-push-run",
               "fingerprint": "sha256:abc", "merge_methods": ["squash"]},
        "release": {"version_files": ["pyproject.toml"], "latest_tag": "v0.1.0"},
        "signals": {"multi_repo": [], "deployment": [], "shared_files": ["TODO.md"], "sequential": []},
    }
    base.update(overrides)
    return base


BASE_ANSWERS = {"flow": "pr-sender", "merge_auth": "human", "review": "every", "permissions": "ask",
                "ci": "github", "ci_where": "cloud", "tiers": ["python"], "tracker": "nowhere",
                "guards": ["revert", "push_receipt"], "model": "one"}


def test_profile_loads_through_the_config_door(tmp_path):
    write_profile(tmp_path, build_profile(detection(tmp_path), BASE_ANSWERS))
    assert config.load_project(tmp_path).schema == 1


def test_pr_flow_with_a_single_allowed_method(tmp_path):
    data = build_profile(detection(tmp_path), BASE_ANSWERS)
    assert data["flow"] == {"mode": "pr", "merge_authorized_by": "human"}
    assert data["pr"] == {"opened_by": "sender", "merged_by": "sender", "merge_method": "squash"}


def test_tiers_from_detection_and_typed_commands(tmp_path):
    data = build_profile(detection(tmp_path), {**BASE_ANSWERS, "tiers": ["python", "make e2e"]})
    assert data["tests"]["tier"] == [
        {"name": "python", "command": "uv run pytest", "required_for": ["handover", "push"]},
        {"name": "custom-1", "command": "make e2e", "required_for": ["handover", "push"]},
    ]


def test_github_ci_takes_the_detected_jobs(tmp_path):
    data = build_profile(detection(tmp_path), BASE_ANSWERS)
    assert data["ci"] == {"provider": "github", "runs_on": "cloud", "required_jobs": ["test", "lint"],
                          "jobs_source": "last-push-run", "workflow_fingerprint": "sha256:abc"}


def test_local_flow_has_no_pr_section(tmp_path):
    answers = {k: v for k, v in BASE_ANSWERS.items() if k not in ("flow", "merge_auth")}
    data = build_profile(detection(tmp_path, remote=False, ci={"provider": "none"}), {**answers, "ci": "none"})
    assert data["flow"] == {"mode": "local"} and "pr" not in data and data["ci"] == {"provider": "none"}


def test_guards_and_tracker(tmp_path):
    data = build_profile(detection(tmp_path), {**BASE_ANSWERS, "tracker": "github-issues"})
    assert data["guards"] == {"revert": True, "line_edit": False, "push_receipt": True}
    assert data["evidence"]["close"] == {"field": "ref", "pattern": "^#\\d+$", "required": False}


def test_typed_tracker_pattern_is_kept(tmp_path):
    data = build_profile(detection(tmp_path), {**BASE_ANSWERS, "tracker": "^CURVE-\\d+(\\.\\d+)*$"})
    assert data["evidence"]["close"]["pattern"] == "^CURVE-\\d+(\\.\\d+)*$"


def test_deployment_adds_the_judge_and_a_deploy_section(tmp_path):
    data = build_profile(detection(tmp_path), {**BASE_ANSWERS, "deploy": "web"})
    assert data["fleet"]["default"] == {"main": 1, "review": 1, "judge": 1}
    assert data["deploy"] == {"surface": "web", "revision_command": ""}
    assert data["judge"] == {"required": False}


def test_existing_profile_is_refused(tmp_path):
    write_profile(tmp_path, build_profile(detection(tmp_path), BASE_ANSWERS))
    with pytest.raises(ProfileExists, match="project.toml"):
        write_profile(tmp_path, build_profile(detection(tmp_path), BASE_ANSWERS))


def test_force_overwrites(tmp_path):
    write_profile(tmp_path, build_profile(detection(tmp_path), BASE_ANSWERS))
    write_profile(tmp_path, build_profile(detection(tmp_path), {**BASE_ANSWERS, "review": "none"}), force=True)
    written = tomllib.loads((tmp_path / ".flotilla" / "project.toml").read_text(encoding="utf-8"))
    assert written["review"] == {"depth": "none"}
