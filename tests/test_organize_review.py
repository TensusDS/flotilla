"""The final whole-branch review of feat/organize (67a497f..7a44d0c): one test per finding taken into the fix pass,
each seen red against the code before its fix."""

import subprocess
from pathlib import Path

import pytest

from flotilla.organize import local, questions, split
from flotilla.organize import walk as walking
from ledgerkit import git, repo_with_origin
from organizekit import MERGE_PROFILE, Hub, project, pull
from test_organize_apply import edits_world, onboard_world, world  # noqa: F401 - fixtures


def refs_holding(root, sha):
    out = subprocess.run(["git", "-C", str(root), "for-each-ref", "--contains", sha, "--format=%(refname)"],
                         capture_output=True, text=True).stdout
    return out.split()


def mine(root, message, name, text=None):
    path = Path(root) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text((text or message) + "\n", encoding="utf-8")
    git(root, "add", name)
    git(root, "commit", "-q", "-m", message)
    return git(root, "rev-parse", "HEAD")


def local_trunk_ahead(w):
    git(w.root, "checkout", "-q", "main")
    lost = mine(w.root, "local only on main", "local.txt")
    git(w.root, "checkout", "-q", "chore/flotilla-onboard")
    return lost


# 1 (Critical) - onboarding's branch answer moved trunk past local commits, and said trunk was left as is

def test_onboard_never_moves_trunk_past_its_own_local_commits(tmp_path, monkeypatch):
    w = onboard_world(tmp_path, monkeypatch)
    lost = local_trunk_ahead(w)
    w.answer("onboard:chore/flotilla-onboard", "land")
    w.answer("trunk:main", "leave")
    lines, complete = w.apply()
    assert git(w.root, "rev-parse", "main") == lost
    assert "refs/heads/main" in refs_holding(w.root, lost)
    assert not complete and any("onboard" in line and "local main" in line for line in lines)


def test_settling_the_local_trunk_first_lets_the_profile_land(tmp_path, monkeypatch):
    w = onboard_world(tmp_path, monkeypatch)
    lost = local_trunk_ahead(w)
    w.answer("onboard:chore/flotilla-onboard", "land")
    w.answer("trunk:main", "move")
    lines, complete = w.apply()
    assert complete, lines
    assert refs_holding(w.root, lost)                                  # on its own branch now
    assert git(w.root, "rev-parse", "main") == w.extra                 # the profile landed after trunk was settled
    assert git(w.root, "symbolic-ref", "--short", "HEAD") == "main"
    assert (Path(w.root) / ".flotilla" / "project.toml").exists()


# 2 - "I say what goes where" for edits was a dead end

@pytest.mark.parametrize("words", [["words"], ["fix.py", "goes", "to", "fix/a"]])
def test_edits_in_the_persons_words_go_through_a_plan(world, words):
    w = edits_world(world)
    key = f"edits:{w.root}"
    w.answer(key, *words)
    assert any("--plan" in line for line in questions.waiting(w.walk(), w.data))
    w.data["proposals"][key] = {"plan": [{"branch": "fix/a", "paths": ["fix.py"]}]}
    assert [q["id"] for q in questions.all_questions(w.walk(), w.data)] == [f"{key}:plan"]
    w.answer(f"{key}:plan", "yes")
    lines, complete = w.apply()
    assert complete, lines
    assert git(w.root, "show", "fix/a:fix.py") == "fixed"


# 3 - nothing showed carry_out stops when a committed blob differs from the tree's file

def test_a_hook_that_rewrites_a_file_stops_the_split_with_nothing_removed(tmp_path):
    root = repo_with_origin(tmp_path)
    git(root, "config", "user.email", "me@example.invalid")
    git(root, "config", "user.name", "me")
    (root / "fix.py").write_text("fixed\n", encoding="utf-8")
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    (hooks / "pre-commit").write_text("#!/bin/sh\necho rewritten > fix.py\ngit add fix.py\n", encoding="utf-8")
    (hooks / "pre-commit").chmod(0o755)
    git(root, "config", "core.hooksPath", str(hooks))
    with pytest.raises(split.Stopped, match="does not hold"):
        split.carry_out(root, root, [{"branch": "fix/a", "paths": ["fix.py"]}],
                        start=git(root, "rev-parse", "HEAD"), state=tmp_path / "state")
    assert (root / "fix.py").read_text(encoding="utf-8") == "fixed\n"


