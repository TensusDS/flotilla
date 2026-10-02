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


def _counting(tmp_path):
    """A green tier that counts its own runs in a file outside the repository."""
    counter = tmp_path / "runs.txt"
    return f"{sys.executable} -c \"open(r'{counter}', 'a').write('x'); print('2 passed')\"", counter


def test_the_same_files_are_not_tested_twice(tmp_path):
    """The slowdown measured on the twosuns fleet (2026-10-02): the path from hand to trunk grew tenfold, almost all of
    it in the machine's one-at-a-time lane. A sender's merge commit often carries exactly the files the author's tip
    did; a green tier over the same files and the same command is that tier's answer, whoever asks."""
    root = repo_with_origin(tmp_path)
    command, counter = _counting(tmp_path)
    tip = git(root, "rev-parse", "HEAD")
    receipts.run_receipt(root, state=tmp_path / "s", repo_key=KEY, purpose="handover", profile=profile(command),
                         timeout=60)
    git(root, "commit", "-q", "--allow-empty", "-m", "a merge that changes no file")
    merge = git(root, "rev-parse", "HEAD")
    assert merge != tip
    receipt = receipts.run_receipt(root, state=tmp_path / "s", repo_key=KEY, purpose="push", profile=profile(command),
                                   timeout=60)
    assert counter.read_text() == "x"   # run once, not twice
    assert receipt["tiers"][0]["status"] == "green" and tip[:7] in receipt["tiers"][0]["summary"]
    ok, _ = receipts.check_receipt(state=tmp_path / "s", repo_key=KEY, sha=merge, purpose="push",
                                   profile=profile(command))
    assert ok


def test_other_files_or_another_command_run_again(tmp_path):
    root = repo_with_origin(tmp_path)
    command, counter = _counting(tmp_path)
    receipts.run_receipt(root, state=tmp_path / "s", repo_key=KEY, purpose="handover", profile=profile(command),
                         timeout=60)
    commit(root, "a change", "src.txt", "new\n")
    receipts.run_receipt(root, state=tmp_path / "s", repo_key=KEY, purpose="push", profile=profile(command),
                         timeout=60)
    assert counter.read_text() == "xx"
    receipts.run_receipt(root, state=tmp_path / "s", repo_key=KEY, purpose="push",
                         profile=profile(command + " --again"), timeout=60)
    assert counter.read_text() == "xxx"


def test_a_red_tier_is_never_reused_and_says_why(tmp_path):
    """Worldcore field test W23: a red push receipt said `node: red` and nothing else; the sender looked for a log that
    did not exist. A red tier carries its exit code and the last lines it printed."""
    root = repo_with_origin(tmp_path)
    red = f"{sys.executable} -c \"import sys; print('sh: 1: vitest: not found'); sys.exit(127)\""
    first = receipts.run_receipt(root, state=tmp_path / "s", repo_key=KEY, purpose="handover", profile=profile(red),
                                 timeout=60)
    tier = first["tiers"][0]
    assert tier["status"] == "red" and tier["exit"] == 127 and "vitest: not found" in tier["tail"]
    again = receipts.run_receipt(root, state=tmp_path / "s", repo_key=KEY, purpose="push", profile=profile(red),
                                 timeout=60)
    assert "reused" not in again["tiers"][0]["summary"]


def _with_setup(tmp_path, root, command):
    counter = tmp_path / "setup-runs.txt"
    setup = f"{sys.executable} -c \"open(r'{counter}', 'a').write('s')\""
    data = profile(command)
    data["tests"]["setup_command"] = setup
    (root / "package-lock.json").write_text('{"v": 1}', encoding="utf-8")
    git(root, "add", "package-lock.json")
    git(root, "commit", "-q", "-m", "a lockfile")
    return data, counter


def test_a_fresh_tree_is_set_up_before_its_tiers_once_per_lockfile(tmp_path):
    root = repo_with_origin(tmp_path)
    tiers, _ = _counting(tmp_path)
    data, counter = _with_setup(tmp_path, root, tiers)
    receipts.run_receipt(root, state=tmp_path / "s", repo_key=KEY, purpose="handover", profile=data, timeout=60)
    assert counter.read_text() == "s"
    commit(root, "a change", "src.txt", "new\n")
    receipts.run_receipt(root, state=tmp_path / "s", repo_key=KEY, purpose="handover", profile=data, timeout=60)
    assert counter.read_text() == "s"                  # the lockfile did not change: nothing to set up
    (root / "package-lock.json").write_text('{"v": 2}', encoding="utf-8")
    git(root, "commit", "-q", "-am", "a new dependency")
    receipts.run_receipt(root, state=tmp_path / "s", repo_key=KEY, purpose="handover", profile=data, timeout=60)
    assert counter.read_text() == "ss"


def test_a_failed_setup_is_named_not_reported_as_a_red_tier(tmp_path):
    root = repo_with_origin(tmp_path)
    data, _ = _with_setup(tmp_path, root, GREEN)
    data["tests"]["setup_command"] = f"{sys.executable} -c \"import sys; print('npm ERR! network'); sys.exit(1)\""
    with pytest.raises(receipts.ReceiptRefused, match="setup") as refused:
        receipts.run_receipt(root, state=tmp_path / "s", repo_key=KEY, purpose="handover", profile=data, timeout=60)
    assert "npm ERR! network" in str(refused.value) and "exit 1" in str(refused.value)
