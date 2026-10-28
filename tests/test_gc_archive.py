"""The archive (gc design, section 7): files moved, not copied; identical content stored once; a manifest in the
person's line shape; one restore, into a fresh tree of the parked commit when the tree is gone."""

import errno
import hashlib
import re
import subprocess

import pytest

from flotilla.gc import archive, park
from ledgerkit import PROFILE, commit, git, make_ledger, repo_with_origin

DAY = "2026-10-28"


def world(tmp_path):
    root = repo_with_origin(tmp_path)
    return root, make_ledger(root, tmp_path / "state", profile=PROFILE, live=())


def seat(root, tmp_path, name="app-main-1", branch="fleet/main-1"):
    tree = tmp_path / name
    git(root, "worktree", "add", "-q", "-b", branch, str(tree))
    return tree


def put_file(tree, path, data: bytes):
    target = tree / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return target


def stash(ledger, tree, files, *, entry=None, patch=b""):
    head = git(tree, "rev-parse", "HEAD").strip()
    return archive.put(ledger, entry=entry or archive.entry_id("work:fleet/main-1", DAY), tree=tree, files=files,
                       tier="2", approved_by="told after: small", branch="fleet/main-1", head=head, patch=patch)


def test_files_move_and_the_manifest_names_hash_now_and_from(tmp_path):
    root, ledger = world(tmp_path)
    tree = seat(root, tmp_path)
    original = put_file(tree, "out/big.bin", b"payload")
    stash(ledger, tree, ["out/big.bin"])
    assert not original.exists()
    lines = (archive.root_of(ledger) / "MANIFEST").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert re.match(r"^[0-9a-f]{64}  objects/[0-9a-f]{2}/[0-9a-f]{64}  <-  /.+/out/big\.bin$", lines[0]), lines
    digest = hashlib.sha256(b"payload").hexdigest()
    assert (archive.root_of(ledger) / "objects" / digest[:2] / digest).read_bytes() == b"payload"


def test_the_archive_sits_beside_the_main_checkout(tmp_path):
    root, ledger = world(tmp_path)
    assert archive.root_of(ledger) == root.parent / f"{root.name}-gc-archive"


def test_identical_content_is_stored_once(tmp_path):
    root, ledger = world(tmp_path)
    first, second = seat(root, tmp_path), seat(root, tmp_path, "app-main-2", "fleet/main-2")
    put_file(first, "a.bin", b"same")
    put_file(second, "b.bin", b"same")
    stash(ledger, first, ["a.bin"])
    stash(ledger, second, ["b.bin"], entry=archive.entry_id("work:fleet/main-2", DAY))
    objects = [path for path in (archive.root_of(ledger) / "objects").rglob("*") if path.is_file()]
    assert len(objects) == 1
    assert len((archive.root_of(ledger) / "MANIFEST").read_text(encoding="utf-8").splitlines()) == 2


def test_across_filesystems_it_copies_verifies_then_deletes(tmp_path, monkeypatch):
    root, ledger = world(tmp_path)
    tree = seat(root, tmp_path)
    original = put_file(tree, "a.bin", b"payload")

    def cross(*_args, **_kwargs):
        raise OSError(errno.EXDEV, "Invalid cross-device link")

    monkeypatch.setattr(archive.os, "rename", cross)
    stash(ledger, tree, ["a.bin"])
    digest = hashlib.sha256(b"payload").hexdigest()
    assert not original.exists()
    assert (archive.root_of(ledger) / "objects" / digest[:2] / digest).read_bytes() == b"payload"


def test_a_copy_that_does_not_verify_leaves_the_original(tmp_path, monkeypatch):
    root, ledger = world(tmp_path)
    tree = seat(root, tmp_path)
    original = put_file(tree, "a.bin", b"payload")

    def cross(*_args, **_kwargs):
        raise OSError(errno.EXDEV, "Invalid cross-device link")

    def bad_copy(_source, target, **_kwargs):
        with open(target, "wb") as handle:
            handle.write(b"corrupt")

    monkeypatch.setattr(archive.os, "rename", cross)
    monkeypatch.setattr(archive.shutil, "copy2", bad_copy)
    with pytest.raises(archive.ArchiveFailed):
        stash(ledger, tree, ["a.bin"])
    assert original.read_bytes() == b"payload"
    digest = hashlib.sha256(b"payload").hexdigest()
    assert not (archive.root_of(ledger) / "objects" / digest[:2] / digest).exists()


