import argparse

from flotilla.guards import overrides, reserve
from flotilla.guards.rules import rules_for
from flotilla.ledger import core
from guardkit import IDENTITY, git, onboarded
from ledgerkit import actor, drive, make_ledger

TODO = "".join(f"- item {n}\n" for n in range(10))


def world(tmp_path):
    root = onboarded(tmp_path, extra='\n[reservation]\nfiles = ["TODO.md"]\n', push=False)
    (root / "TODO.md").write_text(TODO, encoding="utf-8")
    git(root, "add", "TODO.md")
    git(root, *IDENTITY, "commit", "-q", "-m", "todo")
    git(root, "push", "-q", "origin", "main")
    profile, _ = rules_for(root)
    ledger = make_ledger(root, tmp_path / "state", profile=profile)
    drive(root, ledger, "feat/a", to="claimed", owner="main session 1")
    drive(root, ledger, "feat/b", to="claimed", owner="minor session 1")
    return root, ledger


def rewrite(root, keep=4):
    (root / "TODO.md").write_text("".join(TODO.splitlines(keepends=True)[:keep]), encoding="utf-8")
    git(root, "add", "TODO.md")


def check(root, ledger, env=None):
    return reserve.check(root, env=env or {}, ledger=ledger)


def test_the_first_rewrite_reserves_the_file_for_its_row(tmp_path):
    root, ledger = world(tmp_path)
    git(root, "checkout", "-q", "feat/a")
    rewrite(root)
    code, text = check(root, ledger)
    assert code == 0 and "reserved for feat/a" in text


def test_a_second_rewrite_under_anothers_reservation_is_refused(tmp_path):
    root, ledger = world(tmp_path)
    git(root, "checkout", "-q", "feat/a")
    rewrite(root)
    check(root, ledger)
    git(root, *IDENTITY, "commit", "-q", "-m", "a rewrites")
    git(root, "checkout", "-q", "feat/b")
    rewrite(root, keep=3)
    code, text = check(root, ledger)
    assert code == 1 and "main session 1" in text and "feat/a" in text


def test_an_append_always_passes(tmp_path):
    root, ledger = world(tmp_path)
    git(root, "checkout", "-q", "feat/a")
    rewrite(root)
    check(root, ledger)
    git(root, *IDENTITY, "commit", "-q", "-m", "a rewrites")
    git(root, "checkout", "-q", "feat/b")
    (root / "TODO.md").write_text(TODO + "- mine\n", encoding="utf-8")
    git(root, "add", "TODO.md")
    assert check(root, ledger) == (0, "")


def test_a_released_row_frees_the_file(tmp_path):
    root, ledger = world(tmp_path)
    git(root, "checkout", "-q", "feat/a")
    rewrite(root)
    check(root, ledger)
    git(root, *IDENTITY, "commit", "-q", "-m", "a rewrites")
    core.release(ledger, actor(ledger, "main session 1"), "feat/a", why="dropped")
    git(root, "checkout", "-q", "feat/b")
    rewrite(root, keep=3)
    assert check(root, ledger)[0] == 0


def test_an_override_passes_and_is_recorded(tmp_path):
    root, ledger = world(tmp_path)
    git(root, "checkout", "-q", "feat/a")
    rewrite(root)
    check(root, ledger)
    git(root, *IDENTITY, "commit", "-q", "-m", "a rewrites")
    git(root, "checkout", "-q", "feat/b")
    rewrite(root, keep=3)
    code, text = check(root, ledger, env={"FLOTILLA_RESERVE_OVERRIDE": "different section, agreed"})
    assert code == 0 and "override recorded" in text
    assert overrides.recorded(ledger.state_dir, ledger.repo_key)[-1]["guard"] == "reservation"


def test_a_merge_bringing_the_holders_rewrite_passes(tmp_path):
    root, ledger = world(tmp_path)
    git(root, "checkout", "-q", "feat/a")
    rewrite(root)
    check(root, ledger)
    git(root, *IDENTITY, "commit", "-q", "-m", "a rewrites")
    git(root, "checkout", "-q", "main")
    git(root, *IDENTITY, "merge", "-q", "--no-ff", "--no-commit", "feat/a")
    assert check(root, ledger)[0] == 0


def test_status_lists_live_reservations(tmp_path, capsys):
    from flotilla.ledger.commands import _status
    root, ledger = world(tmp_path)
    git(root, "checkout", "-q", "feat/a")
    rewrite(root)
    check(root, ledger)
    _status(ledger, argparse.Namespace(stalled=None))
    out = capsys.readouterr().out
    assert "reservations:" in out and "TODO.md: main session 1 for feat/a" in out


def test_a_merge_bringing_a_rewrite_passes_after_the_holder_moved_on(tmp_path):
    root, ledger = world(tmp_path)
    git(root, "checkout", "-q", "feat/a")
    rewrite(root)
    check(root, ledger)
    git(root, *IDENTITY, "commit", "-q", "-m", "a rewrites")
    git(root, "checkout", "-q", "main")
    git(root, *IDENTITY, "merge", "-q", "--no-ff", "-m", "land a", "feat/a")
    git(root, "checkout", "-q", "feat/a")
    (root / "later.txt").write_text("more\n", encoding="utf-8")
    git(root, "add", "later.txt")
    git(root, *IDENTITY, "commit", "-q", "-m", "a moves on")
    git(root, "checkout", "-q", "feat/b")
    git(root, *IDENTITY, "merge", "-q", "--no-ff", "--no-commit", "main")
    assert check(root, ledger)[0] == 0
