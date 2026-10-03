import pytest

from flotilla.ledger import core, gitq
from flotilla.ledger import tree as tree_mod
from flotilla.ledger.errors import MoveRefused
from ledgerkit import actor, branch, commit, git, make_ledger, repo_with_origin


def test_cut_makes_a_tree_from_origin_trunk_and_claims_it(tmp_path):
    root = repo_with_origin(tmp_path)
    ledger = make_ledger(root, tmp_path / "state")
    target = tmp_path / "app-main-1"
    row = tree_mod.cut(ledger, actor(ledger, "main session 1"), "feat/x", target, ref="LIN-1")
    assert (target / "README.md").is_file()
    assert (row.branch, row.tree, row.ref) == ("feat/x", str(target.resolve()), "LIN-1")
    assert row.base == git(root, "rev-parse", "origin/main")


def test_a_wrong_repository_is_refused_before_anything_is_cut(tmp_path):
    root = repo_with_origin(tmp_path)
    ledger = make_ledger(root, tmp_path / "state")
    target = tmp_path / "app-main-1"
    with pytest.raises(MoveRefused, match="nothing was cut"):
        tree_mod.cut(ledger, actor(ledger, "main session 1"), "feat/x", target, expect="other-repo")
    assert not target.exists() and gitq.branch_tip(root, "feat/x") is None


def test_an_existing_path_is_refused(tmp_path):
    root = repo_with_origin(tmp_path)
    ledger = make_ledger(root, tmp_path / "state")
    target = tmp_path / "taken"
    target.mkdir()
    with pytest.raises(MoveRefused, match="already exists"):
        tree_mod.cut(ledger, actor(ledger, "main session 1"), "feat/x", target)


def test_an_existing_branch_is_refused(tmp_path):
    root = repo_with_origin(tmp_path)
    ledger = make_ledger(root, tmp_path / "state")
    branch(root, "feat/x", "one")
    with pytest.raises(MoveRefused, match="already exists"):
        tree_mod.cut(ledger, actor(ledger, "main session 1"), "feat/x", tmp_path / "t")


def test_a_duplicate_ref_is_refused_before_cutting(tmp_path):
    root = repo_with_origin(tmp_path)
    ledger = make_ledger(root, tmp_path / "state")
    core.claim(ledger, actor(ledger, "main session 1"), "feat/a", ref="LIN-1")
    target = tmp_path / "t"
    with pytest.raises(MoveRefused, match="LIN-1"):
        tree_mod.cut(ledger, actor(ledger, "minor session 1"), "feat/b", target, ref="LIN-1")
    assert not target.exists() and gitq.branch_tip(root, "feat/b") is None


def test_a_failed_worktree_add_leaves_no_branch_behind(tmp_path):
    root = repo_with_origin(tmp_path)
    ledger = make_ledger(root, tmp_path / "state")
    blocker = tmp_path / "a-file"
    blocker.write_text("x", encoding="utf-8")
    with pytest.raises(MoveRefused, match="worktree add failed"):
        tree_mod.cut(ledger, actor(ledger, "main session 1"), "feat/x", blocker / "tree")
    assert gitq.branch_tip(root, "feat/x") is None
    assert ledger.rows() == {}


def test_cut_refuses_to_pick_up_someone_elses_filed_row(tmp_path):
    root = repo_with_origin(tmp_path)
    ledger = make_ledger(root, tmp_path / "state")
    core.claim(ledger, actor(ledger, "main session 1"), "feat/x")
    with pytest.raises(MoveRefused, match="already claimed by main session 1"):
        tree_mod.cut(ledger, actor(ledger, "minor session 1"), "feat/x", tmp_path / "tree")


def home_world(tmp_path):
    root = repo_with_origin(tmp_path)
    ledger = make_ledger(root, tmp_path / "state")
    home = tmp_path / "app-main-1"
    git(root, "worktree", "add", "-q", "-b", "fleet/main-1", str(home), "origin/main")
    core.reserve(ledger, actor(ledger, "main session 1"), "fleet/main-1", tree=str(home))
    return root, ledger, home


