import dataclasses
from pathlib import Path

from flotilla.ledger import project
from watchkit import row, rows, sess


def at(name, cwd, **kw):
    return dataclasses.replace(sess(name, **kw), cwd=cwd)


ROOTS = [Path("/work/snake"), Path("/work/snake-main-1")]


def test_a_session_in_a_worktree_of_this_repo_is_of_this_project():
    found = project.members([at("main session 1", "/work/snake-main-1/src")], {}, ROOTS)
    assert [s.name for s in found] == ["main session 1"]


def test_a_session_in_the_main_checkout_is_of_this_project():
    assert project.members([at("orchestrator 1", "/work/snake")], {}, ROOTS)[0].name == "orchestrator 1"


def test_a_session_of_another_repo_with_a_matching_name_is_not():
    assert project.members([at("acceptance judge 1", "/work/ai-os")], {}, ROOTS) == []


def test_an_owner_elsewhere_on_disk_is_of_this_project():
    mine = at("main session 7", "/home/max")
    assert project.members([mine], rows(row(owner="main session 7")), ROOTS) == [mine]


def test_a_prefix_of_the_root_is_not_inside_it():
    assert project.members([at("x", "/work/snake-origin")], {}, [Path("/work/snake")]) == []


def test_roots_lists_the_main_checkout_and_its_worktrees(tmp_path):
    from ledgerkit import git, repo_with_origin
    root = repo_with_origin(tmp_path)
    git(root, "worktree", "add", "-q", "-b", "side", str(tmp_path / "side"))
    assert set(project.roots(root)) == {root.resolve(), (tmp_path / "side").resolve()}
