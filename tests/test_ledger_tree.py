import pytest

from flotilla.ledger import core, gitq
from flotilla.ledger import tree as tree_mod
from flotilla.ledger.errors import MoveRefused
from ledgerkit import actor, branch, git, make_ledger, repo_with_origin


def test_cut_makes_a_tree_from_origin_trunk_and_claims_it(tmp_path):
    root = repo_with_origin(tmp_path)
    ledger = make_ledger(root, tmp_path / "state")
    target = tmp_path / "app-main-1"
    row = tree_mod.cut(ledger, actor(ledger, "main session 1"), "feat/x", target, ref="LIN-1")
    assert (target / "README.md").is_file()
    assert (row.branch, row.tree, row.ref) == ("feat/x", str(target.resolve()), "LIN-1")
    assert row.base == git(root, "rev-parse", "origin/main")


def test_a_wrong_repository_is_refused_before_anything_is_cut(tmp_path):
    root = repo_with_origin(tmp_path)
    ledger = make_ledger(root, tmp_path / "state")
    target = tmp_path / "app-main-1"
    with pytest.raises(MoveRefused, match="nothing was cut"):
        tree_mod.cut(ledger, actor(ledger, "main session 1"), "feat/x", target, expect="other-repo")
    assert not target.exists() and gitq.branch_tip(root, "feat/x") is None


def test_an_existing_path_is_refused(tmp_path):
    root = repo_with_origin(tmp_path)
    ledger = make_ledger(root, tmp_path / "state")
    target = tmp_path / "taken"
    target.mkdir()
    with pytest.raises(MoveRefused, match="already exists"):
        tree_mod.cut(ledger, actor(ledger, "main session 1"), "feat/x", target)


def test_an_existing_branch_is_refused(tmp_path):
    root = repo_with_origin(tmp_path)
    ledger = make_ledger(root, tmp_path / "state")
    branch(root, "feat/x", "one")
    with pytest.raises(MoveRefused, match="already exists"):
        tree_mod.cut(ledger, actor(ledger, "main session 1"), "feat/x", tmp_path / "t")


def test_a_duplicate_ref_is_refused_before_cutting(tmp_path):
    root = repo_with_origin(tmp_path)
    ledger = make_ledger(root, tmp_path / "state")
    core.claim(ledger, actor(ledger, "main session 1"), "feat/a", ref="LIN-1")
    target = tmp_path / "t"
    with pytest.raises(MoveRefused, match="LIN-1"):
        tree_mod.cut(ledger, actor(ledger, "minor session 1"), "feat/b", target, ref="LIN-1")
    assert not target.exists() and gitq.branch_tip(root, "feat/b") is None