def test_switch_moves_the_home_tree_to_a_new_branch_from_trunk_and_claims_it(tmp_path):
    root, ledger, home = home_world(tmp_path)
    row = tree_mod.switch(ledger, actor(ledger, "main session 1"), "feat/x", ref="LIN-1")
    assert (row.state, row.branch, row.tree, row.ref) == ("claimed", "feat/x", str(home.resolve()), "LIN-1")
    assert git(home, "rev-parse", "--abbrev-ref", "HEAD") == "feat/x"
    assert row.base == git(root, "rev-parse", "origin/main")


def test_switch_back_to_your_own_branch_files_no_new_row(tmp_path):
    root, ledger, home = home_world(tmp_path)
    tree_mod.switch(ledger, actor(ledger, "main session 1"), "feat/x")
    commit(home, "work", "work.txt")
    tree_mod.switch(ledger, actor(ledger, "main session 1"), "feat/y")
    again = tree_mod.switch(ledger, actor(ledger, "main session 1"), "feat/x")
    assert again.branch == "feat/x" and git(home, "rev-parse", "--abbrev-ref", "HEAD") == "feat/x"
    assert sorted(row.branch for row in ledger.rows().values() if row.state == "claimed") == ["feat/x", "feat/y"]


def test_switch_refuses_a_home_tree_with_uncommitted_work(tmp_path):
    root, ledger, home = home_world(tmp_path)
    (home / "draft.txt").write_text("unsaved\n", encoding="utf-8")
    with pytest.raises(MoveRefused, match="uncommitted"):
        tree_mod.switch(ledger, actor(ledger, "main session 1"), "feat/x")
    assert git(home, "rev-parse", "--abbrev-ref", "HEAD") == "fleet/main-1" and len(ledger.rows()) == 1


def test_switch_needs_a_home_tree(tmp_path):
    root = repo_with_origin(tmp_path)
    ledger = make_ledger(root, tmp_path / "state")
    with pytest.raises(MoveRefused, match="no home tree"):
        tree_mod.switch(ledger, actor(ledger, "main session 1"), "feat/x")


def test_switch_refuses_a_branch_that_is_someone_elses(tmp_path):
    root, ledger, home = home_world(tmp_path)
    branch(root, "feat/theirs", "their work")
    core.claim(ledger, actor(ledger, "minor session 1"), "feat/theirs")
    with pytest.raises(MoveRefused, match="minor session 1"):
        tree_mod.switch(ledger, actor(ledger, "main session 1"), "feat/theirs")


def test_switch_takes_a_row_of_yours_that_has_no_branch_yet(tmp_path):
    root, ledger, home = home_world(tmp_path)
    filed = core.claim(ledger, actor(ledger, "main session 1"), "fix/x")   # as `broke` files a fix row
    assert filed.base == "" and gitq.branch_tip(root, "fix/x") is None
    row = tree_mod.switch(ledger, actor(ledger, "main session 1"), "fix/x")
    assert row.id == filed.id and row.tree == str(home.resolve())
    assert git(home, "rev-parse", "--abbrev-ref", "HEAD") == "fix/x"
    assert row.base == git(root, "rev-parse", "origin/main")
    assert len([r for r in ledger.rows().values() if r.branch == "fix/x"]) == 1


def test_switch_never_turns_a_delivered_row_back_into_a_claim(tmp_path):
    from ledgerkit import PROFILE, shipped_direct
    root = repo_with_origin(tmp_path)
    ledger = make_ledger(root, tmp_path / "state", profile={**PROFILE, "flow": {"mode": "direct"}})
    home = tmp_path / "app-main-1"
    git(root, "worktree", "add", "-q", "-b", "fleet/main-1", str(home), "origin/main")
    core.reserve(ledger, actor(ledger, "main session 1"), "fleet/main-1", tree=str(home))
    shipped = shipped_direct(root, ledger, "feat/done")
    git(root, "branch", "-D", "feat/done")   # the merged local branch cleaned up
    with pytest.raises(MoveRefused, match="shipped"):
        tree_mod.switch(ledger, actor(ledger, shipped.owner), "feat/done")
    assert ledger.rows()[shipped.id].state == "shipped"


