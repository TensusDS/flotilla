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
