import json
import subprocess

from flotilla.onboard import detect_ci as ci

WORKFLOW = """name: ci
on:
  push:
    branches: [main]
jobs:
  # the suite
  test:
    runs-on: ubuntu-latest
    steps:
      - run: pytest
  lint:
    runs-on: ubuntu-latest
"""


def write_workflow(root, name="ci.yml", text=WORKFLOW):
    folder = root / ".github" / "workflows"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_text(text, encoding="utf-8")


def gh(run_list="[]", jobs=None, repo=None, fail=False):
    def run(argv, **kwargs):
        if fail:
            raise FileNotFoundError("gh")
        if argv[:3] == ["gh", "run", "list"]:
            return subprocess.CompletedProcess(argv, 0, stdout=run_list, stderr="")
        if argv[:3] == ["gh", "run", "view"]:
            return subprocess.CompletedProcess(argv, 0, stdout=json.dumps({"jobs": jobs or []}), stderr="")
        if argv[:3] == ["gh", "repo", "view"]:
            return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(repo or {}), stderr="")
        raise AssertionError(f"unexpected call {argv}")
    return run


def test_no_workflows_means_no_ci(tmp_path):
    assert ci.detect_ci(tmp_path, "github.com/o/app", "main", run=gh(fail=True)) == {"provider": "none"}


def test_job_ids_from_a_workflow_file():
    assert ci.job_ids_from_file(WORKFLOW) == ["test", "lint"]


def test_fingerprint_is_stable_and_follows_edits(tmp_path):
    write_workflow(tmp_path)
    first = ci.fingerprint(tmp_path, ci.workflow_files(tmp_path))
    assert first == ci.fingerprint(tmp_path, ci.workflow_files(tmp_path)) and first.startswith("sha256:")
    write_workflow(tmp_path, text=WORKFLOW + "  e2e:\n    runs-on: ubuntu-latest\n")
    assert ci.fingerprint(tmp_path, ci.workflow_files(tmp_path)) != first


def test_jobs_come_from_the_last_push_run(tmp_path):
    write_workflow(tmp_path)
    run = gh(run_list='[{"databaseId": 42}]',
             jobs=[{"name": "test (ubuntu-latest, 3.11)"}, {"name": "lint"}],
             repo={"mergeCommitAllowed": False, "squashMergeAllowed": True, "rebaseMergeAllowed": False})
    got = ci.detect_ci(tmp_path, "github.com/o/app", "main", run=run)
    assert got["jobs"] == ["test (ubuntu-latest, 3.11)", "lint"]
    assert got["jobs_source"] == "last-push-run"
    assert got["merge_methods"] == ["squash"]


def test_gh_missing_falls_back_to_files_marked_unverified(tmp_path):
    write_workflow(tmp_path)
    got = ci.detect_ci(tmp_path, "github.com/o/app", "main", run=gh(fail=True))
    assert got["jobs"] == ["test", "lint"]
    assert got["jobs_source"].startswith("workflow-files") and "unverified" in got["jobs_source"]
    assert "merge_methods" not in got


def test_no_push_run_yet_falls_back_to_files(tmp_path):
    write_workflow(tmp_path)
    got = ci.detect_ci(tmp_path, "github.com/o/app", "main", run=gh(run_list="[]"))
    assert got["jobs"] == ["test", "lint"] and "unverified" in got["jobs_source"]


def test_non_github_origin_never_calls_gh(tmp_path):
    write_workflow(tmp_path)
    def forbidden(argv, **kwargs):
        raise AssertionError("gh must not be called for a non-GitHub origin")
    got = ci.detect_ci(tmp_path, "gitlab.com/o/app", "main", run=forbidden)
    assert got["provider"] == "github" and "unverified" in got["jobs_source"]


def test_github_slug():
    assert ci.github_slug("github.com/Owner/app") == "Owner/app"
    assert ci.github_slug("gitlab.com/o/app") is None and ci.github_slug(None) is None


def test_merge_methods_unknown_when_gh_fails():
    def failing(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="HTTP 404")
    assert ci.merge_methods("o/app", run=failing) is None
