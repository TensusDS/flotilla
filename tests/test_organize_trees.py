from pathlib import Path

from flotilla.organize import trees
from flotilla.organize import walk as walking
from ledgerkit import git
from organizekit import MERGE_PROFILE, Hub, project
from test_fleet_leftovers import fake_proc


def a_tree(tmp_path, name="other", locked=False):
    return trees.Tree(path=(tmp_path / name).resolve(), branch="docs/x", locked=locked)


def test_a_live_process_inside_makes_a_tree_in_use(tmp_path):
    tree = a_tree(tmp_path)
    (tree.path / "src").mkdir(parents=True)
    fake_proc(tmp_path / "proc", 700, ppid=1, cwd=str(tree.path / "src"), command="codex run", tty=34816)
    said = trees.in_use(tree, seats=[], me=tmp_path / "app", proc_root=tmp_path / "proc")
    assert "process 700" in said


def test_an_idle_tree_is_free(tmp_path):
    tree = a_tree(tmp_path)
    tree.path.mkdir(parents=True)
    fake_proc(tmp_path / "proc", 700, ppid=1, cwd=str(tmp_path), command="bash")
    assert trees.in_use(tree, seats=[], me=tmp_path / "app", proc_root=tmp_path / "proc") == ""


def test_a_locked_tree_and_a_seat_tree_are_in_use(tmp_path):
    (tmp_path / "proc").mkdir()
    locked = a_tree(tmp_path, locked=True)
    seat = a_tree(tmp_path, "seat")
    assert trees.in_use(locked, seats=[], me=tmp_path / "app", proc_root=tmp_path / "proc") == "locked"
    assert trees.in_use(seat, seats=[seat.path], me=tmp_path / "app", proc_root=tmp_path / "proc") == "a flotilla seat"


def test_without_procfs_a_tree_counts_as_in_use(tmp_path):
    said = trees.in_use(a_tree(tmp_path), seats=[], me=tmp_path / "app", proc_root=tmp_path / "no-proc")
    assert "procfs" in said


def test_the_organizing_tree_is_never_in_use(tmp_path):
    me = a_tree(tmp_path, "app")
    me.path.mkdir(parents=True)
    fake_proc(tmp_path / "proc", 700, ppid=1, cwd=str(me.path), command="claude")
    assert trees.in_use(me, seats=[], me=me.path, proc_root=tmp_path / "proc") == ""


def test_worktrees_lists_every_tree_with_its_branch_and_lock(tmp_path):
    root = project(tmp_path)
    git(root, "branch", "docs/x")
    git(root, "worktree", "add", "-q", str(tmp_path / "other"), "docs/x")
    git(root, "worktree", "lock", str(tmp_path / "other"))
    found = {tree.path.name: tree for tree in trees.worktrees(root)}
    assert found["other"].branch == "docs/x" and found["other"].locked
    assert not found["app"].locked


def _walk(root, proc_root):
    return walking.walk(Path(root), rows={}, profile=MERGE_PROFILE, run=Hub().run, slug="o/r", proc_root=proc_root)


def test_an_idle_other_tree_is_offered_behind_a_yes(tmp_path):
    root = project(tmp_path)
    git(root, "branch", "docs/x")
    git(root, "worktree", "add", "-q", str(tmp_path / "other"), "docs/x")
    (tmp_path / "other" / "draft.md").write_text("half\n", encoding="utf-8")
    (tmp_path / "proc").mkdir()
    found = _walk(root, tmp_path / "proc")
    item = next(item for item in found.items if item.kind == "edits")
    assert item.facts["tree"] == str((tmp_path / "other").resolve()) and item.facts["needs_yes"] is True
    assert found.listed == []


def test_a_tree_in_use_is_listed_and_its_edits_are_not_offered(tmp_path):
    root = project(tmp_path)
    git(root, "branch", "docs/x")
    git(root, "worktree", "add", "-q", str(tmp_path / "other"), "docs/x")
    (tmp_path / "other" / "draft.md").write_text("half\n", encoding="utf-8")
    fake_proc(tmp_path / "proc", 700, ppid=1, cwd=str((tmp_path / "other").resolve()), command="codex")
    found = _walk(root, tmp_path / "proc")
    assert not [item for item in found.items if item.kind == "edits"]
    assert any("in use" in line and "other" in line for line in found.listed)


def test_a_tree_with_another_ais_files_is_listed(tmp_path):
    root = project(tmp_path)
    git(root, "branch", "docs/codex-project-skills")
    git(root, "worktree", "add", "-q", str(tmp_path / "codex"), "docs/codex-project-skills")
    (tmp_path / "codex" / ".agents").mkdir()
    (tmp_path / "codex" / ".agents" / "skill.md").write_text("x\n", encoding="utf-8")
    (tmp_path / "codex" / "CODEX-TO-CLAUDE.md").write_text("x\n", encoding="utf-8")
    (tmp_path / "proc").mkdir()
    found = _walk(root, tmp_path / "proc")
    assert not [item for item in found.items if item.kind == "edits"]
    assert any("codex" in line and "another AI" in line for line in found.listed)


def test_a_process_of_mine_whose_directory_cannot_be_read_keeps_the_tree_untouched(tmp_path):
    """Background security review of 4ec6c7c: an entry procfs listed but whose working directory could not be read
    was skipped, and the tree read as free. One's own process that cannot be read is "cannot tell", not "nobody"."""
    tree = a_tree(tmp_path)
    tree.path.mkdir(parents=True)
    (tmp_path / "proc" / "700").mkdir(parents=True)   # listed, no cwd link: reading it fails
    said = trees.in_use(tree, seats=[], me=tmp_path / "app", proc_root=tmp_path / "proc")
    assert "cannot be told" in said and "700" in said