def test_switching_back_to_a_handed_branch_keeps_it_handed(tmp_path):
    from flotilla.ledger import handover
    root, ledger, home = home_world(tmp_path)
    tree_mod.switch(ledger, actor(ledger, "main session 1"), "feat/x")
    commit(home, "work", "work.txt")
    handover.hand(ledger, actor(ledger, "main session 1"), "feat/x")
    tree_mod.switch(ledger, actor(ledger, "main session 1"), "feat/y")
    again = tree_mod.switch(ledger, actor(ledger, "main session 1"), "feat/x")
    assert again.state == "handed" and git(home, "rev-parse", "--abbrev-ref", "HEAD") == "feat/x"


def test_cut_records_an_after_link_and_refuses_an_unknown_one_before_cutting(tmp_path):
    root = repo_with_origin(tmp_path)
    ledger = make_ledger(root, tmp_path / "state")
    first = core.claim(ledger, actor(ledger, "main session 1"), "feat/x")
    with pytest.raises(MoveRefused, match="--after `feat/none`"):
        tree_mod.cut(ledger, actor(ledger, "minor session 1"), "tool/y", tmp_path / "t0", after=["feat/none"])
    assert not (tmp_path / "t0").exists() and gitq.branch_tip(root, "tool/y") is None
    row = tree_mod.cut(ledger, actor(ledger, "minor session 1"), "tool/y", tmp_path / "t1", after=["feat/x"])
    assert (row.after, row.requires) == ([first.id], [])


def test_switch_records_an_after_link(tmp_path):
    root, ledger, home = home_world(tmp_path)
    first = core.claim(ledger, actor(ledger, "minor session 1"), "feat/a")
    row = tree_mod.switch(ledger, actor(ledger, "main session 1"), "tool/b", after=["feat/a"])
    assert (row.after, row.requires) == ([first.id], [])


def test_a_tree_another_session_made_at_the_same_moment_is_never_removed(tmp_path):
    """The path was checked outside the ledger lock; when `worktree add` then failed because another session had
    made that tree, the undo force-removed it (security review F17)."""
    import subprocess as sp
    root = repo_with_origin(tmp_path)
    ledger = make_ledger(root, tmp_path / "state")
    tree = tmp_path / "app-main-1"
    real = ledger.run

    def racing(cmd, **kwargs):
        if cmd[3:5] == ["worktree", "add"]:   # another session's tree lands on the path first
            sp.run(["git", "-C", str(root), "worktree", "add", "-q", "-b", "theirs", str(tree)], check=True,
                   capture_output=True)
            (tree / "theirs.txt").write_text("their work\n", encoding="utf-8")
        return real(cmd, **kwargs)
    ledger.run = racing
    with pytest.raises(MoveRefused):
        tree_mod.cut(ledger, actor(ledger, "main session 1"), "feat/x", tree)
    assert (tree / "theirs.txt").exists()
    assert git(root, "rev-parse", "--verify", "-q", "refs/heads/theirs")


@pytest.mark.parametrize("name", ["feat/x$(touch pwned)", "feat/`id`", "feat/x;rm", "feat/a|b", "feat/{a,b}",
                                  "-feat", "feat/x y", "feat/x'y", "feat/ü"])
def test_a_branch_name_a_shell_would_expand_is_refused_at_the_claim(tmp_path, name):
    """Scan of 0.6.10, F2: git allows `$`, `(`, `|`, braces and backticks in a ref name, and a branch name travels
    into commands a person types (`! flotilla work approve <branch>`). A name is chosen by a session, so it is
    refused where it enters the ledger: claim, reserve, and every move that files a row."""
    root = repo_with_origin(tmp_path)
    ledger = make_ledger(root, tmp_path / "state")
    with pytest.raises(MoveRefused, match="ASCII letters, digits"):
        core.claim(ledger, actor(ledger, "main session 1"), name)
    with pytest.raises(MoveRefused, match="ASCII letters, digits"):
        core.reserve(ledger, actor(ledger, "main session 1"), name)


@pytest.mark.parametrize("name", ["feat/x", "fleet/sender-1", "fix/a.b_c-2", "release/0.6.11", "UPPER/Case",
                                  "feat/a+b", "user@fix", "feat/50%", "feat/k=v,w"])
def test_ordinary_branch_names_are_claimed(tmp_path, name):
    root = repo_with_origin(tmp_path)
    ledger = make_ledger(root, tmp_path / "state")
    assert core.claim(ledger, actor(ledger, "main session 1"), name).branch == name
