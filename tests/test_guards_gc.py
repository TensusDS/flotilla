"""The gc guard (gc design, section 9): a session does not remove fleet trees, fleet branches, parked refs or the
archive by hand - only the collector does, from the person's recorded answer. A guard that stops a mistaken session,
not a determined one (decision 245)."""

from pathlib import Path

import pytest

from flotilla.guards import gc, shell
from flotilla.guards import run as guard_run
from guardkit import onboarded


def judge(command, cwd, *, seats=(), branches=(), archive=None, home=None):
    """`home` is where the session stands (the hook's cwd); by default the directory the command starts in."""
    segment = [segment for segment in shell.segments(command, Path(cwd)) if gc.door(segment)]
    return gc.check(segment[0] if segment else shell.segments(command, Path(cwd))[0],
                    seat_trees={Path(seat).resolve() for seat in seats}, fleet_branches=set(branches),
                    archive=Path(archive or Path(cwd) / "app-gc-archive").resolve(), home=Path(home or cwd))


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


def test_a_plain_removal_of_a_seat_tree_is_refused_too(tmp_path):
    """Final review of gc stage 2, I2: git deletes ignored files without a word, forced or not."""
    seat, store = layout(tmp_path)
    assert refused(judge(f"git worktree remove {seat}", tmp_path, seats=[seat], archive=store))


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


@pytest.mark.parametrize("target", ["app-main-1", "app-gc-archive", "app-gc-archive/objects/ab", "."])
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


# Security review of 7590fff: the guard reads what the tools read - a glob's literal prefix, abbreviated long options,
# git's worktree suffix match, and update-ref's other ways to delete.

def test_a_glob_reaching_seat_trees_from_elsewhere_is_refused(tmp_path):
    seat, store = layout(tmp_path)
    (tmp_path / "scratch").mkdir()
    assert refused(judge("rm -rf ../app-main-*", tmp_path / "scratch", seats=[seat], archive=store))
    (tmp_path / "scratch" / "own").mkdir()
    assert judge("rm -rf own/*", tmp_path / "scratch", seats=[seat], archive=store) is None


@pytest.mark.parametrize("command", ["rm --recur app-main-1", "rm --recursive=x app-main-1",
                                     "git worktree remove --forc app-main-1", "git branch --del --forc fleet/main-1"])
def test_abbreviated_long_options_count_as_the_option(tmp_path, command):
    seat, store = layout(tmp_path)
    assert refused(judge(command, tmp_path, seats=[seat], archive=store)), command


def test_a_worktree_named_by_its_suffix_is_refused(tmp_path):
    seat, store = layout(tmp_path)
    (tmp_path / "scratch").mkdir()
    assert refused(judge("git worktree remove --force app-main-1", tmp_path / "scratch", seats=[seat],
                         archive=store))


@pytest.mark.parametrize("command", ["git update-ref -d refs/heads/fleet/main-1",
                                     "git update-ref --delete refs/heads/feat/tracked",
                                     "git update-ref -d refs/flotilla/dropped/x",
                                     "git update-ref refs/heads/fleet/main-1 " + "0" * 40,
                                     "git update-ref --stdin"])
def test_update_ref_deletions_of_fleet_refs_are_refused(tmp_path, command):
    seat, store = layout(tmp_path)
    assert refused(judge(command, tmp_path, seats=[seat], branches=["feat/tracked"], archive=store)), command


def test_update_ref_of_a_persons_branch_is_let_through(tmp_path):
    seat, store = layout(tmp_path)
    assert judge("git update-ref -d refs/heads/experiment", tmp_path, seats=[seat], archive=store) is None



def test_rm_inside_a_seat_tree_is_the_sessions_own_work(tmp_path):
    """Final review of gc stage 2, I3: a builder's `rm -rf dist` in its own tree."""
    seat, store = layout(tmp_path)
    assert judge("rm -rf src", seat, seats=[seat], archive=store) is None
    assert judge("rm -rf src/*", seat, seats=[seat], archive=store) is None
    assert refused(judge("rm -rf *", seat, seats=[seat], archive=store))
    assert refused(judge("rm -rf ./*", seat, seats=[seat], archive=store))


# Security review of b4a4213.

def test_an_expansion_after_a_literal_prefix_is_judged_by_where_it_runs_too(tmp_path):
    seat, store = layout(tmp_path)
    assert refused(judge("rm -rf /tmp/$(printf x)", tmp_path, seats=[seat], archive=store))


def test_an_abbreviated_stdin_is_refused(tmp_path):
    seat, store = layout(tmp_path)
    assert refused(judge("git update-ref --std", tmp_path, seats=[seat], archive=store))


def test_a_message_before_the_ref_does_not_hide_it(tmp_path):
    seat, store = layout(tmp_path)
    assert refused(judge("git update-ref -m why -d refs/heads/fleet/main-1", tmp_path, seats=[seat], archive=store))


# Security review of dd5d577: inside a seat tree means the tree the command runs in, by a path that can be named.

def test_an_expansion_from_inside_a_seat_tree_is_refused(tmp_path):
    seat, store = layout(tmp_path)
    assert refused(judge("rm -rf $X", seat / "src", seats=[seat], archive=store))


