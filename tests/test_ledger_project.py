import dataclasses
from pathlib import Path

from flotilla.ledger import project
from watchkit import row, rows, sess


def at(name, cwd, **kw):
    return dataclasses.replace(sess(name, **kw), cwd=cwd)


ROOTS = [Path("/work/snake"), Path("/work/snake-main-1")]


def checkouts(tmp_path):
    """A main checkout and a worktree on disk: membership reads where `.git` is."""
    main, tree = tmp_path / "snake", tmp_path / "snake-main-1"
    (main / ".git").mkdir(parents=True)
    tree.mkdir()
    (tree / ".git").write_text("gitdir: elsewhere\n", encoding="utf-8")   # a worktree's .git is a file
    (tree / "src").mkdir()
    return main, tree, [main.resolve(), tree.resolve()]


def test_a_session_in_a_worktree_of_this_repo_is_of_this_project(tmp_path):
    main, tree, roots = checkouts(tmp_path)
    found = project.members([at("main session 1", str(tree / "src"))], {}, roots)
    assert [s.name for s in found] == ["main session 1"]


def test_a_session_in_the_main_checkout_is_of_this_project(tmp_path):
    main, tree, roots = checkouts(tmp_path)
    assert project.members([at("orchestrator 1", str(main))], {}, roots)[0].name == "orchestrator 1"


def test_a_session_of_another_repo_with_a_matching_name_is_not():
    assert project.members([at("acceptance judge 1", "/work/other-project")], {}, ROOTS) == []


def test_an_owner_elsewhere_on_disk_is_of_this_project():
    mine = at("main session 7", "/home/user")
    assert project.members([mine], rows(row(owner="main session 7")), ROOTS) == [mine]


def test_a_prefix_of_the_root_is_not_inside_it():
    assert project.members([at("x", "/work/snake-origin")], {}, [Path("/work/snake")]) == []


def test_roots_lists_the_main_checkout_and_its_worktrees(tmp_path):
    from ledgerkit import git, repo_with_origin
    root = repo_with_origin(tmp_path)
    git(root, "worktree", "add", "-q", "-b", "side", str(tmp_path / "side"))
    assert set(project.roots(root)) == {root.resolve(), (tmp_path / "side").resolve()}


def test_a_nested_repository_inside_the_root_is_not_of_this_project(tmp_path):
    outer = tmp_path / "outer"
    (outer / ".git").mkdir(parents=True)
    inner = outer / "core"
    (inner / ".git").mkdir(parents=True)
    (inner / "src").mkdir()
    found = project.members([at("acceptance judge 1", str(inner / "src")), at("main session 1", str(outer))],
                            {}, [outer.resolve()])
    assert [s.name for s in found] == ["main session 1"]


def test_a_relative_working_directory_is_not_inside():
    assert project.members([at("x", ".")], {}, [Path.cwd().resolve()]) == []