def test_a_symlink_is_archived_as_a_link(tmp_path):
    root, ledger = world(tmp_path)
    tree = seat(root, tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"not ours")
    link = tree / "link.txt"
    link.symlink_to(outside)
    entry = stash(ledger, tree, ["link.txt"])
    stored = archive.root_of(ledger) / "objects" / entry.files[0]["sha256"][:2] / entry.files[0]["sha256"]
    assert stored.is_symlink() and str(stored.readlink()) == str(outside)
    assert outside.read_bytes() == b"not ours" and not link.is_symlink()


def test_restore_puts_files_back_and_names_a_conflict(tmp_path):
    root, ledger = world(tmp_path)
    tree = seat(root, tmp_path)
    put_file(tree, "a.bin", b"first")
    put_file(tree, "b.bin", b"second")
    entry = stash(ledger, tree, ["a.bin", "b.bin"])
    put_file(tree, "b.bin", b"someone else's")
    lines = archive.restore(ledger, entry.id)
    assert (tree / "a.bin").read_bytes() == b"first"
    assert (tree / "b.bin").read_bytes() == b"someone else's"
    assert any("not overwritten" in line and "b.bin" in line for line in lines), lines


def test_restore_into_a_fresh_tree_at_the_parked_head(tmp_path):
    root, ledger = world(tmp_path)
    tree = seat(root, tmp_path)
    head = commit(tree, "unlanded work", "w.txt", "w\n")
    (tree / "w.txt").write_text("edited\n", encoding="utf-8")
    patch = subprocess.run(["git", "-C", str(tree), "diff", "--binary", "HEAD"], capture_output=True,
                           check=True).stdout
    put_file(tree, "render.exr", b"pixels")
    stash(ledger, tree, ["render.exr"], patch=patch)
    park.park(ledger, tree.name, [head])
    git(root, "worktree", "remove", "--force", str(tree))
    git(root, "branch", "-D", "fleet/main-1")
    lines = archive.restore(ledger, tree.name)
    assert git(tree, "rev-parse", "HEAD").strip() == head, lines
    assert (tree / "render.exr").read_bytes() == b"pixels"
    assert (tree / "w.txt").read_text(encoding="utf-8") == "edited\n"


def test_one_entry_per_piece_of_work_per_day(tmp_path):
    root, ledger = world(tmp_path)
    tree = seat(root, tmp_path)
    put_file(tree, "a.bin", b"a")
    put_file(tree, "b.bin", b"b")
    stash(ledger, tree, ["a.bin"])
    stash(ledger, tree, ["b.bin"])
    found = archive.entries(ledger)
    assert len(found) == 1 and len(found[0].files) == 2
    assert archive.entry_id("work:fleet/main-1", DAY) == f"{DAY}-fleet-main-1"


# Security review of 75eff4b: nothing is taken from, or written to, outside the tree it belongs to.

@pytest.mark.parametrize("path", ["../escape.bin", "/etc/hostname", "inner/../../escape.bin"])
def test_put_refuses_a_path_outside_the_tree(tmp_path, path):
    root, ledger = world(tmp_path)
    tree = seat(root, tmp_path)
    (tmp_path / "escape.bin").write_bytes(b"not the tree's")
    with pytest.raises(archive.ArchiveFailed):
        stash(ledger, tree, [path])
    assert (tmp_path / "escape.bin").read_bytes() == b"not the tree's"


def test_put_refuses_a_file_behind_a_linked_directory(tmp_path):
    root, ledger = world(tmp_path)
    tree = seat(root, tmp_path)
    put_file(tmp_path / "outside", "x.bin", b"not the tree's")
    (tree / "out").symlink_to(tmp_path / "outside", target_is_directory=True)
    with pytest.raises(archive.ArchiveFailed):
        stash(ledger, tree, ["out/x.bin"])
    assert (tmp_path / "outside" / "x.bin").exists()


def test_restore_never_writes_outside_its_tree(tmp_path):
    import json
    root, ledger = world(tmp_path)
    tree = seat(root, tmp_path)
    put_file(tree, "a.bin", b"payload")
    entry = stash(ledger, tree, ["a.bin"])
    path = archive.root_of(ledger) / "entries" / entry.id / "entry.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["files"][0]["from"] = str(tmp_path / "outside" / "evil.bin")
    path.write_text(json.dumps(data), encoding="utf-8")
    lines = archive.restore(ledger, entry.id)
    assert not (tmp_path / "outside" / "evil.bin").exists()
    assert any("refused" in line for line in lines), lines


