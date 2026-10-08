import io
import tarfile

import pytest

from flotilla.rig import transfer
from rigtree import git, git_tree


def archive(tmp_path, members):
    path = tmp_path / "a.tar"
    with tarfile.open(path, mode="w") as t:
        for name, kind, payload in members:
            info = tarfile.TarInfo(name)
            info.type = {"file": tarfile.REGTYPE, "dir": tarfile.DIRTYPE, "symlink": tarfile.SYMTYPE,
                         "hardlink": tarfile.LNKTYPE, "fifo": tarfile.FIFOTYPE}[kind]
            if kind in ("symlink", "hardlink"):
                info.linkname = payload
                t.addfile(info)
            elif kind == "file":
                info.size = len(payload)
                t.addfile(info, io.BytesIO(payload))
            else:
                t.addfile(info)
    return path


@pytest.mark.parametrize("member", [("../x", "file", b"1"), ("/etc/x", "file", b"1"),
                                    ("shots/a", "symlink", "/etc/passwd"), ("shots/b", "hardlink", "x"),
                                    ("shots/f", "fifo", None), ("other/a", "file", b"1"),
                                    ("shotsX/a", "file", b"1"), ("shots/../../x", "file", b"1")])
def test_get_refuses_and_leaves_the_tree_untouched(tmp_path, member):
    tree = tmp_path / "tree"
    (tree / "shots").mkdir(parents=True)
    (tree / "shots" / "old.png").write_bytes(b"old")
    with pytest.raises(transfer.Refused):
        transfer.unpack(tree, archive(tmp_path, [("shots/ok.png", "file", b"new"), member]), ["shots"])
    assert sorted(p.name for p in tree.iterdir()) == ["shots"]
    assert (tree / "shots" / "old.png").read_bytes() == b"old" and not (tree / "shots" / "ok.png").exists()


def test_get_past_the_cap_is_refused(tmp_path):
    with pytest.raises(transfer.Refused):
        transfer.unpack(tmp_path, archive(tmp_path, [("shots/a.png", "file", b"x" * 11)]), ["shots"], cap=10)


def test_get_replaces_the_path_whole(tmp_path):
    tree = tmp_path / "tree"
    (tree / "shots").mkdir(parents=True)
    (tree / "shots" / "stale.png").write_bytes(b"stale")
    placed = transfer.unpack(tree, archive(tmp_path, [("./shots", "dir", None), ("./shots/new.png", "file", b"n")]),
                             ["shots"])
    assert placed == ["shots"] and sorted(p.name for p in (tree / "shots").iterdir()) == ["new.png"]
    assert not any(p.name.startswith(".flotilla-get-") for p in tree.iterdir())


def test_an_empty_archive_brings_nothing(tmp_path):
    (tmp_path / "empty.tar").write_bytes(b"")
    assert transfer.unpack(tmp_path, tmp_path / "empty.tar", ["shots"]) == []


def test_a_symlink_planted_while_the_run_computed_is_refused_at_placement(tmp_path):
    tree = tmp_path / "tree"
    (tree / "real").mkdir(parents=True)
    (tree / "out").symlink_to(tree / "real")
    with pytest.raises(transfer.Refused):
        transfer.unpack(tree, archive(tmp_path, [("out/a", "file", b"1")]), ["out/a"])
    assert list((tree / "real").iterdir()) == []


@pytest.mark.parametrize("bad", [".", "./", "shots/..", ".git/hooks", "a/.git/x", ".claude", "tracked.txt",
                                 "link/inside", "node_modules", "app/.venv", ".envrc", ".husky/pre-commit"])
def test_a_get_path_that_reaches_code_is_refused(tmp_path, bad):
    tree = git_tree(tmp_path / "t", {"tracked.txt": "t"})
    (tree / "real").mkdir()
    (tree / "link").symlink_to(tree / "real")
    with pytest.raises(transfer.Refused):
        transfer.check_gets(tree, [bad])


def test_an_untracked_artifact_path_is_allowed(tmp_path):
    tree = git_tree(tmp_path / "t", {"tracked.txt": "t"})
    assert transfer.check_gets(tree, ["shots", "out/frames"]) == ["shots", "out/frames"]


@pytest.mark.parametrize("name", [".env", ".env.local", "keys/id_ed25519", "cert.PEM", "credentials.json", ".npmrc",
                                  ".netrc", "sub/.pypirc", ".git/config"])
def test_put_refuses_secrets(tmp_path, name):
    (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / name).write_text("s")
    with pytest.raises(transfer.Refused):
        transfer.put_tar(tmp_path, [name], forbidden_roots=())


