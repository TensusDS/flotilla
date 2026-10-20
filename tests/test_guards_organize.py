from pathlib import Path

import pytest

from flotilla.guards import organize, shell
from flotilla.guards import run as guard_run
from flotilla.ledger.model import Row
from guardkit import onboarded

ROWS = {"r1": Row(id="r1", branch="docs/c", state="claimed", pr="44", closes=[42, 43]),
        "r2": Row(id="r2", branch="feat/x", state="claimed", pr="12")}
NUMBERS = {50}


def judge(command, rows=ROWS, numbers=NUMBERS):
    [segment] = shell.segments(command, Path("/tmp"))
    return organize.check(segment, rows=rows, numbers=numbers)


@pytest.mark.parametrize("command", [
    "gh pr close 43", "gh pr close '#42'", "gh pr edit 44 --base main", "gh pr edit 44 -B main",
    "gh pr edit 44 --base=main", "gh pr close https://github.com/o/r/pull/43", "gh pr close feat/x",
    "gh pr close 50 --comment done", "gh api -X POST repos/o/r/stacks/7/unstack",
    "gh api --method=POST repos/o/r/stacks/7/unstack", "gh stack merge", "gh stack unstack 7", "gh pr close",
    "gh pr edit --base main",
])
def test_outward_commands_on_tracked_work_are_refused(command):
    found = judge(command)
    assert found is not None and found.refuse and "flotilla organize apply" in found.text


@pytest.mark.parametrize("command", [
    "gh pr close 99", "gh pr edit 44 --title x", "gh api repos/o/r/stacks", "gh pr view 43",
    "gh pr close other/branch", "gh stack view", "gh pr edit 99 --base main", "gh api -X GET repos/o/r/stacks",
])
def test_other_commands_pass(command):
    assert judge(command) is None


def test_a_comment_value_is_not_the_target():
    assert judge("gh pr close 99 --comment 43") is None


def test_the_guard_runs_with_the_others_in_a_flotilla_project(tmp_path, monkeypatch):
    root = onboarded(tmp_path)
    monkeypatch.setattr(organize, "tracked", lambda root: (ROWS, NUMBERS))
    found = guard_run.evaluate("gh pr close 43", root, root)
    assert any(item.guard == organize.GUARD and item.refuse for item in found)
    assert not [item for item in guard_run.evaluate("gh pr close 99", root, root) if item.guard == organize.GUARD]


def test_a_ledger_that_cannot_be_read_refuses_a_door(tmp_path, monkeypatch):
    root = onboarded(tmp_path)

    def broken(root):
        raise RuntimeError("no ledger")
    monkeypatch.setattr(organize, "tracked", broken)
    found = guard_run.evaluate("gh pr close 99", root, root)
    assert any(item.guard == organize.GUARD and item.refuse and "cannot tell" in item.text for item in found)
    assert not [item for item in guard_run.evaluate("gh pr view 99", root, root) if item.guard == organize.GUARD]


def test_outside_a_flotilla_project_the_guard_is_quiet(tmp_path):
    found = guard_run.evaluate("gh pr close 43", tmp_path, None)
    assert not [item for item in found if item.guard == organize.GUARD]
