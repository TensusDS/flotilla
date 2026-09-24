import json
import subprocess

import pytest

from flotilla.onboard import detect_repo as dr


def git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def commit(cwd, message):
    git(cwd, "-c", "user.email=t@example.invalid", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", message)


@pytest.fixture()
def repo(tmp_path):
    root = tmp_path / "app"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    commit(root, "feat: start")
    return root


def test_no_remote(repo):
    assert dr.has_remote(repo) is False


def test_remote(repo):
    git(repo, "remote", "add", "origin", "git@github.com:o/app.git")
    assert dr.has_remote(repo) is True


def test_trunk_from_origin_head(repo):
    git(repo, "update-ref", "refs/remotes/origin/develop", "HEAD")
    git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/develop")
    assert dr.trunk_branch(repo) == "develop"


def test_trunk_prefers_main_over_the_current_feature_branch(repo):
    git(repo, "checkout", "-q", "-b", "feat/x")
    assert dr.trunk_branch(repo) == "main"


def test_trunk_falls_back_to_master(tmp_path):
    root = tmp_path / "old"
    root.mkdir()
    git(root, "init", "-q", "-b", "master")
    commit(root, "init")
    git(root, "checkout", "-q", "-b", "feat/y")
    assert dr.trunk_branch(root) == "master"


def test_release_info_reads_the_latest_tag_and_version_files(repo):
    git(repo, "tag", "v1.2.0")
    commit(repo, "fix: later")
    git(repo, "tag", "v1.10.0")
    (repo / "pyproject.toml").write_text('[project]\nname = "app"\nversion = "1.10.0"\n', encoding="utf-8")
    (repo / "package.json").write_text(json.dumps({"name": "app"}), encoding="utf-8")
    info = dr.release_info(repo)
    assert info["latest_tag"] == "v1.10.0"
    assert info["version_files"] == ["pyproject.toml"]


def test_release_info_ignores_malformed_files(repo):
    (repo / "pyproject.toml").write_text("[project\n", encoding="utf-8")
    (repo / "package.json").write_text("{not json", encoding="utf-8")
    assert dr.release_info(repo) == {"version_files": []}


def test_commit_convention_conventional(repo):
    for n in range(4):
        commit(repo, f"fix: thing {n}")
    assert dr.commit_convention(repo) == "conventional"


def test_commit_convention_free(repo):
    for n in range(4):
        commit(repo, f"Update thing {n}")
    assert dr.commit_convention(repo) == "free"


def test_commit_convention_unknown_without_commits(tmp_path):
    root = tmp_path / "empty"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    assert dr.commit_convention(root) == "unknown"
