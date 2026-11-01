"""Shrinking idle trees, gc design section 8: with a low disk, a tree nobody works in gives up what an install makes
again. A live tree is never touched; nothing a person made is removed."""

import os
import time
from pathlib import Path

from flotilla.gc import shrink
from ledgerkit import PROFILE, actor, commit, git, make_ledger, repo_with_origin
from test_fleet_leftovers import fake_proc
from test_gc_trees import session

DAY = 86400


def world(tmp_path, profile=None):
    root = repo_with_origin(tmp_path)
    commit(root, "ignore", ".gitignore", "node_modules\n")
    ledger = make_ledger(root, tmp_path / "state", profile=profile or PROFILE, live=())
    tree = tmp_path / "app-main-1"
    git(root, "worktree", "add", "-q", "-b", "fleet/main-1", str(tree))
    (tree / "node_modules").mkdir()
    (tree / "node_modules" / "x.js").write_text("x" * 4096, encoding="utf-8")
    return root, ledger, tree


def age_git(tree, hours):
    gitdir = Path(git(tree, "rev-parse", "--absolute-git-dir"))
    then = time.time() - hours * 3600
    for name in ("index", "HEAD", "logs/HEAD"):
        if (gitdir / name).exists():
            os.utime(gitdir / name, (then, then))


def run(ledger, tmp_path, *, sessions=(), act=True, proc_root=None):
    return shrink.shrink(ledger, sessions=list(sessions), act=act,
                         proc_root=proc_root if proc_root is not None else tmp_path / "proc-empty")


def test_an_idle_tree_loses_its_node_modules_and_stays(tmp_path):
    root, ledger, tree = world(tmp_path)
    (tmp_path / "proc-empty").mkdir()
    age_git(tree, 25)
    done = run(ledger, tmp_path)
    assert not (tree / "node_modules").exists() and (tree / "README.md").exists()
    assert done.freed >= 4096 and done.removed and done.removed[0][1] == "node_modules"
    assert "shrank 1 idle tree" in shrink.lines(done)[0]
    assert "flotilla: gc" not in git(root, "worktree", "list", "--porcelain")   # unlocked after


def test_a_live_sessions_tree_is_never_shrunk(tmp_path):
    root, ledger, tree = world(tmp_path)
    (tmp_path / "proc-empty").mkdir()
    age_git(tree, 25)
    with ledger.session() as s:
        s.append(actor(ledger, "main session 1"), "r1", "reserve", "reserved",
                 fields={"branch": "fleet/main-1", "owner": "main session 1", "tree": str(tree)})
    done = run(ledger, tmp_path, sessions=[session("main session 1", tmp_path / "elsewhere")])
    assert (tree / "node_modules" / "x.js").exists() and not done.removed
    assert any("main session 1" in line for line in done.kept)


def test_recent_git_activity_keeps_the_tree(tmp_path):
    root, ledger, tree = world(tmp_path)
    (tmp_path / "proc-empty").mkdir()
    age_git(tree, 25)
    gitdir = Path(git(tree, "rev-parse", "--absolute-git-dir"))
    then = time.time() - 2 * 3600
    os.utime(gitdir / "index", (then, then))
    done = run(ledger, tmp_path)
    assert (tree / "node_modules").exists() and any("git activity" in line for line in done.kept)


def test_a_process_working_in_the_tree_keeps_it(tmp_path):
    root, ledger, tree = world(tmp_path)
    age_git(tree, 25)
    proc = tmp_path / "proc"
    fake_proc(proc, 500, ppid=4000, cwd=tree, command="npm run dev")
    done = run(ledger, tmp_path, proc_root=proc)
    assert (tree / "node_modules").exists() and any("npm" in line for line in done.kept)


def test_unreadable_processes_keep_the_tree(tmp_path):
    root, ledger, tree = world(tmp_path)
    age_git(tree, 25)
    done = run(ledger, tmp_path, proc_root=tmp_path / "absent")
    assert (tree / "node_modules").exists() and any("could not be read" in line for line in done.kept)


def test_a_tracked_node_modules_is_not_touched(tmp_path):
    root = repo_with_origin(tmp_path)
    (root / "node_modules").mkdir()
    commit(root, "vendored", "node_modules/kept.js", "mine\n")
    ledger = make_ledger(root, tmp_path / "state", profile=PROFILE, live=())
    tree = tmp_path / "app-main-1"
    git(root, "worktree", "add", "-q", "-b", "fleet/main-1", str(tree))
    (tmp_path / "proc-empty").mkdir()
    age_git(tree, 25)
    done = run(ledger, tmp_path)
    assert (tree / "node_modules" / "kept.js").exists() and not done.removed


def test_a_regenerable_link_is_removed_as_a_link(tmp_path):
    root, ledger, tree = world(tmp_path)
    (tmp_path / "proc-empty").mkdir()
    outside = tmp_path / "shared-modules"
    outside.mkdir()
    (outside / "big.js").write_text("y" * 4096, encoding="utf-8")
    (tree / "node_modules" / "x.js").unlink()
    (tree / "node_modules").rmdir()
    os.symlink(outside, tree / "node_modules")
    age_git(tree, 25)
    done = run(ledger, tmp_path)
    assert not os.path.lexists(tree / "node_modules") and (outside / "big.js").exists()
    assert done.removed


def test_without_act_nothing_is_removed(tmp_path):
    root, ledger, tree = world(tmp_path)
    (tmp_path / "proc-empty").mkdir()
    age_git(tree, 25)
    done = run(ledger, tmp_path, act=False)
    assert (tree / "node_modules" / "x.js").exists() and done.removed[0][1] == "node_modules"


def test_a_tree_locked_by_someone_else_is_kept(tmp_path):
    root, ledger, tree = world(tmp_path)
    (tmp_path / "proc-empty").mkdir()
    age_git(tree, 25)
    git(root, "worktree", "lock", "--reason", "on a usb disk", str(tree))
    done = run(ledger, tmp_path)
    assert (tree / "node_modules").exists() and any("on a usb disk" in line for line in done.kept)
