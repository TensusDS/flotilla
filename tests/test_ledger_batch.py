import subprocess

import pytest

from flotilla.ledger import batch
from ledgerkit import IDENTITY, PROFILE, commit, drive, git, make_ledger, merge, repo_with_origin

DIRECT = {**PROFILE, "flow": {"mode": "direct"}, "release": {"version_files": ["pyproject.toml"]}}


@pytest.fixture()
def world(tmp_path):
    root = repo_with_origin(tmp_path)
    return root, make_ledger(root, tmp_path / "state", profile=DIRECT)


def write(root, name, text):
    (root / name).write_text(text, encoding="utf-8")
    git(root, "add", name)


def test_reviewed_work_and_its_merge_are_accounted(world):
    root, ledger = world
    drive(root, ledger, "feat/x")
    merge(root, "feat/x")
    assert batch.unaccounted(ledger, ledger.rows(), "main") == []


def test_a_commit_nobody_read_is_named(world):
    root, ledger = world
    drive(root, ledger, "feat/x")
    merge(root, "feat/x")
    stray = commit(root, "stray", "stray.txt")
    assert batch.unaccounted(ledger, ledger.rows(), "main") == [stray]


def test_handed_but_unaccepted_work_is_not_accounted(world):
    root, ledger = world
    row = drive(root, ledger, "feat/x", to="handed")
    merge(root, "feat/x")
    assert batch.unaccounted(ledger, ledger.rows(), "main") == [row.tip]


def test_a_version_bump_is_a_release_and_anything_more_is_not(world):
    root, ledger = world
    write(root, "pyproject.toml", '[project]\nname = "app"\nversion = "0.1.0"\n')
    commit(root, "add pyproject")
    git(root, "push", "-q", "origin", "main")
    write(root, "pyproject.toml", '[project]\nname = "app"\nversion = "0.2.0"\n')
    commit(root, "release 0.2.0")
    assert batch.unaccounted(ledger, ledger.rows(), "main") == []
    write(root, "pyproject.toml", '[project]\nname = "app2"\nversion = "0.3.0"\n')
    sneaky = commit(root, "release 0.3.0")
    assert batch.unaccounted(ledger, ledger.rows(), "main") == [sneaky]


def test_a_squash_of_reviewed_work_is_recognised_by_content(world):
    root, ledger = world
    drive(root, ledger, "feat/x")
    squashed = merge(root, "feat/x", squash=True)
    assert batch.account(ledger, ledger.rows(), squashed).startswith("a squash of `feat/x`")
    assert batch.unaccounted(ledger, ledger.rows(), "main") == []


def test_a_squash_that_adds_to_the_reviewed_change_is_not_accounted(world):
    root, ledger = world
    drive(root, ledger, "feat/x")
    git(root, "merge", "-q", "--squash", "feat/x")
    write(root, "extra.txt", "unread\n")
    squashed = commit(root, "squash with extra")
    assert batch.unaccounted(ledger, ledger.rows(), "main") == [squashed]


def test_an_empty_commit_carries_nothing_to_read(world):
    root, ledger = world
    empty = commit(root, "empty")
    assert batch.account(ledger, ledger.rows(), empty) == "carries no change"


def test_without_origin_or_base_the_question_is_unknown(tmp_path):
    root = tmp_path / "solo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    commit(root, "init", "a.txt")
    ledger = make_ledger(root, tmp_path / "state", profile={**PROFILE, "flow": {"mode": "local"}})
    assert batch.outgoing(ledger, "main") is None
    assert batch.unaccounted(ledger, ledger.rows(), "main") is None


def test_a_merge_that_adds_its_own_change_is_not_accounted(world):
    root, ledger = world
    drive(root, ledger, "feat/x")
    git(root, *IDENTITY, "merge", "-q", "--no-ff", "--no-commit", "feat/x")
    write(root, "evil.txt", "slipped into the merge\n")
    evil = commit(root, "merge feat/x")
    assert batch.account(ledger, ledger.rows(), evil) is None
    assert batch.unaccounted(ledger, ledger.rows(), "main") == [evil]


def test_a_reviewed_branchs_base_is_not_read_by_its_reader(tmp_path):
    root = tmp_path / "solo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    commit(root, "init", "a.txt")
    ledger = make_ledger(root, tmp_path / "state", profile={**PROFILE, "flow": {"mode": "local"}})
    drive(root, ledger, "feat/x")
    base_of_y = commit(root, "on main, read by nobody", "s.txt")
    drive(root, ledger, "feat/y")
    assert batch.account(ledger, ledger.rows(), base_of_y) is None


def git_calls_to_account(tmp_path, count):
    root = repo_with_origin(tmp_path)
    ledger = make_ledger(root, tmp_path / "state", profile=DIRECT)
    for index in range(count):
        drive(root, ledger, f"feat/{index}")
        merge(root, f"feat/{index}", squash=True)
    calls = []

    def counting(cmd, **kwargs):
        if cmd and cmd[0] == "git":
            calls.append(cmd)
        return subprocess.run(cmd, **kwargs)
    ledger.run = counting
    assert batch.unaccounted(ledger, ledger.rows(), "main") == []
    return len(calls)


def test_accounting_costs_grow_with_rows_plus_commits_not_their_product(tmp_path):
    (tmp_path / "small").mkdir()
    (tmp_path / "large").mkdir()
    small, large = git_calls_to_account(tmp_path / "small", 4), git_calls_to_account(tmp_path / "large", 12)
    assert large <= 4 * small, (small, large)


def test_a_squash_that_moves_code_into_a_block_is_not_the_change_that_was_read(world):
    """`git patch-id --stable` drops whitespace, and in Python indentation is meaning: a squash that moves a call
    into an `if` carried the fingerprint of the reviewed change (security review F15)."""
    from flotilla.ledger import core, handover, reading
    from ledgerkit import actor
    root, ledger = world
    git(root, "switch", "-q", "-c", "feat/py")
    write(root, "code.py", "if guarded:\n    check()\nrun()\n")
    commit(root, "run after the check")
    git(root, "switch", "-q", "main")
    core.claim(ledger, actor(ledger, "main session 1"), "feat/py")
    row = handover.hand(ledger, actor(ledger, "main session 1"), "feat/py")
    reading.take(ledger, actor(ledger, "review session 1"), "feat/py")
    reading.accept(ledger, actor(ledger, "review session 1"), "feat/py", reviewed=row.tip)
    write(root, "code.py", "if guarded:\n    check()\n    run()\n")
    squashed = commit(root, "squash of feat/py")
    assert batch.unaccounted(ledger, ledger.rows(), "main") == [squashed]