def test_put_refuses_a_symlink_even_inside_a_directory(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "link").symlink_to("/etc/passwd")
    with pytest.raises(transfer.Refused):
        transfer.put_tar(tmp_path, ["data"], forbidden_roots=())


def test_put_refuses_a_file_under_a_forbidden_root(tmp_path):
    secret = tmp_path / "state"
    secret.mkdir()
    (secret / "vast.key").write_text("k")
    with pytest.raises(transfer.Refused):
        transfer.put_tar(tmp_path, ["state/vast.key"], forbidden_roots=(secret,))


def test_put_packs_files_and_directories(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "a.bin").write_bytes(b"a")
    (tmp_path / "b.txt").write_text("b")
    with tarfile.open(fileobj=io.BytesIO(transfer.put_tar(tmp_path, ["data", "b.txt"], forbidden_roots=()))) as t:
        assert sorted(m.name for m in t.getmembers() if m.isfile()) == ["b.txt", "data/a.bin"]


@pytest.mark.parametrize("bad", ["/etc/passwd", "../x", "", "."])
def test_a_path_outside_the_tree_or_the_root_is_refused(tmp_path, bad):
    with pytest.raises(transfer.Refused):
        transfer.relative(tmp_path, bad)


def test_a_changed_tracked_file_must_be_put(tmp_path):
    tree = git_tree(tmp_path / "t", {"a.txt": "1"})
    (tree / "a.txt").write_text("changed")
    with pytest.raises(transfer.Refused, match="a.txt"):
        transfer.gate(tree, [])
    assert len(transfer.gate(tree, ["a.txt"])) == 40


def test_root_must_be_the_callers_tree(tmp_path):
    mine = git_tree(tmp_path / "mine", {"a": "1"})
    other = git_tree(tmp_path / "other", {"b": "2"})
    git(mine, "worktree", "add", "-q", str(tmp_path / "peer"), "-b", "peer")
    assert transfer.same_repository(mine, mine) == mine.resolve()
    with pytest.raises(transfer.Refused):
        transfer.same_repository(other, mine)
    with pytest.raises(transfer.Refused):
        transfer.same_repository(tmp_path / "peer", mine)


def test_the_project_slug_is_safe_and_stable(tmp_path):
    slug = transfer.project_slug(tmp_path / "two suns!", "github:x/twosuns")
    assert slug == transfer.project_slug(tmp_path / "two suns!", "github:x/twosuns")
    assert all(c.isalnum() or c in "._-" for c in slug) and slug.startswith("two-suns-")


@pytest.mark.parametrize("member", ["shots/.envrc", "shots/.git/hooks/pre-commit", "shots/node_modules/x/index.js",
                                    "shots/sub/.claude/settings.json", "shots/.github/workflows/x.yml"])
def test_an_archive_member_that_plants_code_is_refused(tmp_path, member):
    tree = tmp_path / "tree"
    tree.mkdir()
    with pytest.raises(transfer.Refused):
        transfer.unpack(tree, archive(tmp_path, [("shots/a.png", "file", b"1"), (member, "file", b"x")]), ["shots"])
    assert list(tree.iterdir()) == []


@pytest.mark.parametrize("bad", [".github/workflows", ".vscode", ".flotilla/posts", ".idea", ".gitattributes",
                                 ".pre-commit-config.yaml", ".gitmodules"])
def test_a_get_path_where_tools_run_code_is_refused(tmp_path, bad):
    tree = git_tree(tmp_path / "t", {"tracked.txt": "t"})
    with pytest.raises(transfer.Refused):
        transfer.check_gets(tree, [bad])


def test_put_refuses_a_symlink_that_points_inside_the_tree(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "b.txt").write_text("b")
    (tmp_path / "data" / "link").symlink_to(tmp_path / "b.txt")
    with pytest.raises(transfer.Refused, match="symlink"):
        transfer.put_tar(tmp_path, ["data"], forbidden_roots=())


def test_a_get_path_that_differs_from_a_tracked_one_only_by_case_is_refused(tmp_path):
    tree = git_tree(tmp_path / "t", {"src/App.py": "code"})
    with pytest.raises(transfer.Refused):
        transfer.check_gets(tree, ["src/app.py"])


def test_nested_gets_keep_every_file(tmp_path):
    tree = tmp_path / "tree"
    tree.mkdir()
    placed = transfer.unpack(tree, archive(tmp_path, [("out/a.png", "file", b"a"), ("out/sub/inner.txt", "file", b"i")]),
                             ["out/sub", "out"])
    assert (tree / "out/sub/inner.txt").read_bytes() == b"i" and (tree / "out/a.png").read_bytes() == b"a"
    assert placed == ["out"]