# 4 - a staged rename was carried as a copy, leaving a stray deletion

def test_a_staged_rename_moves_whole(tmp_path):
    root = repo_with_origin(tmp_path)
    git(root, "config", "user.email", "me@example.invalid")
    git(root, "config", "user.name", "me")
    mine(root, "old", "old.txt", "content")
    git(root, "mv", "old.txt", "new.txt")
    dirty = local.dirty(root)
    assert dirty == ["new.txt", "old.txt"]
    split.carry_out(root, root, [{"branch": "fix/r", "paths": dirty}], start=git(root, "rev-parse", "HEAD"),
                    state=tmp_path / "state")
    assert git(root, "ls-tree", "--name-only", "fix/r").split() == ["README.md", "new.txt"]
    assert git(root, "status", "--porcelain") == ""


# 5 - a profile commit left on local trunk was offered as the person's own commits

def test_a_profile_commit_on_local_trunk_is_not_offered_to_move_or_drop(tmp_path):
    root = project(tmp_path, published=False)
    mine(root, "chore: onboard flotilla", ".flotilla/project.toml", 'schema = 1\n[trunk]\nbranch = "main"')
    found = walking.walk(Path(root), rows={}, profile=MERGE_PROFILE, run=Hub().run, slug="o/r")
    assert "trunk:main" not in [item.key for item in found.items]
    mine(root, "my own", "own.txt")
    found = walking.walk(Path(root), rows={}, profile=MERGE_PROFILE, run=Hub().run, slug="o/r")
    assert "trunk:main" not in [item.key for item in found.items]
    assert any("profile" in note and "beside" in note for note in found.notes)


# 6 - the person's pull request read as another AI's when trunk gained AI files later

def test_ai_files_trunk_gained_later_are_no_sign_on_a_pull_request(tmp_path):
    root = project(tmp_path)
    git(root, "checkout", "-q", "-b", "feat/mine")
    head = mine(root, "my work", "mine.txt")
    git(root, "push", "-q", "origin", "feat/mine")
    git(root, "checkout", "-q", "main")
    mine(root, "add codex config on trunk", ".codex/config.toml", "x")
    git(root, "push", "-q", "origin", "main")
    found = walking.walk(Path(root), rows={}, profile=MERGE_PROFILE, run=Hub(pulls=[pull(42, "feat/mine", "main", head)]).run,
                         slug="o/r")
    assert [item.key for item in found.items] == ["pr:42"]


# 7b - the conflicts onboarding's extra commits bring against open pull requests were not named

def test_commits_beside_the_profile_name_the_pull_requests_they_conflict_with(tmp_path, monkeypatch):
    w = onboard_world(tmp_path, monkeypatch)
    git(w.root, "checkout", "-q", "-b", "docs/a", "origin/main")
    head = mine(w.root, "docs a", "t053.md", "a different T-053")
    git(w.root, "push", "-q", "origin", "docs/a")
    git(w.root, "checkout", "-q", "chore/flotilla-onboard")
    w.hub.pulls = {42: pull(42, "docs/a", "main", head)}
    item = next(item for item in w.walk().items if item.kind == "onboard")
    assert any("conflicts with #42" in line for line in item.lines)


# 9 - consent text said nothing of what changes on GitHub

def test_the_shape_answers_say_what_changes_on_github():
    for shape in ("separate", "parts", "whole"):
        assert "on GitHub" in questions.SHAPE_TEXT[shape][1], shape


# 11 - a failed keep was taken for "already kept" and a destructive command printed anyway

def test_a_keep_that_fails_for_another_reason_prints_no_drop_command(world, monkeypatch):
    from flotilla.organize import keep
    w = world(names=())
    git(w.root, "checkout", "-q", "main")
    mine(w.root, "local only", "local.txt")

    def refuses(*args, **kwargs):
        raise RuntimeError("cannot lock ref")
    monkeypatch.setattr(keep, "create_ref", refuses)
    w.answer("trunk:main", "drop")
    lines, complete = w.apply()
    assert not complete and not any("! git" in line for line in lines)
