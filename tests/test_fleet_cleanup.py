"""A seat's tree and branch go when their work is surely on trunk, and only then (the person's decision, 2026-10-02:
"if there is the slightest doubt the work may not be merged - do not delete; if it surely is - delete"). Measured the
same day: 76 seat trees, about 6.9 GB, and 228 local branches already on origin's trunk, left on this machine by
fleets that `down` and `retire` stood down without deleting anything."""

import pytest

from flotilla.fleet import cleanup
from flotilla.ledger import core
from ledgerkit import PROFILE, actor, commit, git, make_ledger, repo_with_origin


def world(tmp_path):
    root = repo_with_origin(tmp_path)
    ledger = make_ledger(root, tmp_path / "state", profile=PROFILE, live=())
    return root, ledger


def seat(root, tmp_path, name="app-main-1", branch="fleet/main-1"):
    tree = tmp_path / name
    git(root, "worktree", "add", "-q", "-b", branch, str(tree))
    return tree


def ship(root, tree, branch):
    """The branch's work reaches origin's trunk by a merge, as a sender's does."""
    git(root, "-c", "user.email=t@example.invalid", "-c", "user.name=t", "merge", "-q", "--no-ff", "--no-edit", branch)
    git(root, "push", "-q", "origin", "HEAD:main")


def test_a_tree_whose_work_is_on_trunk_is_removed_with_its_branch(tmp_path):
    root, ledger = world(tmp_path)
    tree = seat(root, tmp_path)
    commit(tree, "work", "w.txt", "x\n")
    ship(root, tree, "fleet/main-1")
    found = cleanup.judge(ledger, tree)
    assert found.removable, found.reasons
    lines = cleanup.remove(ledger, found)
    assert not tree.exists() and "fleet/main-1" not in git(root, "branch", "--list")
    assert any("removed" in line for line in lines)


def test_regenerable_files_do_not_hold_a_tree(tmp_path):
    root, ledger = world(tmp_path)
    (root / ".gitignore").write_text("node_modules/\n__pycache__/\n.env\n", encoding="utf-8")
    commit(root, "ignore", ".gitignore", "node_modules/\n__pycache__/\n.env\n")
    git(root, "push", "-q", "origin", "HEAD:main")
    tree = seat(root, tmp_path)
    (tree / "node_modules" / "x").mkdir(parents=True)
    (tree / "node_modules" / "x" / "index.js").write_text("1", encoding="utf-8")
    assert cleanup.judge(ledger, tree).removable


@pytest.mark.parametrize("doubt, reason", [
    ("uncommitted", "uncommitted"),
    ("untracked", "uncommitted"),
    ("ignored", ".env"),
    ("unmerged", "not on origin's trunk"),
    ("stash", "stash"),
    ("open row", "open row"),
])
def test_any_doubt_keeps_the_tree_and_names_it(tmp_path, doubt, reason):
    root, ledger = world(tmp_path)
    commit(root, "ignore", ".gitignore", ".env\n")
    git(root, "push", "-q", "origin", "HEAD:main")
    tree = seat(root, tmp_path)
    if doubt == "uncommitted":
        (tree / "README.md").write_text("changed\n", encoding="utf-8")
    elif doubt == "untracked":
        (tree / "notes.txt").write_text("mine\n", encoding="utf-8")
    elif doubt == "ignored":
        (tree / ".env").write_text("TOKEN=secret\n", encoding="utf-8")
    elif doubt == "unmerged":
        commit(tree, "not shipped", "w.txt", "x\n")
    elif doubt == "stash":
        (tree / "README.md").write_text("stashed\n", encoding="utf-8")
        git(tree, "-c", "user.email=t@x", "-c", "user.name=t", "stash", "-q")
    elif doubt == "open row":
        core.claim(ledger, actor(ledger, "main session 1"), "fleet/main-1")
    found = cleanup.judge(ledger, tree)
    assert not found.removable and any(reason in why for why in found.reasons), found.reasons
    assert cleanup.remove(ledger, found) == [] and tree.exists()


