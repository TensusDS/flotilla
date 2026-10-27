"""One pass of the collector (gc design, sections 5 and 9): branches judged first, then trees against the refs that
survive; one report by reason, never one line per kept tree."""

from flotilla.gc import sweep
from ledgerkit import IDENTITY, PROFILE, actor, commit, git, make_ledger, repo_with_origin


def world(tmp_path, profile=None):
    root = repo_with_origin(tmp_path)
    return root, make_ledger(root, tmp_path / "state", profile=profile or PROFILE, live=())


def refs_and_trees(root):
    return git(root, "for-each-ref", "--format=%(refname) %(objectname)"), git(root, "worktree", "list")


def test_a_fleet_like_the_field_one_is_mostly_cleaned(tmp_path):
    root, ledger = world(tmp_path, {**PROFILE, "gc": {"trunks": ["proto/x"]}})
    commit(root, "ignore", ".gitignore", "tools/crystal3d/\n")
    git(root, "push", "-q", "origin", "HEAD:main")
    git(root, "branch", "proto/x")

    def seat(name, *args):
        tree = tmp_path / f"app-{name}"
        git(root, "worktree", "add", "-q", *args, str(tree), *([] if "-b" in args else ["main"]))
        return tree
    dead = seat("main-1", "-b", "fleet/main-1")                    # a dead seat on an unlanded branch
    unlanded = commit(dead, "unlanded work", "w.txt", "x\n")
    reviewer = seat("reviewer-1", "--detach")                      # a reviewer's checkout of a commit
    git(reviewer, "checkout", "-q", "--detach", unlanded)
    trial = seat("main-2", "-b", "fleet/main-2")                   # reflog-only commits
    dropped = commit(trial, "a trial merge")
    git(trial, "reset", "-q", "--hard", "HEAD~1")
    cache = seat("main-3", "-b", "fleet/main-3")                   # the folded `__pycache__` of the field bug
    (cache / "tools" / "crystal3d" / "__pycache__").mkdir(parents=True)
    (cache / "tools" / "crystal3d" / "__pycache__" / "a.pyc").write_bytes(b"x")
    dirty = seat("main-4", "-b", "fleet/main-4")                   # untracked work
    (dirty / "notes.txt").write_text("mine\n", encoding="utf-8")
    sole = seat("main-6", "--detach")                              # the only holder of its HEAD
    commit(sole, "only here")
    git(root, "checkout", "-q", "-b", "feat/s")                    # the sender's batch branch, shipped
    commit(root, "shipped work")
    git(root, "checkout", "-q", "main")
    git(root, *IDENTITY, "merge", "-q", "--no-ff", "--no-edit", "feat/s")
    git(root, "push", "-q", "origin", "HEAD:main")
    merge = git(root, "rev-parse", "HEAD")
    git(root, "branch", "batch/1", merge)
    with ledger.session() as s:
        s.append(actor(ledger, "sender 1"), "r5", "ship", "shipped", fields={"branch": "feat/s", "merge": merge})
    git(root, "checkout", "-q", "-b", "fleet/line-1", "proto/x")   # a branch landed on the declared trunk
    commit(root, "on the line")
    git(root, "checkout", "-q", "proto/x")
    git(root, *IDENTITY, "merge", "-q", "--no-ff", "--no-edit", "fleet/line-1")
    git(root, "checkout", "-q", "main")

    report = sweep.run_pass(ledger, sessions=[], act=True)

    assert not dead.exists() and not reviewer.exists() and not trial.exists() and not cache.exists()
    assert dirty.exists() and sole.exists()
    listed = git(root, "branch", "--list", "--format=%(refname:short)").split()
    assert "batch/1" not in listed and "fleet/line-1" not in listed and "fleet/main-3" not in listed
    assert "fleet/main-1" in listed and "fleet/main-4" in listed and "proto/x" in listed
    assert dropped in git(root, "for-each-ref", "--format=%(objectname)", "refs/flotilla/parked/").split()
    lines = sweep.lines(report)
    assert lines[0].startswith("removed 4 trees")
    kept = [line for line in lines if line.startswith("kept ") and " tree" in line.split(":")[0]]
    assert len(kept) == 2, lines
    assert any("only on this disk" in line for line in lines)


def test_a_missing_tree_is_named_not_pruned(tmp_path):
    root, ledger = world(tmp_path)
    tree = tmp_path / "app-main-1"
    git(root, "worktree", "add", "-q", "-b", "fleet/main-1", str(tree))
    import shutil
    shutil.rmtree(tree)
    report = sweep.run_pass(ledger, sessions=[], act=True)
    assert str(tree) in report.missing
    assert str(tree) in git(root, "worktree", "list")
    assert any("not pruned" in line for line in sweep.lines(report))


def test_without_yes_nothing_changes(tmp_path):
    root, ledger = world(tmp_path)
    tree = tmp_path / "app-main-1"
    git(root, "worktree", "add", "-q", "-b", "fleet/main-1", str(tree))
    commit(tree, "a trial")
    git(tree, "reset", "-q", "--hard", "HEAD~1")
    before = refs_and_trees(root)
    report = sweep.run_pass(ledger, sessions=[], act=False)
    assert refs_and_trees(root) == before
    assert sweep.lines(report)[0].startswith("would remove 1 tree")


def test_many_kept_trees_of_one_reason_are_one_line(tmp_path):
    root, ledger = world(tmp_path)
    for index in range(1, 9):
        tree = tmp_path / f"app-main-{index}"
        git(root, "worktree", "add", "-q", "-b", f"fleet/main-{index}", str(tree))
        (tree / "notes.txt").write_text("mine\n", encoding="utf-8")
    lines = sweep.lines(sweep.run_pass(ledger, sessions=[], act=True))
    kept = [line for line in lines if line.startswith("kept 8 trees")]
    assert len(kept) == 1 and "and 3 more" in kept[0], lines



def test_a_branch_a_staying_tree_is_rebasing_is_kept(tmp_path):
    """Final review of gc stage 1, I4: during a rebase `git worktree list` shows the tree detached, so its landed
    branch read as nobody's and was deleted under the rebase."""
    import subprocess
    root, ledger = world(tmp_path)
    tree = tmp_path / "app-main-1"
    git(root, "worktree", "add", "-q", "-b", "fleet/main-1", str(tree))
    commit(tree, "landed work", "a.txt", "1\n")
    git(root, "merge", "-q", "--ff-only", "fleet/main-1")
    git(root, "push", "-q", "origin", "HEAD:main")
    subprocess.run(["git", "-C", str(tree), *IDENTITY, "-c", "sequence.editor=sed -i 1ibreak", "rebase", "-i",
                    "HEAD~1"], capture_output=True, text=True, check=True)
    report = sweep.run_pass(ledger, sessions=[], act=True, proc_root=tmp_path / "no-proc")
    assert tree.exists()
    assert "fleet/main-1" in git(root, "branch", "--list", "--format=%(refname:short)").split()
    assert "fleet/main-1" not in report.deleted
