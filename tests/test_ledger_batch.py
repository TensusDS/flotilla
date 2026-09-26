import pytest

from flotilla.ledger import batch
from ledgerkit import PROFILE, commit, drive, git, make_ledger, merge, repo_with_origin

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