def test_a_squash_shipped_branch_counts_when_the_ledger_shipped_that_tip(tmp_path):
    """A squash merge leaves the branch's commits off trunk; the ledger's ship move, which asks origin, carries it."""
    from flotilla.core.storage import LocalLogStore  # noqa: F401
    root, ledger = world(tmp_path)
    tree = seat(root, tmp_path, branch="feat/x")
    tip = commit(tree, "work", "w.txt", "x\n")
    git(root, "merge", "-q", "--squash", "feat/x")
    commit(root, "squashed")
    git(root, "push", "-q", "origin", "HEAD:main")
    assert not cleanup.judge(ledger, tree).removable          # no record: commits not on trunk, kept
    with ledger.session() as s:
        s.append(actor(ledger, "sender 1"), "r9", "ship", "shipped", fields={"branch": "feat/x", "tip": tip})
    found = cleanup.judge(ledger, tree)
    assert found.removable, found.reasons


def test_origin_that_cannot_be_asked_is_a_doubt(tmp_path):
    root, ledger = world(tmp_path)
    tree = seat(root, tmp_path)
    git(root, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    found = cleanup.judge(ledger, tree)
    assert not found.removable and any("origin" in why for why in found.reasons)


def test_the_sweep_removes_what_is_sure_and_lists_the_rest(tmp_path):
    root, ledger = world(tmp_path)
    done = seat(root, tmp_path, "app-main-1", "fleet/main-1")
    commit(done, "work", "w.txt", "x\n")
    ship(root, done, "fleet/main-1")
    kept = seat(root, tmp_path, "app-main-2", "fleet/main-2")
    commit(kept, "not shipped", "y.txt", "y\n")
    git(root, "branch", "fleet/main-7", "HEAD")      # an older seat's home branch, its work on trunk
    git(root, "branch", "old-merged", "HEAD")        # a person's: merged, but not the fleet's to delete
    preview = cleanup.sweep(ledger, sessions=[], act=False)
    assert done.exists() and any("would remove" in line and "app-main-1" in line for line in preview)
    lines = cleanup.sweep(ledger, sessions=[], act=True)
    assert not done.exists() and kept.exists()
    assert any("kept" in line and "app-main-2" in line for line in lines)
    branches = git(root, "branch", "--list")
    assert "fleet/main-7" not in branches and "fleet/main-2" in branches and "main" in branches
    assert "old-merged" in branches


def test_the_sweep_leaves_a_tree_a_live_session_works_in(tmp_path):
    import dataclasses
    from watchkit import sess
    root, ledger = world(tmp_path)
    tree = seat(root, tmp_path)
    someone = dataclasses.replace(sess("worldcore-main-1-x", kind="interactive"), cwd=str(tree))
    lines = cleanup.sweep(ledger, sessions=[someone], act=True)
    assert tree.exists() and any("alive" in line for line in lines)


def test_work_added_after_a_squash_ship_is_a_doubt(tmp_path):
    """The ledger shipped one tip; a commit made after it is on no trunk and in no ship."""
    root, ledger = world(tmp_path)
    tree = seat(root, tmp_path, branch="feat/x")
    tip = commit(tree, "work", "w.txt", "x\n")
    git(root, "merge", "-q", "--squash", "feat/x")
    commit(root, "squashed")
    git(root, "push", "-q", "origin", "HEAD:main")
    with ledger.session() as s:
        s.append(actor(ledger, "sender 1"), "r9", "ship", "shipped", fields={"branch": "feat/x", "tip": tip})
    commit(tree, "more work, after the ship", "more.txt", "y\n")
    found = cleanup.judge(ledger, tree)
    assert not found.removable and any("not on origin's trunk" in why for why in found.reasons)


# --- review of the cleanup (decision 207): each of these lost work in a probe of the first version ---

def test_a_config_that_hides_untracked_files_hides_nothing_from_the_judge(tmp_path):
    """C1: with status.showUntrackedFiles=no, untracked work and an ignored .env were invisible, and removed."""
    root, ledger = world(tmp_path)
    commit(root, "ignore", ".gitignore", ".env\n")
    git(root, "push", "-q", "origin", "HEAD:main")
    git(root, "config", "status.showUntrackedFiles", "no")
    tree = seat(root, tmp_path)
    (tree / "notes.txt").write_text("mine\n", encoding="utf-8")
    assert not cleanup.judge(ledger, tree).removable
    (tree / "notes.txt").unlink()
    (tree / ".env").write_text("TOKEN=1\n", encoding="utf-8")
    assert not cleanup.judge(ledger, tree).removable


def test_only_the_ignored_entry_itself_may_be_regenerable(tmp_path):
    """I1: `deploy/build/prod.env` passed because a parent directory is named `build`."""
    assert not cleanup._regenerable("deploy/build/prod.env")
    assert cleanup._regenerable("node_modules/") and cleanup._regenerable("packages/a/node_modules/")
    assert cleanup._regenerable("src/__pycache__/") and cleanup._regenerable("a/b.pyc")


def test_files_git_is_told_not_to_look_at_are_a_doubt(tmp_path):
    """I2: an edit to a skip-worktree file shows nowhere in status."""
    root, ledger = world(tmp_path)
    tree = seat(root, tmp_path)
    git(tree, "update-index", "--skip-worktree", "README.md")
    (tree / "README.md").write_text("edited where git does not look\n", encoding="utf-8")
    found = cleanup.judge(ledger, tree)
    assert not found.removable and any("not to look" in why for why in found.reasons)


@pytest.mark.parametrize("move_away", ["detach", "reset"])
def test_commits_only_its_reflog_reaches_are_a_doubt(tmp_path, move_away):
    """I3: a commit left behind by a detach or a reset survives only in the tree's or the branch's reflog, which go
    with the tree and the branch."""
    root, ledger = world(tmp_path)
    tree = seat(root, tmp_path)
    if move_away == "detach":   # a commit made on a detached HEAD, then left: no branch ever held it
        git(tree, "checkout", "-q", "--detach")
        commit(tree, "work nobody shipped", "w.txt", "x\n")
        git(tree, "checkout", "-q", "--detach", "origin/main")
    else:
        commit(tree, "work nobody shipped", "w.txt", "x\n")
        git(tree, "reset", "-q", "--hard", "origin/main")
    found = cleanup.judge(ledger, tree)
    assert not found.removable and any("reflog" in why for why in found.reasons)


def test_a_closed_row_that_never_shipped_is_not_trunk(tmp_path):
    """I4: in local flow a row closes after landing on the local trunk only; nothing reached origin."""
    root, ledger = world(tmp_path)
    tree = seat(root, tmp_path, branch="feat/x")
    tip = commit(tree, "work", "w.txt", "x\n")
    with ledger.session() as s:
        s.append(actor(ledger, "main session 1"), "r9", "close", "closed", fields={"branch": "feat/x", "tip": tip})
    assert not cleanup.judge(ledger, tree).removable


def test_the_sweep_leaves_what_flotilla_did_not_make(tmp_path):
    """I5: a person's own worktree and branches were swept like a fleet's."""
    root, ledger = world(tmp_path)
    mine = tmp_path / "my-own-tree"
    git(root, "worktree", "add", "-q", "-b", "person/try", str(mine))
    git(root, "branch", "my-idea", "HEAD")
    lines = cleanup.sweep(ledger, sessions=[], act=True)
    assert mine.exists() and "my-idea" in git(root, "branch", "--list")
    assert any("flotilla did not make" in line for line in lines)


def test_a_branch_that_moved_after_the_judgement_is_not_deleted(tmp_path):
    """M4: the branch is deleted only at the commit it was judged at."""
    root, ledger = world(tmp_path)
    tree = seat(root, tmp_path)
    found = cleanup.judge(ledger, tree)
    assert found.removable
    git(root, "worktree", "remove", str(tree))
    other = tmp_path / "elsewhere"
    git(root, "worktree", "add", "-q", str(other), "fleet/main-1")
    commit(other, "a commit after the judgement", "late.txt", "z\n")
    git(root, "worktree", "remove", str(other))
    git(root, "worktree", "add", "-q", str(tree), "fleet/main-1")
    git(root, "worktree", "remove", "--force", str(tree))
    cleanup._delete_branch(ledger, found.branch, found.tip)
    assert "fleet/main-1" in git(root, "branch", "--list")