def test_a_climb_after_a_glob_is_judged_by_where_it_runs(tmp_path):
    seat, store = layout(tmp_path)
    assert refused(judge("rm -rf src/*/../..", seat, seats=[seat], archive=store))


def test_rm_inside_another_sessions_tree_is_refused(tmp_path):
    seat, store = layout(tmp_path)
    other = tmp_path / "app-main-2"
    (other / "out").mkdir(parents=True)
    assert refused(judge("rm -rf app-main-2/out", tmp_path, seats=[seat, other], archive=store))
    assert refused(judge("rm -rf ../app-main-2/out", seat, seats=[seat, other], archive=store))
    assert judge("rm -rf src", seat, seats=[seat, other], archive=store) is None


# Security review of 70f64bd: a brace or a `..` inside a part after a glob can climb too.

@pytest.mark.parametrize("operand", ["src/{..,x}", "src/*..", "{src,..}"])
def test_braces_and_dots_after_a_glob_are_judged_by_where_they_run(tmp_path, operand):
    seat, store = layout(tmp_path)
    assert refused(judge(f"rm -rf {operand}", seat, seats=[seat], archive=store)), operand


# The `cd` hole: a session's own tree is the one the session stands in (the hook's cwd), not the one a `cd` takes the
# command to.

def test_a_cd_into_another_sessions_tree_does_not_make_it_ones_own(tmp_path):
    seat, store = layout(tmp_path)
    other = tmp_path / "app-main-2"
    (other / "out").mkdir(parents=True)
    for command in ("cd ../app-main-2 && rm -rf out", f"cd {other} && rm -rf out", f"cd {other}/out && rm -rf ../out"):
        assert refused(judge(command, seat, seats=[seat, other], archive=store)), command
    assert judge("cd src && rm -rf build", seat, seats=[seat, other], archive=store) is None


def test_a_session_outside_every_seat_tree_cannot_cd_into_one_to_rm(tmp_path):
    seat, store = layout(tmp_path)
    assert refused(judge("cd app-main-1 && rm -rf src", tmp_path, seats=[seat], archive=store))


def test_without_the_sessions_directory_no_tree_is_its_own(tmp_path):
    seat, store = layout(tmp_path)
    [segment] = shell.segments("rm -rf src", seat)
    assert refused(gc.check(segment, seat_trees={seat.resolve()}, fleet_branches=set(), archive=store.resolve()))


def two_seats(tmp_path, monkeypatch):
    from flotilla.gc import sweep
    root = onboarded(tmp_path)
    seat, other = tmp_path / "app-main-1", tmp_path / "app-main-2"
    (seat / "src").mkdir(parents=True)
    (other / "out").mkdir(parents=True)
    monkeypatch.setattr(sweep, "made_by_flotilla", lambda ledger: ({seat.resolve(), other.resolve()}, set()))
    return root, seat, other


def gc_refusals(found):
    return [item for item in found if item.guard == gc.GUARD and item.refuse]


def test_the_hook_judges_rm_by_the_tree_the_session_was_started_in(tmp_path, monkeypatch):
    root, seat, other = two_seats(tmp_path, monkeypatch)
    assert gc_refusals(guard_run.evaluate("cd ../app-main-2 && rm -rf out", seat, root, stands=lambda: seat))
    assert not gc_refusals(guard_run.evaluate("rm -rf src", seat, root, stands=lambda: seat))


# Security review of 9830acb: the hook's cwd follows a `cd` that persisted from an earlier call, so a session's own
# tree is the directory it was started in, asked of the census by its id - which no command can move.

def test_a_cd_persisted_from_an_earlier_call_does_not_make_a_tree_ones_own(tmp_path, monkeypatch):
    root, seat, other = two_seats(tmp_path, monkeypatch)
    assert gc_refusals(guard_run.evaluate("rm -rf out", other, root, stands=lambda: seat))


@pytest.mark.parametrize("stands", [None, lambda: None, lambda: 1 / 0])
def test_a_session_whose_start_is_unknown_owns_no_tree(tmp_path, monkeypatch, stands):
    root, seat, other = two_seats(tmp_path, monkeypatch)
    assert gc_refusals(guard_run.evaluate("rm -rf src", seat, root, stands=stands))


def test_the_hook_asks_the_census_for_the_calling_sessions_start(tmp_path, monkeypatch):
    import io
    import json
    from flotilla.core import census
    root, seat, other = two_seats(tmp_path, monkeypatch)
    me = census.Session(name="main session 1", session_id="sid-1", kind="background", pid=None, short_id=None,
                        status=None, state=None, cwd=str(seat), started_at_ms=None)
    monkeypatch.setattr(census, "read_census", lambda **_: [me])

    def decision(command, cwd, session_id):
        out = io.StringIO()
        guard_run.guard_hook(command, cwd, root, out, session_id=session_id)
        return (json.loads(out.getvalue() or "{}").get("hookSpecificOutput") or {}).get("permissionDecision")

    assert decision("rm -rf out", other, "sid-1") == "deny"
    assert decision("rm -rf src", seat, "sid-1") != "deny"
    assert decision("rm -rf src", seat, "sid-unknown") == "deny"
