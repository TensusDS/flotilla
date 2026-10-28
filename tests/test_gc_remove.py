"""Removal under the lock (gc design, section 5.5): the tree is locked as a mutex, judged again, its reflog-only
commits parked, then removed without `--force`; anything that moved since the judgement keeps it."""

import subprocess

from flotilla.gc import branches, refs, remove, trees
from ledgerkit import PROFILE, commit, git, make_ledger, repo_with_origin


def world(tmp_path):
    root = repo_with_origin(tmp_path)
    return root, make_ledger(root, tmp_path / "state", profile=PROFILE, live=())


def seat(root, tmp_path, name="app-main-1", branch="fleet/main-1"):
    tree = tmp_path / name
    git(root, "worktree", "add", "-q", "-b", branch, str(tree))
    return tree


def judged(ledger, tree):
    return trees.judge_tree(ledger, tree, holders=refs.holders(ledger), sessions=[],
                            proc_root=tree.parent / "no-proc")


def take_out(ledger, verdict):
    return remove.remove_tree(ledger, verdict, dropping=set(), sessions=[], proc_root=verdict.tree + "-no-proc")


def test_a_clean_tree_is_removed(tmp_path):
    root, ledger = world(tmp_path)
    tree = seat(root, tmp_path)
    lines = take_out(ledger, judged(ledger, tree))
    assert not tree.exists() and lines == [f"removed {tree}"]


def test_a_tree_locked_while_judged_stays(tmp_path):
    root, ledger = world(tmp_path)
    tree = seat(root, tmp_path)
    verdict = judged(ledger, tree)
    git(root, "worktree", "lock", "--reason", "flotilla: spawn of main session 4", str(tree))
    lines = take_out(ledger, verdict)
    assert tree.exists() and "locked it while it was judged" in lines[0]
    assert "locked" in git(root, "worktree", "list", "--porcelain")


def test_a_head_that_moved_since_the_judgement_aborts(tmp_path):
    root, ledger = world(tmp_path)
    tree = seat(root, tmp_path)
    verdict = judged(ledger, tree)
    commit(tree, "new work")
    lines = take_out(ledger, verdict)
    assert tree.exists() and "moved since it was judged" in lines[0]
    assert "locked" not in git(root, "worktree", "list", "--porcelain")


def test_reflog_only_commits_are_parked_before_the_tree_goes(tmp_path):
    root, ledger = world(tmp_path)
    tree = seat(root, tmp_path)
    dropped = commit(tree, "a trial")
    git(tree, "reset", "-q", "--hard", "HEAD~1")
    lines = take_out(ledger, judged(ledger, tree))
    assert not tree.exists() and "parked 1 commit" in lines[0]
    assert git(root, "for-each-ref", "--format=%(objectname)", "refs/flotilla/parked/") == dropped


def test_git_refusing_the_removal_frees_the_branch(tmp_path):
    root, ledger = world(tmp_path)
    tree = seat(root, tmp_path)
    verdict = judged(ledger, tree)
    real = ledger.run

    def run(args, **kwargs):   # git refuses at the very end, as it does for what the judge cannot see
        if args[-2:] == ["remove", str(tree)]:
            return subprocess.CompletedProcess(args, 128, "", "fatal: cannot remove a locked working tree")
        return real(args, **kwargs)
    ledger.run = run
    lines = take_out(ledger, verdict)
    assert tree.exists() and "git refused to remove it" in lines[0]
    ledger.run = real
    git(root, "worktree", "add", "-q", str(tmp_path / "again"), "fleet/main-1")


def test_a_branch_is_deleted_only_at_the_judged_commit_and_parked_first(tmp_path):
    root, ledger = world(tmp_path)
    git(root, "checkout", "-q", "-b", "fleet/main-2")
    tip = commit(root, "squashed")
    git(root, "checkout", "-q", "main")
    verdict = branches.BranchVerdict("fleet/main-2", tip, True, "shipped as a squash", [tip])
    assert remove.delete_branch(ledger, verdict) == "deleted branch `fleet/main-2`"
    assert git(root, "for-each-ref", "--format=%(objectname)", "refs/flotilla/parked/") == tip
    git(root, "branch", "fleet/main-3", tip)
    moved = branches.BranchVerdict("fleet/main-3", git(root, "rev-parse", "HEAD"), True, "", [])
    assert "moved since it was judged" in remove.delete_branch(ledger, moved)


def test_a_file_that_changed_under_the_lock_aborts_the_archive(tmp_path):
    """gc design 7 (stage 2): what is archived is what was judged; a file rewritten since aborts it."""
    from flotilla.gc import archive, twins
    root, ledger = world(tmp_path)
    commit(root, "ignore", ".gitignore", "out/\n")
    tree = seat(root, tmp_path)
    (tree / "out").mkdir()
    (tree / "out" / "x.bin").write_bytes(b"12345")
    finder = twins.Twins(ledger, holders=refs.holders(ledger), cache={},
                         sources=twins.sources(ledger, untouched=[], archive_root=archive.root_of(ledger)))
    verdict = trees.judge_tree(ledger, tree, holders=refs.holders(ledger), sessions=[], twins=finder,
                               proc_root=tree.parent / "no-proc")
    assert verdict.archive and not verdict.removable
    (tree / "out" / "x.bin").write_bytes(b"54321")
    line = remove.remove_tree(ledger, verdict, dropping=set(), sessions=[], proc_root=tree.parent / "no-proc",
                              twins=finder, archive_entry="2026-10-28-fleet-main-1")[0]
    assert tree.exists() and "changed while it was judged" in line, line
    assert not (archive.root_of(ledger) / "objects").exists()