def test_restore_does_not_follow_a_linked_directory(tmp_path):
    root, ledger = world(tmp_path)
    tree = seat(root, tmp_path)
    put_file(tree, "out/x.bin", b"payload")
    entry = stash(ledger, tree, ["out/x.bin"])
    (tree / "out").rmdir()
    (tmp_path / "outside").mkdir()
    (tree / "out").symlink_to(tmp_path / "outside", target_is_directory=True)
    lines = archive.restore(ledger, entry.id)
    assert not (tmp_path / "outside" / "x.bin").exists()
    assert any("refused" in line for line in lines), lines


def test_restore_refuses_a_tree_that_is_not_one_of_the_repositorys(tmp_path):
    import json
    root, ledger = world(tmp_path)
    tree = seat(root, tmp_path)
    put_file(tree, "a.bin", b"payload")
    entry = stash(ledger, tree, ["a.bin"])
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    path = archive.root_of(ledger) / "entries" / entry.id / "entry.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["trees"][0]["tree"] = str(elsewhere)
    data["files"][0].update(tree=str(elsewhere), **{"from": str(elsewhere / "a.bin")})
    path.write_text(json.dumps(data), encoding="utf-8")
    lines = archive.restore(ledger, entry.id)
    assert not (elsewhere / "a.bin").exists() and any("refused" in line for line in lines), lines


# Security review of 554be5c: an entry's record is a file any session can edit - digest, head and tree are checked.

def tampered(ledger, entry, change):
    import json
    path = archive.root_of(ledger) / "entries" / entry.id / "entry.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    change(data)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_restore_refuses_a_digest_that_is_not_one(tmp_path):
    root, ledger = world(tmp_path)
    tree = seat(root, tmp_path)
    put_file(tree, "a.bin", b"payload")
    entry = stash(ledger, tree, ["a.bin"])
    (tmp_path / "secret").write_bytes(b"secret")
    tampered(ledger, entry, lambda data: data["files"][0].update(sha256="../../../secret"))
    lines = archive.restore(ledger, entry.id)
    assert not (tree / "a.bin").exists() and any("refused" in line for line in lines), lines


def test_restore_refuses_an_object_that_does_not_hash_to_its_name(tmp_path):
    root, ledger = world(tmp_path)
    tree = seat(root, tmp_path)
    put_file(tree, "a.bin", b"payload")
    entry = stash(ledger, tree, ["a.bin"])
    digest = entry.files[0]["sha256"]
    stored = archive.root_of(ledger) / "objects" / digest[:2] / digest
    stored.chmod(0o600)
    stored.write_bytes(b"swapped")
    lines = archive.restore(ledger, entry.id)
    assert not (tree / "a.bin").exists() and any("refused" in line for line in lines), lines


def test_restore_makes_a_tree_again_only_beside_the_main_checkout(tmp_path):
    root, ledger = world(tmp_path)
    tree = seat(root, tmp_path, f"{root.name}-main-1")
    put_file(tree, "a.bin", b"payload")
    entry = stash(ledger, tree, ["a.bin"])
    elsewhere = tmp_path / "deep" / "inside"
    tampered(ledger, entry, lambda data: (data["trees"][0].update(tree=str(elsewhere)),
                                          data["files"][0].update(tree=str(elsewhere),
                                                                  **{"from": str(elsewhere / "a.bin")})))
    lines = archive.restore(ledger, entry.id)
    assert not elsewhere.exists() and any("refused" in line for line in lines), lines


def test_restore_refuses_a_head_that_is_not_an_object_id(tmp_path):
    root, ledger = world(tmp_path)
    tree = seat(root, tmp_path, f"{root.name}-main-1")
    put_file(tree, "a.bin", b"payload")
    entry = stash(ledger, tree, ["a.bin"])
    git(root, "worktree", "remove", "--force", str(tree))
    tampered(ledger, entry, lambda data: data["trees"][0].update(head="--orphan=x"))
    lines = archive.restore(ledger, entry.id)
    assert not tree.exists() and any("refused" in line for line in lines), lines


# Security review of de4ae37: a forged record must not reach a git directory or the main checkout.

