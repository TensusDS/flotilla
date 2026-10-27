"""Parking (gc design, section 5.4): a commit about to lose its last holder gets a ref of its own, under a name git
accepts whatever the tree or branch was called, and parking it twice is one ref."""

import subprocess

import pytest

from flotilla.gc import park
from ledgerkit import PROFILE, commit, git, make_ledger, repo_with_origin


def world(tmp_path):
    root = repo_with_origin(tmp_path)
    return root, make_ledger(root, tmp_path / "state", profile=PROFILE, live=())


def test_a_parked_commit_survives_its_branch(tmp_path):
    root, ledger = world(tmp_path)
    git(root, "checkout", "-q", "-b", "feat/a")
    tip = commit(root, "work")
    git(root, "checkout", "-q", "main")
    names = park.park(ledger, "feat/a", [tip])
    git(root, "branch", "-D", "feat/a")
    git(root, "cat-file", "-e", f"{tip}^{{commit}}")
    assert git(root, "for-each-ref", "--format=%(refname) %(objectname)", "refs/flotilla/parked/") == \
        f"{names[0]} {tip}"


@pytest.mark.parametrize("owner", ["a..b", "x y", "t.lock", "@{x", ".hidden", "/", "feat/a//b", "-x"])
def test_names_git_would_refuse_are_made_safe(tmp_path, owner):
    root, ledger = world(tmp_path)
    tip = git(root, "rev-parse", "HEAD")
    name = park.ref_name(owner, tip)
    assert name.startswith(park.PARKED) and name.endswith(f"/{tip}")
    assert subprocess.run(["git", "check-ref-format", name]).returncode == 0
    assert park.park(ledger, owner, [tip]) == [name]
    assert git(root, "rev-parse", name) == tip


def test_parking_twice_is_one_ref(tmp_path):
    root, ledger = world(tmp_path)
    tip = git(root, "rev-parse", "HEAD")
    assert park.park(ledger, "app-main-1", [tip]) == park.park(ledger, "app-main-1", [tip])
    assert len(git(root, "for-each-ref", "refs/flotilla/parked/").splitlines()) == 1
