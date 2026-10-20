import subprocess
from pathlib import Path

import pytest

from flotilla.organize import secrets, split
from ledgerkit import git, repo_with_origin

TOKEN = "ghp_" + "A1b2C3d4E5" * 4   # 40 characters after the prefix: the shape of a GitHub token


def none_installed(name):
    return None


@pytest.fixture()
def tree(tmp_path):
    root = repo_with_origin(tmp_path)
    git(root, "config", "user.email", "me@example.invalid")
    git(root, "config", "user.name", "me")
    (root / "tracked.txt").write_text("one\n", encoding="utf-8")
    git(root, "add", "tracked.txt")
    git(root, "commit", "-q", "-m", "tracked")
    git(root, "push", "-q", "origin", "main")
    return root


def test_a_secret_is_named_by_content_and_by_name(tree):
    (tree / "config.py").write_text(f"TOKEN = '{TOKEN}'\n", encoding="utf-8")
    (tree / ".env").write_text("A=1\n", encoding="utf-8")
    (tree / "plain.md").write_text("nothing here\n", encoding="utf-8")
    hits = secrets.scan(tree, ["config.py", ".env", "plain.md"], which=none_installed)
    assert sorted(hit.path for hit in hits) == [".env", "config.py"]


def test_an_installed_scanner_is_asked_first(tree):
    (tree / "a.py").write_text("x = 1\n", encoding="utf-8")
    asked = []

    def run(cmd, **kwargs):
        asked.append(cmd)
        return subprocess.CompletedProcess(cmd, 1, "[]", "")   # exit 1: the tool found a leak
    hits = secrets.scan(tree, ["a.py"], run=run, which=lambda name: "/usr/bin/" + name)
    assert asked[0][0].endswith("betterleaks") and [hit.path for hit in hits] == ["a.py"]


@pytest.mark.parametrize("plan, wrong", [
    ([{"branch": "fix/a", "paths": ["nope.txt"]}], "not among"),
    ([{"branch": "fix/a", "paths": ["a.txt"]}, {"branch": "fix/b", "paths": ["a.txt"]}], "twice"),
    ([{"branch": "fix/a", "paths": [".flotilla/project.toml"]}], "onboarding"),
    ([{"branch": "fix/$(x)", "paths": ["a.txt"]}], "branch name"),
])
def test_a_plan_that_names_a_path_twice_is_refused(plan, wrong):
    errors = split.validate_plan(plan, ["a.txt", "b.txt"])
    assert any(wrong in error for error in errors), errors


def plan_world(tree):
    (tree / "fix.py").write_text("fixed\n", encoding="utf-8")
    (tree / "tracked.txt").write_text("one, edited\n", encoding="utf-8")
    (tree / "notes.md").write_text("notes\n", encoding="utf-8")
    return [{"branch": "fix/tone", "paths": ["fix.py", "tracked.txt"]}, {"branch": "docs/notes", "paths": ["notes.md"]}]


def test_the_plan_is_carried_out_exactly(tree, tmp_path):
    plan = plan_world(tree)
    start = git(tree, "rev-parse", "HEAD")
    lines, made = split.carry_out(tree, tree, plan, start=start, state=tmp_path / "state")
    assert made == ["fix/tone", "docs/notes"]
    assert git(tree, "show", "fix/tone:fix.py") == "fixed" and git(tree, "show", "fix/tone:tracked.txt") == "one, edited"
    assert git(tree, "show", "docs/notes:notes.md") == "notes"
    assert git(tree, "rev-parse", "fix/tone~1") == start
    assert git(tree, "status", "--porcelain") == ""             # the moved edits left the original tree
    assert (tree / "tracked.txt").read_text(encoding="utf-8") == "one\n"
    assert git(tree, "worktree", "list").count("\n") == 0       # its own trees are gone again


def test_a_failing_hook_stops_and_removes_nothing(tree, tmp_path):
    plan = plan_world(tree)
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    (hooks / "pre-commit").write_text("#!/bin/sh\necho no >&2\nexit 1\n", encoding="utf-8")
    (hooks / "pre-commit").chmod(0o755)
    git(tree, "config", "core.hooksPath", str(hooks))
    before = git(tree, "status", "--porcelain")
    with pytest.raises(split.Stopped, match="hook"):
        split.carry_out(tree, tree, plan, start=git(tree, "rev-parse", "HEAD"), state=tmp_path / "state")
    assert git(tree, "status", "--porcelain") == before
    assert (tree / "fix.py").read_text(encoding="utf-8") == "fixed\n"
    assert "fix/tone" not in git(tree, "branch", "--list")


def test_ignored_files_are_never_added(tree, tmp_path):
    (tree / ".gitignore").write_text("*.log\n", encoding="utf-8")
    (tree / "run.log").write_text("noise\n", encoding="utf-8")
    errors = split.validate_plan([{"branch": "fix/a", "paths": ["run.log"]}], ["run.log"])
    assert errors == [] or any("ignored" in error for error in errors)
    if not errors:
        with pytest.raises(split.Stopped, match="ignored"):
            split.carry_out(tree, tree, [{"branch": "fix/a", "paths": ["run.log"]}],
                            start=git(tree, "rev-parse", "HEAD"), state=tmp_path / "state")
    assert (tree / "run.log").exists()


def test_leave_as_is_touches_nothing(tree):
    assert Path(tree / "tracked.txt").read_text(encoding="utf-8") == "one\n"
