"""The gc guard (gc design, section 9): a session does not remove fleet trees, fleet branches, parked refs or the
archive by hand - only the collector does, from the person's recorded answer. A guard that stops a mistaken session,
not a determined one (decision 245)."""

from pathlib import Path

import pytest

from flotilla.guards import gc, shell
from flotilla.guards import run as guard_run
from guardkit import onboarded


def judge(command, cwd, *, seats=(), branches=(), archive=None):
    [segment] = shell.segments(command, Path(cwd))
    return gc.check(segment, seat_trees={Path(seat).resolve() for seat in seats}, fleet_branches=set(branches),
                    archive=Path(archive or Path(cwd) / "app-gc-archive").resolve())


def layout(tmp_path):
    seat = tmp_path / "app-main-1"
    (seat / "src").mkdir(parents=True)
    store = tmp_path / "app-gc-archive"
    (store / "objects" / "ab").mkdir(parents=True)
    return seat, store


def refused(found):
    return found is not None and found.refuse and "flotilla gc apply" in found.text


def test_a_forced_removal_of_a_seat_tree_is_refused(tmp_path):
    seat, store = layout(tmp_path)
    for command in (f"git worktree remove --force {seat}", f"git worktree remove -f {seat}",
                    "git worktree remove --force app-main-1"):
        assert refused(judge(command, tmp_path, seats=[seat], archive=store)), command


def test_a_plain_removal_is_let_through(tmp_path):
    seat, store = layout(tmp_path)
    assert judge(f"git worktree remove {seat}", tmp_path, seats=[seat], archive=store) is None


def test_a_forced_removal_of_a_persons_tree_is_let_through(tmp_path):
    seat, store = layout(tmp_path)
    (tmp_path / "mine").mkdir()
    assert judge("git worktree remove --force mine", tmp_path, seats=[seat], archive=store) is None


def test_prune_is_refused(tmp_path):
    seat, store = layout(tmp_path)
    assert refused(judge("git worktree prune", tmp_path, seats=[seat], archive=store))


@pytest.mark.parametrize("command", ["git branch -D fleet/main-1", "git branch -df fleet/main-1",
                                     "git branch --delete --force fleet/main-1", "git branch -D feat/tracked"])
def test_deleting_a_fleet_branch_by_force_is_refused(tmp_path, command):
    seat, store = layout(tmp_path)
    assert refused(judge(command, tmp_path, seats=[seat], branches=["feat/tracked"], archive=store)), command


@pytest.mark.parametrize("command", ["git branch -D experiment", "git branch -d fleet/main-1"])
def test_deleting_a_persons_branch_or_a_merged_one_is_let_through(tmp_path, command):
    seat, store = layout(tmp_path)
    assert judge(command, tmp_path, seats=[seat], archive=store) is None


def test_deleting_a_parked_ref_is_refused(tmp_path):
    seat, store = layout(tmp_path)
    assert refused(judge("git update-ref -d refs/flotilla/parked/app-main-1/abc123", tmp_path, seats=[seat],
                         archive=store))


@pytest.mark.parametrize("target", ["app-main-1", "app-main-1/src", "app-gc-archive", "app-gc-archive/objects/ab",
                                    "."])
def test_rm_rf_of_a_seat_tree_the_archive_or_a_parent_of_them_is_refused(tmp_path, target):
    seat, store = layout(tmp_path)
    assert refused(judge(f"rm -rf {target}", tmp_path, seats=[seat], archive=store)), target


def test_rm_rf_with_a_glob_inside_the_archive_is_refused(tmp_path):
    seat, store = layout(tmp_path)
    assert refused(judge("rm -rf objects/*", store, seats=[seat], archive=store))


def test_rm_of_one_file_or_elsewhere_is_let_through(tmp_path):
    seat, store = layout(tmp_path)
    (tmp_path / "scratch").mkdir()
    assert judge(f"rm {seat}/src/x.txt", tmp_path, seats=[seat], archive=store) is None
    assert judge("rm -rf scratch", tmp_path, seats=[seat], archive=store) is None


def test_the_hook_runs_the_gc_guard(tmp_path):
    root = onboarded(tmp_path)
    found = guard_run.evaluate("git worktree prune", root, root)
    assert any(item.guard == gc.GUARD and item.refuse for item in found), found
