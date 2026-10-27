"""The tree's verdict (gc design, section 5.1): a tree goes when it holds nothing that exists nowhere else. Alive, in
use, clean, its commits held by a ref that survives, its ignored files regenerable - in that order."""

from pathlib import Path

import pytest

from flotilla.core.census import Session
from flotilla.gc import refs, trees
from ledgerkit import PROFILE, actor, commit, git, make_ledger, repo_with_origin
from test_fleet_leftovers import fake_proc


def world(tmp_path):
    root = repo_with_origin(tmp_path)
    return root, make_ledger(root, tmp_path / "state", profile=PROFILE, live=())


def seat(root, tmp_path, name="app-main-1", branch="fleet/main-1"):
    tree = tmp_path / name
    git(root, "worktree", "add", "-q", "-b", branch, str(tree))
    return tree


def judge(ledger, tree, *, dropping=frozenset(), sessions=(), proc_root=None):
    return trees.judge_tree(ledger, tree, holders=refs.holders(ledger, dropping=dropping), sessions=list(sessions),
                            proc_root=proc_root if proc_root is not None else tree.parent / "no-proc")


def session(name, cwd=""):
    return Session(name=name, session_id=name, kind="background", pid=None, short_id=None, status="idle",
                   state=None, cwd=str(cwd), started_at_ms=None)


def test_a_clean_tree_on_an_unlanded_branch_goes_and_the_branch_holds_its_work(tmp_path):
    root, ledger = world(tmp_path)
    tree = seat(root, tmp_path)
    commit(tree, "unlanded work", "w.txt", "x\n")
    assert judge(ledger, tree).removable
    found = judge(ledger, tree, dropping={"refs/heads/fleet/main-1"})
    assert not found.removable and any("only holder" in reason for reason in found.reasons)


def test_a_detached_tree_held_by_another_branch_goes(tmp_path):
    root, ledger = world(tmp_path)
    git(root, "checkout", "-q", "-b", "feat/x")
    tip = commit(root, "work")
    git(root, "checkout", "-q", "main")
    tree = tmp_path / "app-review-1"
    git(root, "worktree", "add", "-q", "--detach", str(tree), tip)
    found = judge(ledger, tree)
    assert found.removable, found.reasons
    assert found.branch == "" and found.head == tip


def test_reflog_only_commits_are_parked_not_a_doubt(tmp_path):
    root, ledger = world(tmp_path)
    tree = seat(root, tmp_path)
    dropped = commit(tree, "a trial")
    git(tree, "reset", "-q", "--hard", "HEAD~1")
    found = judge(ledger, tree)
    assert found.removable, found.reasons
    assert found.park == [dropped]


@pytest.mark.parametrize("marker", trees.MARKERS)
def test_an_operation_in_progress_keeps_the_tree(tmp_path, marker):
    root, ledger = world(tmp_path)
    tree = seat(root, tmp_path)
    gitdir = Path(git(tree, "rev-parse", "--absolute-git-dir"))
    if marker.startswith("rebase-"):
        (gitdir / marker).mkdir()
    else:
        (gitdir / marker).write_text("x", encoding="utf-8")
    found = judge(ledger, tree)
    assert not found.removable and any(marker in reason for reason in found.reasons)


def test_a_process_working_in_the_tree_keeps_it(tmp_path):
    root, ledger = world(tmp_path)
    tree = seat(root, tmp_path)
    proc = tmp_path / "proc"
    fake_proc(proc, 500, ppid=4000, cwd=tree, command="python render.py --frames 900")
    found = judge(ledger, tree, proc_root=proc)
    assert not found.removable
    assert any("render.py" in reason for reason in found.reasons)


def test_no_procfs_is_said_and_does_not_hold_the_tree(tmp_path):
    root, ledger = world(tmp_path)
    tree = seat(root, tmp_path)
    found = judge(ledger, tree, proc_root=tmp_path / "absent")
    assert found.removable and any("/proc" in note for note in found.notes)


