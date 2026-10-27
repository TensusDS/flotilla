from flotilla.ledger import gitq
from ledgerkit import branch, commit, git, repo_with_origin


def test_resolve_full_short_and_unknown(tmp_path):
    root = repo_with_origin(tmp_path)
    full = git(root, "rev-parse", "HEAD")
    assert gitq.resolve(root, full[:7]) == full and gitq.resolve(root, "HEAD") == full
    assert gitq.resolve(root, "0000000") is None and gitq.resolve(root, "") is None


def test_same_revision_three_outcomes(tmp_path):
    root = repo_with_origin(tmp_path)
    first = git(root, "rev-parse", "HEAD")
    second = commit(root, "second")
    assert gitq.same_revision(root, first[:8], first) is True
    assert gitq.same_revision(root, first, second) is False
    assert gitq.same_revision(root, "deadbeef", first) is None


def test_branch_tip(tmp_path):
    root = repo_with_origin(tmp_path)
    tip = branch(root, "feat/x", "one", "two")
    assert gitq.branch_tip(root, "feat/x") == tip and gitq.branch_tip(root, "feat/none") is None


def test_fork_point_is_asked_of_origin_not_the_local_trunk(tmp_path):
    root = repo_with_origin(tmp_path)
    other = tmp_path / "other"
    git(tmp_path, "clone", "-q", str(tmp_path / "origin.git"), str(other))
    ahead = commit(other, "upstream moves", "up.txt", "u\n")
    git(other, "push", "-q", "origin", "main")
    git(root, "fetch", "-q", "origin")
    git(root, "checkout", "-q", "-b", "feat/x", "origin/main")
    commit(root, "work", "w.txt")
    git(root, "checkout", "-q", "main")
    assert gitq.fork_point(root, "feat/x", "main") == ahead


def test_fork_point_without_origin_uses_the_local_trunk(tmp_path):
    root = tmp_path / "lone"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    base = commit(root, "init")
    branch(root, "feat/x", "one")
    assert gitq.fork_point(root, "feat/x", "main") == base


def test_is_ancestor_three_outcomes(tmp_path):
    root = repo_with_origin(tmp_path)
    first = git(root, "rev-parse", "HEAD")
    second = commit(root, "b")
    assert gitq.is_ancestor(root, first, second) is True and gitq.is_ancestor(root, second, first) is False
    assert gitq.is_ancestor(root, "deadbeef", first) is None


def test_patch_fingerprint_survives_a_rebase(tmp_path):
    root = repo_with_origin(tmp_path)
    base = git(root, "rev-parse", "HEAD")
    tip = branch(root, "feat/x", "work")
    before = gitq.patch_fingerprint(root, base, tip)
    new_base = commit(root, "unrelated", "other.txt", "x\n")
    git(root, "checkout", "-q", "feat/x")
    git(root, "-c", "user.email=t@example.invalid", "-c", "user.name=t", "rebase", "-q", "main")
    git(root, "checkout", "-q", "main")
    after = gitq.patch_fingerprint(root, new_base, gitq.branch_tip(root, "feat/x"))
    assert before == after and before not in (None, "empty")


def test_patch_fingerprint_changes_with_content(tmp_path):
    root = repo_with_origin(tmp_path)
    base = git(root, "rev-parse", "HEAD")
    one = branch(root, "feat/a", "a")
    two = branch(root, "feat/b", "b")
    assert gitq.patch_fingerprint(root, base, one) != gitq.patch_fingerprint(root, base, two)


def test_is_clean(tmp_path):
    root = repo_with_origin(tmp_path)
    assert gitq.is_clean(root) is True
    (root / "dirty.txt").write_text("x", encoding="utf-8")
    assert gitq.is_clean(root) is False


def test_a_submodule_pointer_move_is_a_change_whatever_gitmodules_says(tmp_path):
    """Review of the sender's push allow, I2: with `ignore = all` in .gitmodules, a commit that only moves a
    submodule pointer had an empty diff and was accounted as carrying no change."""
    import subprocess
    from flotilla.ledger import gitq

    def g(*args):
        return subprocess.run(["git", "-C", str(tmp_path), "-c", "user.name=t", "-c", "user.email=t@x", *args],
                              capture_output=True, text=True, check=True).stdout.strip()
    g("init", "-q", "-b", "main")
    g("commit", "-q", "--allow-empty", "-m", "a")
    first = g("rev-parse", "HEAD")
    g("commit", "-q", "--allow-empty", "-m", "b")
    second = g("rev-parse", "HEAD")
    (tmp_path / ".gitmodules").write_text('[submodule "sub"]\n\tpath = sub\n\turl = ./x\n\tignore = all\n',
                                          encoding="utf-8")
    g("add", ".gitmodules")
    g("update-index", "--add", "--cacheinfo", f"160000,{first},sub")
    g("commit", "-q", "-m", "submodule")
    base = g("rev-parse", "HEAD")
    g("update-index", "--cacheinfo", f"160000,{second},sub")
    g("commit", "-q", "-m", "move the pointer")
    assert gitq.patch_fingerprint(tmp_path, base, g("rev-parse", "HEAD")) not in (None, "empty")


def _commit_bytes(root, name, data):
    """A file git diffs as text (no NUL in its first 8000 bytes) whose bytes are not UTF-8: a float16 table (twosuns
    field report A1, 2026-10-26)."""
    (root / name).write_bytes(data)
    git(root, "add", name)
    git(root, "-c", "user.email=t@example.invalid", "-c", "user.name=t", "commit", "-q", "-m", f"add {name}")
    return git(root, "rev-parse", "HEAD")


NOT_UTF8 = bytes(range(0x80, 0x100)) * 64 + b"\n"   # every byte invalid as a UTF-8 start, and no NUL


def test_patch_fingerprint_reads_a_diff_that_is_not_utf8(tmp_path):
    root = repo_with_origin(tmp_path)
    base = git(root, "rev-parse", "HEAD")
    tip = _commit_bytes(root, "table.f16", NOT_UTF8)
    first = gitq.patch_fingerprint(root, base, tip)
    assert first not in (None, "empty")
    other = _commit_bytes(root, "table.f16", NOT_UTF8 + b"\xa8\n")
    assert gitq.patch_fingerprint(root, tip, other) not in (None, "empty", first)


def test_patch_ids_of_a_log_that_is_not_utf8(tmp_path):
    from types import SimpleNamespace
    import subprocess
    from flotilla.ledger import batch
    root = repo_with_origin(tmp_path)
    base = git(root, "rev-parse", "HEAD")
    tip = _commit_bytes(root, "table.f16", NOT_UTF8)
    ids = batch._patch_ids(SimpleNamespace(root=root, run=subprocess.run), f"{base}..{tip}")
    assert len(ids) == 1 and ids[0] == gitq.patch_fingerprint(root, base, tip)
