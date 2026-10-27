"""Refs, trunks and landing for the collector (gc design, sections 5.1 and 5.2): every ref holds, origin answers for
its own trunk, local flow lands on the local trunk, and a declared trunk lands its branches at its local tip."""

import subprocess

from flotilla.gc import refs
from ledgerkit import IDENTITY, PROFILE, commit, git, make_ledger, repo_with_origin


def world(tmp_path, profile=None):
    root = repo_with_origin(tmp_path)
    return root, make_ledger(root, tmp_path / "state", profile=profile or PROFILE, live=())


def test_a_commit_on_any_ref_is_held(tmp_path):
    root, ledger = world(tmp_path)
    git(root, "checkout", "-q", "-b", "side")
    sha = commit(root, "kept by the person")
    git(root, "checkout", "-q", "main")
    git(root, "update-ref", f"refs/attic/x/{sha}", sha)
    git(root, "branch", "-D", "side")
    assert refs.held(ledger, sha, refs.holders(ledger)) is True
    assert refs.held(ledger, sha, refs.holders(ledger, dropping={f"refs/attic/x/{sha}"})) is False


def test_holders_reach_git_on_stdin(tmp_path):
    root, ledger = world(tmp_path)
    head = git(root, "rev-parse", "HEAD")
    lines = "".join(f"create refs/tags/t{index} {head}\n" for index in range(3000))
    subprocess.run(["git", "-C", str(root), "update-ref", "--stdin"], input=lines, text=True, check=True)
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        return subprocess.run(args, **kwargs)
    ledger.run = run
    holders = refs.holders(ledger)
    assert len(holders) > 3000
    calls.clear()
    assert refs.held(ledger, head, holders) is True
    assert calls and all(len(call) < 20 for call in calls)


def test_local_flow_lands_on_the_local_trunk(tmp_path):
    root, ledger = world(tmp_path, {**PROFILE, "flow": {"mode": "local"}})
    git(root, "remote", "remove", "origin")
    git(root, "checkout", "-q", "-b", "feat/a")
    tip = commit(root, "work")
    git(root, "checkout", "-q", "main")
    git(root, *IDENTITY, "merge", "-q", "--no-ff", "--no-edit", "feat/a")
    trunks, _ = refs.trunks(ledger)
    assert refs.landing(ledger, tip, trunks, set()) == refs.Landing("trunk", "main", True)


def test_origin_flow_lands_on_origins_answer_not_the_local_ref(tmp_path):
    root, ledger = world(tmp_path)
    tip = commit(root, "not pushed")
    git(root, "update-ref", "refs/remotes/origin/main", tip)
    trunks, _ = refs.trunks(ledger)
    assert refs.landing(ledger, tip, trunks, set()) is None
    git(root, "push", "-q", "origin", "HEAD:main")
    trunks, _ = refs.trunks(ledger)
    assert refs.landing(ledger, tip, trunks, set()) == refs.Landing("trunk", "main", False)


def test_an_extra_trunk_lands_its_branches_at_its_local_tip(tmp_path):
    root, ledger = world(tmp_path, {**PROFILE, "gc": {"trunks": ["proto/x"]}})
    git(root, "checkout", "-q", "-b", "proto/x")
    git(root, "push", "-q", "origin", "proto/x")
    git(root, "checkout", "-q", "-b", "feat/b")
    tip = commit(root, "work on the line")
    git(root, "checkout", "-q", "proto/x")
    git(root, *IDENTITY, "merge", "-q", "--no-ff", "--no-edit", "feat/b")
    trunks, _ = refs.trunks(ledger)
    assert refs.landing(ledger, tip, trunks, set()) == refs.Landing("trunk", "proto/x", True)
    git(root, "push", "-q", "origin", "proto/x")
    trunks, _ = refs.trunks(ledger)
    assert refs.landing(ledger, tip, trunks, set()) == refs.Landing("trunk", "proto/x", False)


def test_a_squash_shipped_tip_lands_and_is_parked(tmp_path):
    root, ledger = world(tmp_path)
    tip = commit(root, "squashed elsewhere")
    assert refs.landing(ledger, tip, [], {tip}) == refs.Landing("shipped", "", True)


def test_unheld_keeps_only_chain_tips(tmp_path):
    root, ledger = world(tmp_path)
    git(root, "checkout", "-q", "-b", "gone")
    first, second, third = commit(root, "1"), commit(root, "2"), commit(root, "3")
    git(root, "checkout", "-q", "main")
    git(root, "branch", "-D", "gone")
    assert refs.unheld(ledger, [first, second, third], refs.holders(ledger)) == [third]


def test_origin_that_cannot_be_asked_is_named(tmp_path):
    root, ledger = world(tmp_path)
    git(root, "remote", "set-url", "origin", str(tmp_path / "nowhere.git"))
    trunks, warnings = refs.trunks(ledger)
    assert trunks == []
    assert any("origin could not be asked" in line for line in warnings)


def test_an_origin_on_this_disk_is_named(tmp_path):
    root, ledger = world(tmp_path)
    _, warnings = refs.trunks(ledger)
    assert any("no copy off this disk" in line for line in warnings)
