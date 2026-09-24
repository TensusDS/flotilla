import json
import subprocess

from flotilla.onboard.detect import detect
from flotilla.onboard.detect_signals import detect_signals


def git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def test_sibling_repositories_from_uv_lock_and_package_json(tmp_path):
    (tmp_path / "uv.lock").write_text(
        'source = { editable = "../core" }\nsource = { path = "../shared" }\nsource = { editable = "." }\n',
        encoding="utf-8")
    (tmp_path / "package.json").write_text(json.dumps({"dependencies": {"ui": "file:../ui", "x": "^1"}}),
                                           encoding="utf-8")
    assert detect_signals(tmp_path)["multi_repo"] == ["../core", "../shared", "../ui"]


def test_deployment_signals(tmp_path):
    (tmp_path / "deploy").mkdir()
    (tmp_path / "app.service").write_text("[Unit]\n", encoding="utf-8")
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"dev": "next dev"}}), encoding="utf-8")
    assert detect_signals(tmp_path)["deployment"] == ["deploy", "app.service", "package.json scripts.dev"]


def test_shared_and_sequential_signals(tmp_path):
    (tmp_path / "CHANGELOG.md").write_text("", encoding="utf-8")
    (tmp_path / "alembic" / "versions").mkdir(parents=True)
    signals = detect_signals(tmp_path)
    assert signals["shared_files"] == ["CHANGELOG.md"] and signals["sequential"] == ["alembic/versions"]


def test_empty_directory_has_no_signals(tmp_path):
    assert detect_signals(tmp_path) == {"multi_repo": [], "deployment": [], "shared_files": [], "sequential": []}


def test_whole_detection_on_a_repository(tmp_path):
    root = tmp_path / "app"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "remote", "add", "origin", "git@gitlab.com:o/app.git")
    (root / "pyproject.toml").write_text("[tool.pytest.ini_options]\n", encoding="utf-8")
    (root / "uv.lock").write_text("", encoding="utf-8")
    workflows = root / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "ci.yml").write_text("on: push\njobs:\n  test:\n    runs-on: x\n", encoding="utf-8")
    got = detect(root)
    assert got["remote"] is True and got["origin"] == "gitlab.com/o/app" and got["trunk"] == "main"
    assert [t["name"] for t in got["tests"]] == ["python"]
    assert got["ci"]["jobs"] == ["test"] and "unverified" in got["ci"]["jobs_source"]
    assert set(got) == {"root", "repo_key", "origin", "remote", "trunk", "tests", "notes", "ci", "release",
                        "commit_convention", "signals"}


def test_repository_without_remote(tmp_path):
    root = tmp_path / "lone"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    got = detect(root)
    assert got["remote"] is False and got["origin"] == "" and got["ci"] == {"provider": "none"}