def test_restore_never_writes_into_a_git_directory(tmp_path):
    root, ledger = world(tmp_path)
    tree = seat(root, tmp_path)
    put_file(tree, "a.bin", b"#!/bin/sh\n")
    entry = stash(ledger, tree, ["a.bin"])
    hook = tree / ".git" / "hooks" / "post-commit"
    tampered(ledger, entry, lambda data: data["files"][0].update(**{"from": str(hook)}))
    lines = archive.restore(ledger, entry.id)
    assert not hook.exists() and any("refused" in line for line in lines), lines


def test_restore_never_writes_into_the_main_checkout(tmp_path):
    root, ledger = world(tmp_path)
    tree = seat(root, tmp_path)
    put_file(tree, "a.bin", b"#!/bin/sh\n")
    entry = stash(ledger, tree, ["a.bin"])
    target = root / "scripts" / "a.sh"
    tampered(ledger, entry, lambda data: (data["trees"][0].update(tree=str(root)),
                                          data["files"][0].update(tree=str(root), **{"from": str(target)})))
    lines = archive.restore(ledger, entry.id)
    assert not target.exists() and any("refused" in line for line in lines), lines


def test_put_refuses_a_path_through_a_git_directory(tmp_path):
    root, ledger = world(tmp_path)
    tree = seat(root, tmp_path)
    put_file(tree, "vendor/.git/config", b"[core]\n")
    with pytest.raises(archive.ArchiveFailed):
        stash(ledger, tree, ["vendor/.git/config"])


# Security review of 06edd1c: `.git` by any case or trailing dot (case-insensitive file systems), and the main checkout
# as git names it, not as the ledger's root.

@pytest.mark.parametrize("part", [".GIT", ".Git", ".git.", ".git "])
def test_restore_never_writes_into_a_git_directory_by_any_spelling(tmp_path, part):
    root, ledger = world(tmp_path)
    tree = seat(root, tmp_path)
    put_file(tree, "a.bin", b"payload")
    entry = stash(ledger, tree, ["a.bin"])
    target = tree / part / "hooks" / "post-commit"
    tampered(ledger, entry, lambda data: data["files"][0].update(**{"from": str(target)}))
    lines = archive.restore(ledger, entry.id)
    assert not target.exists() and any("refused" in line for line in lines), lines


def test_restore_refuses_the_main_checkout_when_the_ledger_stands_in_a_linked_tree(tmp_path):
    root, _ = world(tmp_path)
    tree = seat(root, tmp_path)
    ledger = make_ledger(tree, tmp_path / "state2", profile=PROFILE, live=())
    other = seat(root, tmp_path, "app-main-2", "fleet/main-2")
    put_file(other, "a.bin", b"payload")
    entry = stash(ledger, other, ["a.bin"])
    target = root / "planted.sh"
    tampered(ledger, entry, lambda data: (data["trees"][0].update(tree=str(root)),
                                          data["files"][0].update(tree=str(root), **{"from": str(target)})))
    lines = archive.restore(ledger, entry.id)
    assert not target.exists() and any("refused" in line for line in lines), lines


def test_the_archive_follows_the_main_checkout_not_the_ledgers_tree(tmp_path):
    """Final review of gc stage 2, I8: a pass run from inside a seat archived beside the seat, out of sight."""
    root, _ = world(tmp_path)
    tree = seat(root, tmp_path)
    ledger = make_ledger(tree, tmp_path / "state2", profile=PROFILE, live=())
    assert archive.root_of(ledger) == root.parent / f"{root.name}-gc-archive"


# Final review of gc stage 2, I9: restoring into a tree that exists names what it does not do.

def test_restore_refuses_a_tree_that_moved_to_another_commit(tmp_path):
    root, ledger = world(tmp_path)
    tree = seat(root, tmp_path)
    put_file(tree, "a.bin", b"payload")
    entry = stash(ledger, tree, ["a.bin"])
    commit(tree, "a new session's work", "n.txt", "n\n")
    lines = archive.restore(ledger, entry.id)
    assert not (tree / "a.bin").exists() and any("refused" in line and "commit" in line for line in lines), lines


def test_restore_into_an_existing_tree_names_the_edits_it_did_not_apply(tmp_path):
    root, ledger = world(tmp_path)
    tree = seat(root, tmp_path)
    put_file(tree, "a.bin", b"payload")
    stash(ledger, tree, ["a.bin"], patch=b"diff --git a/x b/x\n")
    lines = archive.restore(ledger, tree.name)
    assert (tree / "a.bin").exists() and any("not applied" in line and ".patch" in line for line in lines), lines