def test_a_live_session_in_the_tree_keeps_it(tmp_path):
    root, ledger = world(tmp_path)
    tree = seat(root, tmp_path)
    found = judge(ledger, tree, sessions=[session("minor session 3", tree / "src")])
    assert not found.removable and "minor session 3" in found.reasons[0]


def test_an_open_row_for_a_live_session_holds_its_tree(tmp_path):
    root, ledger = world(tmp_path)
    tree = seat(root, tmp_path)
    from flotilla.ledger import tree as cut
    work = tmp_path / "app-main-2"
    cut.cut(ledger, actor(ledger, "main session 1"), "feat/held", work)
    found = judge(ledger, work, sessions=[session("main session 1", root)])
    assert not found.removable and "main session 1" in found.reasons[0]


def test_a_lock_by_someone_else_keeps_the_tree(tmp_path):
    root, ledger = world(tmp_path)
    tree = seat(root, tmp_path)
    git(root, "worktree", "lock", "--reason", "my experiment", str(tree))
    found = judge(ledger, tree)
    assert not found.removable and "locked by someone else" in found.reasons[0]


def test_a_flotilla_lock_of_a_gone_seat_does_not_hold_it(tmp_path):
    root, ledger = world(tmp_path)
    tree = seat(root, tmp_path)
    git(root, "worktree", "lock", "--reason", "flotilla: main session 9", str(tree))
    found = judge(ledger, tree)
    assert found.removable and found.lock == "flotilla: main session 9"


@pytest.mark.parametrize("doubt, reason", [
    ("uncommitted", "uncommitted"),
    ("untracked", "uncommitted"),
    ("ignored", ".env"),
    ("stash", "stash"),
    ("hidden", "told not to look at"),
])
def test_any_doubt_keeps_the_tree_and_names_it(tmp_path, doubt, reason):
    root, ledger = world(tmp_path)
    commit(root, "base", ".gitignore", ".env\n")
    commit(root, "tracked", "t.txt", "t\n")
    tree = seat(root, tmp_path)
    if doubt == "uncommitted":
        (tree / "t.txt").write_text("changed\n", encoding="utf-8")
    elif doubt == "untracked":
        (tree / "new.txt").write_text("n\n", encoding="utf-8")
    elif doubt == "ignored":
        (tree / ".env").write_text("SECRET=1\n", encoding="utf-8")
    elif doubt == "stash":
        (tree / "t.txt").write_text("stashed\n", encoding="utf-8")
        git(tree, "-c", "user.email=t@example.invalid", "-c", "user.name=t", "stash", "-q")
    elif doubt == "hidden":
        git(tree, "update-index", "--skip-worktree", "t.txt")
    found = judge(ledger, tree)
    assert not found.removable and any(reason in line for line in found.reasons), found.reasons


def test_a_populated_submodule_keeps_the_tree(tmp_path):
    """Security review of gc stage 1: a submodule's edits hide from status under `submodule.<name>.ignore=all`, and
    its commits live in the tree's own git directory - a nested repository is the person's to decide (gc design 6,
    rule 5)."""
    root, ledger = world(tmp_path)
    sub = tmp_path / "lib-src"
    sub.mkdir()
    git(sub, "init", "-q", "-b", "main")
    commit(sub, "lib", "lib.txt", "l\n")
    git(root, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(sub), "lib")
    git(root, "config", "-f", ".gitmodules", "submodule.lib.ignore", "all")
    git(root, "add", ".gitmodules")
    commit(root, "add lib")
    tree = seat(root, tmp_path)
    git(tree, "-c", "protocol.file.allow=always", "submodule", "update", "-q", "--init")
    (tree / "lib" / "lib.txt").write_text("edited inside the submodule\n", encoding="utf-8")
    found = judge(ledger, tree)
    assert not found.removable and any("submodule" in reason for reason in found.reasons), found.reasons
