"""Ignored files, rule 1 of the gc design (section 6): a name list applied to the entry itself, a directory judged by
what is in it (the field bug: `tools/crystal3d/` held only `__pycache__`), evidence beside an entry, and Claude
Code's runtime state - all but the permissions a person granted."""

import subprocess

from flotilla.gc import ignored
from ledgerkit import commit, git, repo_with_origin


def tree_with(tmp_path, ignore: str):
    root = repo_with_origin(tmp_path)
    commit(root, "ignore", ".gitignore", ignore)
    tree = tmp_path / "app-main-1"
    git(root, "worktree", "add", "-q", "-b", "fleet/main-1", str(tree))
    return root, tree


def put(base, path, text="x"):
    target = base / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def holding(root, tree, profile=None):
    return ignored.holding(tree, main=root, profile=profile or {}, run=subprocess.run)


def test_a_directory_holding_only_caches_does_not_hold_the_tree(tmp_path):
    root, tree = tree_with(tmp_path, "tools/crystal3d/\n")
    put(tree, "tools/crystal3d/__pycache__/a.pyc")
    assert holding(root, tree) == []


def test_a_directory_with_one_real_file_holds_it(tmp_path):
    root, tree = tree_with(tmp_path, "tools/crystal3d/\n")
    put(tree, "tools/crystal3d/__pycache__/a.pyc")
    put(tree, "tools/crystal3d/model.bin")
    assert holding(root, tree) == ["tools/crystal3d/"]


def test_evidence_beside_a_directory_makes_it_regenerable(tmp_path):
    root, tree = tree_with(tmp_path, "vendor/\n")
    put(tree, "vendor/lib/a.php")
    assert holding(root, tree) == ["vendor/"]
    put(tree, "composer.lock", "{}")
    git(tree, "add", "composer.lock")
    assert holding(root, tree) == []


def test_claude_runtime_state_is_regenerable_but_granted_permissions_are_not(tmp_path):
    root, tree = tree_with(tmp_path, ".claude/\n")
    put(tree, ".claude/scheduled_tasks.json")
    assert holding(root, tree) == []
    put(tree, ".claude/settings.local.json", '{"permissions": {"allow": ["Bash(make)"]}}')
    assert holding(root, tree) == [".claude/"]
    put(root, ".claude/settings.local.json", '{"permissions": {"allow": ["Bash(make)"]}}')
    assert holding(root, tree) == []


def test_the_profile_extends_the_names(tmp_path):
    root, tree = tree_with(tmp_path, "teaser-cache/\n")
    put(tree, "teaser-cache/a.bin")
    assert holding(root, tree) == ["teaser-cache/"]
    assert holding(root, tree, {"gc": {"regenerable": ["teaser-cache"]}}) == []


def test_a_parent_name_does_not_vouch_for_a_file(tmp_path):
    root, tree = tree_with(tmp_path, "*.env\n")
    put(tree, "deploy/build/prod.env")
    assert holding(root, tree) == ["deploy/build/prod.env"]


def test_a_regenerable_entry_is_not_walked(tmp_path, monkeypatch):
    root, tree = tree_with(tmp_path, "node_modules/\n")
    put(tree, "node_modules/x/index.js")
    monkeypatch.setattr(ignored.os, "walk", lambda *a, **k: (_ for _ in ()).throw(AssertionError("walked")))
    assert holding(root, tree) == []


def test_a_nested_repository_under_an_ignored_path_holds_the_tree(tmp_path):
    """Security review of gc stage 1; gc design 6, rule 5: a `.git` under an ignored path is a nested repository."""
    root, tree = tree_with(tmp_path, "node_modules/\nwork/\n")
    put(tree, "work/__pycache__/a.pyc")
    git(tree / "work", "init", "-q")
    assert holding(root, tree) == ["work/"]



def test_only_named_runtime_state_under_claude_is_regenerable(tmp_path):
    """Final review of gc stage 1, C1: everything under an ignored `.claude/` passed, a plan another AI wrote among
    it; only Claude Code's own runtime state does (the eleven paths a field project excludes)."""
    root, tree = tree_with(tmp_path, ".claude/\n")
    put(tree, ".claude/plans/plan.md")
    assert holding(root, tree) == [".claude/"]


def test_a_cache_name_inside_a_folded_directory_does_not_vouch_for_a_secret(tmp_path):
    """Final review of gc stage 1, C2: inside a walked directory `build/` vouched for `deploy/build/prod.env` - the
    I1 hole again. Within a walk only suffixes, true caches and an install beside its manifest vouch."""
    root, tree = tree_with(tmp_path, "deploy/\n")
    put(tree, "deploy/build/prod.env")
    assert holding(root, tree) == ["deploy/"]
    (tmp_path / "second").mkdir()
    root2, tree2 = tree_with(tmp_path / "second", "tools/\n")
    put(tree2, "tools/crystal3d/__pycache__/a.cpython-312.pyc")
    assert holding(root2, tree2) == []
    put(tree2, "tools/renders/dist/final.mp4")
    assert holding(root2, tree2) == ["tools/"]


def test_a_regenerable_entry_that_is_a_repository_holds_the_tree(tmp_path):
    """gc design 6, rule 5: a nested repository is the person's, whatever its directory is called."""
    root, tree = tree_with(tmp_path, "build/\n")
    put(tree, "build/x.o")
    git(tree / "build", "init", "-q")
    assert holding(root, tree) == ["build/"]
