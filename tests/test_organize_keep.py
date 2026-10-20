import subprocess

import pytest

from flotilla.organize import keep
from ledgerkit import commit, git, repo_with_origin


def test_a_kept_ref_is_never_overwritten(tmp_path):
    root = repo_with_origin(tmp_path)
    first = git(root, "rev-parse", "HEAD")
    ref = keep.create_ref(root, "kept", first)
    assert ref == f"refs/flotilla/kept/{first}"
    assert git(root, "rev-parse", ref) == first
    with pytest.raises(RuntimeError):
        keep.create_ref(root, "kept", first)
    later = commit(root, "later", "later.txt")
    assert keep.create_ref(root, "dropped", later) == f"refs/flotilla/dropped/{later}"


def _tree(tmp_path):
    root = repo_with_origin(tmp_path)
    git(root, "config", "user.email", "me@example.invalid")
    git(root, "config", "user.name", "me")
    (root / "tracked.txt").write_text("one\n", encoding="utf-8")
    git(root, "add", "tracked.txt")
    git(root, "commit", "-q", "-m", "tracked")
    return root


def test_keep_holds_untracked_binary_and_staged_states(tmp_path):
    """Review Focus 2: `git stash create` keeps no untracked file; the kept commit holds the working tree whole,
    and the index as its second parent."""
    root = _tree(tmp_path)
    (root / "tracked.txt").write_text("staged\n", encoding="utf-8")
    git(root, "add", "tracked.txt")
    (root / "tracked.txt").write_text("staged then changed\n", encoding="utf-8")
    (root / "image.bin").write_bytes(bytes(range(256)))
    (root / "ignored.log").write_text("noise\n", encoding="utf-8")
    (root / ".gitignore").write_text("*.log\n", encoding="utf-8")
    before = git(root, "status", "--porcelain")
    kept = keep.keep_all(root)
    assert git(root, "show", f"{kept}:tracked.txt") == "staged then changed"
    assert git(root, "show", f"{kept}^2:tracked.txt") == "staged"
    blob = subprocess.run(["git", "-C", str(root), "show", f"{kept}:image.bin"], capture_output=True).stdout
    assert blob == bytes(range(256))
    assert "ignored.log" not in git(root, "ls-tree", "--name-only", kept)
    assert git(root, "status", "--porcelain") == before   # keeping changed nothing in the tree or the index


def test_a_path_whose_stored_blob_differs_stays(tmp_path):
    root = _tree(tmp_path)
    (root / "a.txt").write_text("a\n", encoding="utf-8")
    kept = keep.keep_all(root)
    assert keep.differs(root, kept, ["a.txt", "tracked.txt"]) == []
    (root / "a.txt").write_text("changed after\n", encoding="utf-8")
    assert keep.differs(root, kept, ["a.txt"]) == ["a.txt"]


def test_line_ending_rules_are_applied_before_comparing(tmp_path):
    root = _tree(tmp_path)
    (root / ".gitattributes").write_text("*.txt text eol=lf\n", encoding="utf-8")
    (root / "crlf.txt").write_bytes(b"one\r\ntwo\r\n")
    kept = keep.keep_all(root)
    assert keep.differs(root, kept, ["crlf.txt"]) == []
