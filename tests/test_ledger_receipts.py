import sys

import pytest

from flotilla.ledger import receipts
from ledgerkit import commit, git, repo_with_origin

KEY = "k-000000000000"
GREEN = f"{sys.executable} -c \"print('2 passed')\""


def profile(command=None):
    if command is None:
        return {"schema": 1}
    return {"schema": 1, "tests": {"tier": [{"name": "unit", "command": command, "required_for": ["handover", "push"]}]}}


def test_no_tiers_configured_is_valid_and_says_so(tmp_path):
    ok, why = receipts.check_receipt(state=tmp_path / "s", repo_key=KEY, sha="a" * 40, purpose="handover",
                                     profile=profile())
    assert ok and "no handover tiers" in why


def test_a_green_run_is_a_valid_receipt(tmp_path):
    root = repo_with_origin(tmp_path)
    sha = git(root, "rev-parse", "HEAD")
    receipts.run_receipt(root, state=tmp_path / "s", repo_key=KEY, purpose="handover", profile=profile(GREEN), timeout=60)
    ok, why = receipts.check_receipt(state=tmp_path / "s", repo_key=KEY, sha=sha, purpose="handover",
                                     profile=profile(GREEN))
    assert ok and sha[:7] in why


def test_a_dirty_tree_gets_no_receipt(tmp_path):
    root = repo_with_origin(tmp_path)
    (root / "wip.txt").write_text("x", encoding="utf-8")
    with pytest.raises(receipts.ReceiptRefused, match="uncommitted"):
        receipts.run_receipt(root, state=tmp_path / "s", repo_key=KEY, purpose="handover", profile=profile(GREEN),
                             timeout=60)


def test_a_red_tier_is_named(tmp_path):
    root = repo_with_origin(tmp_path)
    sha = git(root, "rev-parse", "HEAD")
    red = profile("exit 1")
    receipts.run_receipt(root, state=tmp_path / "s", repo_key=KEY, purpose="handover", profile=red, timeout=60)
    ok, why = receipts.check_receipt(state=tmp_path / "s", repo_key=KEY, sha=sha, purpose="handover", profile=red)
    assert not ok and "unit" in why


def test_a_changed_tier_command_voids_the_receipt(tmp_path):
    root = repo_with_origin(tmp_path)
    sha = git(root, "rev-parse", "HEAD")
    receipts.run_receipt(root, state=tmp_path / "s", repo_key=KEY, purpose="handover", profile=profile(GREEN), timeout=60)
    ok, why = receipts.check_receipt(state=tmp_path / "s", repo_key=KEY, sha=sha, purpose="handover",
                                     profile=profile(GREEN + "  # changed"))
    assert not ok and "changed" in why


def test_a_receipt_describes_one_revision(tmp_path):
    root = repo_with_origin(tmp_path)
    receipts.run_receipt(root, state=tmp_path / "s", repo_key=KEY, purpose="handover", profile=profile(GREEN), timeout=60)
    later = commit(root, "later")
    ok, why = receipts.check_receipt(state=tmp_path / "s", repo_key=KEY, sha=later, purpose="handover",
                                     profile=profile(GREEN))
    assert not ok and "no handover receipt" in why


def test_a_duplicate_tier_name_cannot_hide_a_red_run(tmp_path):
    root = repo_with_origin(tmp_path)
    sha = git(root, "rev-parse", "HEAD")
    both = {"schema": 1, "tests": {"tier": [
        {"name": "unit", "command": "exit 1", "required_for": ["handover"]},
        {"name": "unit", "command": GREEN, "required_for": ["handover"]}]}}
    receipts.run_receipt(root, state=tmp_path / "s", repo_key=KEY, purpose="handover", profile=both, timeout=60)
    ok, why = receipts.check_receipt(state=tmp_path / "s", repo_key=KEY, sha=sha, purpose="handover", profile=both)
    assert not ok and "unit" in why
