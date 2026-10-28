"""Twins by content (gc design, section 6 rule 2): a byte-identical file in a place that survives the pass - the main
checkout, a declared store, the archive, or a blob a surviving ref reaches - and never a link's target."""

import hashlib
import os

from flotilla.gc import refs, twins
from ledgerkit import PROFILE, commit, git, make_ledger, repo_with_origin


def world(tmp_path, profile=None):
    root = repo_with_origin(tmp_path)
    return root, make_ledger(root, tmp_path / "state", profile=profile or PROFILE, live=())


def put(path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def finder(ledger, cache=None, archive_root=None, untouched=()):
    sources = twins.sources(ledger, untouched=list(untouched),
                            archive_root=archive_root or ledger.root.parent / "none-gc-archive")
    return twins.Twins(ledger, holders=refs.holders(ledger), sources=sources, cache={} if cache is None else cache)


def test_a_byte_identical_file_in_the_main_checkout_is_a_twin(tmp_path):
    root, ledger = world(tmp_path)
    put(root / "out" / "a.bin", b"payload")
    mine = put(tmp_path / "app-main-1" / "out" / "a.bin", b"payload")
    found = finder(ledger).find(mine)
    assert found == twins.Twin(str(root / "out" / "a.bin"), "checkout")


def test_same_size_other_bytes_is_no_twin(tmp_path):
    root, ledger = world(tmp_path)
    put(root / "out" / "a.bin", b"aaaa")
    mine = put(tmp_path / "app-main-1" / "out" / "a.bin", b"aaab")
    assert finder(ledger).find(mine) is None


def test_a_declared_store_is_a_source(tmp_path):
    root, ledger = world(tmp_path, {**PROFILE, "gc": {"stores": ["feedback"]}})
    put(root.parent / "feedback" / "x" / "y.mp4", b"render")
    mine = put(tmp_path / "app-main-1" / "renders" / "y.mp4", b"render")
    found = finder(ledger).find(mine)
    assert found is not None and found.kind == "store"
    assert found.where == str(root.parent / "feedback" / "x" / "y.mp4")


def test_a_reachable_blob_is_a_twin_and_an_unreachable_one_is_not(tmp_path):
    root, ledger = world(tmp_path)
    git(root, "checkout", "-q", "-b", "side")
    commit(root, "keep it", "kept.txt", "committed bytes\n")
    git(root, "checkout", "-q", "main")
    mine = put(tmp_path / "app-main-1" / "kept.txt", b"committed bytes\n")
    found = finder(ledger).find(mine)
    assert found is not None and found.kind == "blob"
    git(root, "branch", "-D", "side")   # the blob stays in the object store, but nothing reaches it
    assert finder(ledger).find(mine) is None


def test_the_cache_is_reused_until_size_or_mtime_moves(tmp_path, monkeypatch):
    mine = put(tmp_path / "a.bin", b"payload")
    calls = []
    real = hashlib.sha256

    def counting(*args, **kwargs):
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(twins.hashlib, "sha256", counting)
    cache: dict = {}
    first = twins.sha256(mine, cache)
    assert twins.sha256(mine, cache) == first and len(calls) == 1
    stat = mine.stat()
    os.utime(mine, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))
    assert twins.sha256(mine, cache) == first and len(calls) == 2


def test_the_cache_survives_a_save_and_a_load(tmp_path):
    cache = {}
    twins.sha256(put(tmp_path / "a.bin", b"x"), cache)
    twins.save_cache(tmp_path / "state", "key", cache)
    assert twins.load_cache(tmp_path / "state", "key") == cache
    assert twins.load_cache(tmp_path / "state", "other") == {}


def test_a_symlink_is_never_followed(tmp_path):
    root, ledger = world(tmp_path)
    outside = put(tmp_path / "outside" / "secret.bin", b"secret")
    link = tmp_path / "app-main-1" / "link.bin"
    link.parent.mkdir(parents=True)
    link.symlink_to(outside)
    expected = hashlib.sha256(f"link:{outside}".encode()).hexdigest()
    assert twins.sha256(link, {}) == expected
    (root / "store-link").symlink_to(tmp_path / "outside", target_is_directory=True)
    mine = put(tmp_path / "app-main-1" / "s.bin", b"secret")
    assert finder(ledger).find(mine) is None   # the checkout's linked directory is not walked


def test_a_tree_inside_the_checkout_is_never_a_source(tmp_path):
    """Security review of dea3c4e: two judged trees nested in the main checkout read as twins of each other."""
    root, ledger = world(tmp_path)
    inner = root / ".worktrees" / "app-main-2"
    put(inner / ".git", b"gitdir: /elsewhere\n")
    put(inner / "out" / "a.bin", b"only in judged trees")
    mine = put(tmp_path / "app-main-1" / "out" / "a.bin", b"only in judged trees")
    assert finder(ledger).find(mine) is None


def test_a_rewrite_keeping_size_and_mtime_is_hashed_again(tmp_path):
    """Security review of dea3c4e: the cache trusted (size, mtime), which a rewrite can keep."""
    mine = put(tmp_path / "a.bin", b"aaaa")
    cache: dict = {}
    first = twins.sha256(mine, cache)
    stat = mine.stat()
    mine.write_bytes(b"bbbb")
    os.utime(mine, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert twins.sha256(mine, cache) != first


def test_verify_hashes_the_twin_without_the_cache(tmp_path):
    root, ledger = world(tmp_path)
    theirs = put(root / "out" / "a.bin", b"payload")
    mine = put(tmp_path / "app-main-1" / "out" / "a.bin", b"payload")
    cache: dict = {}
    assert finder(ledger, cache).find(mine) is not None
    theirs.write_bytes(b"PAYLOAD")   # changed since the cache was written ...
    stat = theirs.stat()
    cache[str(theirs)] = [stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino,
                          hashlib.sha256(b"payload").hexdigest()]   # ... and the cache entry lies about it
    trusting = twins.Twins(ledger, holders=refs.holders(ledger), cache=cache,
                           sources=twins.sources(ledger, untouched=[], archive_root=tmp_path / "none"))
    assert trusting.find(mine) is not None
    checking = twins.Twins(ledger, holders=refs.holders(ledger), cache=cache, verify=True,
                           sources=twins.sources(ledger, untouched=[], archive_root=tmp_path / "none"))
    assert checking.find(mine) is None