@pytest.mark.parametrize("name", ["certs/server.key", ".ssh/config", ".aws/credentials", ".git-credentials",
                                  "store.p12", "store.pfx", ".kube/config", ".docker/config.json"])
def test_put_refuses_more_secrets(tmp_path, name):
    (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / name).write_text("s")
    with pytest.raises(transfer.Refused):
        transfer.put_tar(tmp_path, [name], forbidden_roots=())


def test_put_refuses_a_hard_link(tmp_path):
    import os
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret").write_text("s")
    tree = tmp_path / "tree"
    tree.mkdir()
    os.link(outside / "secret", tree / "data.bin")
    with pytest.raises(transfer.Refused):
        transfer.put_tar(tree, ["data.bin"], forbidden_roots=())


@pytest.mark.parametrize("member", ["out/conftest.py", "out/x.pth", "out/sitecustomize.py", "out/usercustomize.py",
                                    "out/.npmrc", "out/.yarnrc.yml", "out/.pnpmfile.cjs"])
def test_an_archive_member_a_tool_runs_by_its_name_is_refused(tmp_path, member):
    tree = tmp_path / "tree"
    tree.mkdir()
    with pytest.raises(transfer.Refused):
        transfer.unpack(tree, archive(tmp_path, [("out/a.png", "file", b"1"), (member, "file", b"x")]), ["out"])
    assert list(tree.iterdir()) == []


def test_what_comes_back_is_never_executable(tmp_path):
    path = tmp_path / "a.tar"
    with tarfile.open(path, mode="w") as t:
        info = tarfile.TarInfo("out/frame.png")
        info.size, info.mode = 2, 0o755
        t.addfile(info, io.BytesIO(b"x\n"))
    tree = tmp_path / "tree"
    tree.mkdir()
    transfer.unpack(tree, path, ["out"])
    assert (tree / "out/frame.png").stat().st_mode & 0o111 == 0


def test_a_compressed_archive_is_refused(tmp_path):
    path = tmp_path / "a.tar.gz"
    with tarfile.open(path, mode="w:gz") as t:
        info = tarfile.TarInfo("out/a")
        info.size = 1
        t.addfile(info, io.BytesIO(b"x"))
    with pytest.raises(transfer.Refused):
        transfer.unpack(tmp_path, path, ["out"])


def test_too_many_members_are_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(transfer, "MAX_MEMBERS", 3)
    path = archive(tmp_path, [(f"out/{n}", "file", b"x") for n in range(4)])
    with pytest.raises(transfer.Refused):
        transfer.unpack(tmp_path, path, ["out"])


def test_only_artifact_types_come_back_and_the_rest_is_named(tmp_path):
    tree = tmp_path / "tree"
    tree.mkdir()
    placed, skipped = transfer.unpack(tree, archive(tmp_path, [
        ("out/frame.png", "file", b"p"), ("out/report.json", "file", b"{}"), ("out/jest.config.js", "file", b"x"),
        ("out/Makefile", "file", b"x"), ("out/sub/run.sh", "file", b"x")]), ["out"], skipped=True)
    assert sorted(p.name for p in (tree / "out").rglob("*") if p.is_file()) == ["frame.png", "report.json"]
    assert sorted(skipped) == ["out/Makefile", "out/jest.config.js", "out/sub/run.sh"]


def test_a_project_may_add_a_type(tmp_path):
    tree = tmp_path / "tree"
    tree.mkdir()
    placed, skipped = transfer.unpack(tree, archive(tmp_path, [("out/model.onnx", "file", b"m")]), ["out"],
                                      types=transfer.ARTIFACT_TYPES + ("onnx",), skipped=True)
    assert (tree / "out/model.onnx").exists() and skipped == []


def test_a_staged_rename_names_both_files_whole(tmp_path):
    from rigtree import git
    tree = git_tree(tmp_path / "t", {"alpha.txt": "1"})
    git(tree, "mv", "alpha.txt", "beta.txt")
    assert sorted(transfer.unsent(tree)) == ["alpha.txt", "beta.txt"]


def test_a_directory_put_covers_the_changed_files_under_it(tmp_path):
    tree = git_tree(tmp_path / "t", {"data/a.txt": "1"})
    (tree / "data" / "a.txt").write_text("changed")
    assert len(transfer.gate(tree, ["data"])) == 40
