from flotilla.onboard.check import check_drift

PROFILE = {
    "trunk": {"branch": "main"},
    "tests": {"tier": [{"name": "unit", "command": "uv run pytest", "required_for": ["push"]}]},
    "ci": {"provider": "github", "required_jobs": ["test", "lint"], "workflow_fingerprint": "sha256:a"},
}


def detection(**ci_overrides):
    ci = {"provider": "github", "fingerprint": "sha256:a", "jobs": ["test", "lint"], "jobs_source": "last-push-run"}
    ci.update(ci_overrides)
    return {"trunk": "main", "ci": ci}


def test_nothing_drifted():
    assert check_drift(PROFILE, detection(), {"unit": 4.2}) == []


def test_workflow_files_changed():
    assert any("workflow files changed" in f for f in check_drift(PROFILE, detection(fingerprint="sha256:b"), {"unit": 1}))


def test_a_new_job_ran_that_is_not_required():
    found = check_drift(PROFILE, detection(jobs=["test", "lint", "e2e"]), {"unit": 1})
    assert found == ["CI: job `e2e` ran on the last push but is not required"]


def test_a_required_job_did_not_run():
    found = check_drift(PROFILE, detection(jobs=["test"]), {"unit": 1})
    assert found == ["CI: required job `lint` did not run on the last push"]


def test_trunk_renamed():
    det = {**detection(), "trunk": "trunk"}
    assert "trunk: the profile says `main`, the repository says `trunk`" in check_drift(PROFILE, det, {"unit": 1})


def test_a_tier_never_green_on_this_machine():
    assert check_drift(PROFILE, detection(), {}) == ["tests: tier `unit` has never run green on this machine"]


def test_unverifiable_jobs_are_unknown_not_matching():
    found = check_drift(PROFILE, detection(jobs_source="workflow-files (unverified)"), {"unit": 1})
    assert found == ["unknown: CI required jobs not verified (gh unavailable or no push run on trunk yet)"]


def test_exit_code_separates_drift_from_unknown():
    from flotilla.onboard.check import exit_code
    assert exit_code([]) == 0
    assert exit_code(["unknown: x"]) == 3
    assert exit_code(["unknown: x", "trunk: y"]) == 1
