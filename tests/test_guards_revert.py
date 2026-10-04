import pytest

from flotilla.guards import revert
from guardkit import first, git, plain_repo


def check(command, cwd):
    return revert.check(first(command, cwd))


def test_a_checkout_that_would_destroy_unadded_work_is_refused(tmp_path):
    root = plain_repo(tmp_path)
    (root / "f.txt").write_text("work\n", encoding="utf-8")
    found = check("git checkout -- f.txt", root)
    assert found.refuse and "f.txt" in found.text and "git add" in found.text


def test_after_git_add_the_same_checkout_passes(tmp_path):
    root = plain_repo(tmp_path)
    (root / "f.txt").write_text("work\n", encoding="utf-8")
    git(root, "add", "f.txt")
    assert check("git checkout -- f.txt", root) is None


def test_the_form_without_a_separator_is_read_too(tmp_path):
    root = plain_repo(tmp_path)
    (root / "f.txt").write_text("work\n", encoding="utf-8")
    assert check("git checkout f.txt", root).refuse


def test_a_named_source_is_not_told_to_git_add(tmp_path):
    root = plain_repo(tmp_path)
    (root / "f.txt").write_text("work\n", encoding="utf-8")
    git(root, "add", "f.txt")
    found = check("git checkout HEAD -- f.txt", root)
    assert found.refuse and "saves nothing" in found.text


def test_restore_reads_the_index_and_restore_staged_leaves_the_tree(tmp_path):
    root = plain_repo(tmp_path)
    (root / "f.txt").write_text("work\n", encoding="utf-8")
    assert check("git restore f.txt", root).refuse
    assert check("git restore --staged f.txt", root) is None


def test_restore_staged_and_worktree_is_not_told_to_git_add(tmp_path):
    root = plain_repo(tmp_path)
    (root / "f.txt").write_text("work\n", encoding="utf-8")
    git(root, "add", "f.txt")
    found = check("git restore -SW f.txt", root)
    assert found.refuse and "saves nothing" in found.text


def test_reset_hard_is_refused_only_over_changes(tmp_path):
    root = plain_repo(tmp_path)
    assert check("git reset --hard", root) is None
    (root / "f.txt").write_text("work\n", encoding="utf-8")
    assert check("git reset --hard HEAD", root).refuse


def test_clean_names_what_it_would_remove(tmp_path):
    root = plain_repo(tmp_path)
    (root / "notes.md").write_text("mine\n", encoding="utf-8")
    found = check("git clean -fd", root)
    assert found.refuse and "notes.md" in found.text
    assert check("git clean -n", root) is None


def test_switching_branches_is_left_to_git(tmp_path):
    root = plain_repo(tmp_path)
    git(root, "branch", "feature")
    (root / "f.txt").write_text("work\n", encoding="utf-8")
    assert check("git checkout feature", root) is None


def test_a_tree_named_by_c_is_the_one_checked(tmp_path):
    root = plain_repo(tmp_path)
    (root / "f.txt").write_text("work\n", encoding="utf-8")
    assert check(f"git -C {root} checkout -- f.txt", tmp_path).refuse


def test_an_unknown_tree_warns_and_lets_it_run(tmp_path):
    found = check("cd $D && git checkout -- f.txt", tmp_path)
    assert found is not None and not found.refuse and "could not tell which tree" in found.text


def test_outside_a_repository_there_is_nothing_to_say(tmp_path):
    assert check("git checkout -- f.txt", tmp_path) is None


def test_a_quiet_clean_is_still_judged(tmp_path):
    root = plain_repo(tmp_path)
    (root / "notes.md").write_text("mine\n", encoding="utf-8")
    assert check("git clean -fdq", root).refuse
    assert check("git clean -f -q", root).refuse


def test_the_guard_never_deletes_what_it_was_asked_to_judge(tmp_path):
    """git takes `--forc` for `--force` and `--no-dry-run` undoes `-n`: the guard ran `git clean -n` with the
    command's own arguments, so this command deleted the files before anyone allowed it (security review F11)."""
    root = plain_repo(tmp_path)
    (root / "new.txt").write_text("unsaved\n", encoding="utf-8")
    (root / "notes").mkdir()
    (root / "notes" / "draft.md").write_text("draft\n", encoding="utf-8")
    found = check("git clean -f --forc --no-dry-run -dx", root)
    assert (root / "new.txt").exists() and (root / "notes" / "draft.md").exists()
    assert found.refuse and "new.txt" in found.text


def test_an_abbreviated_force_is_a_force(tmp_path):
    root = plain_repo(tmp_path)
    (root / "new.txt").write_text("unsaved\n", encoding="utf-8")
    found = check("git clean --forc -d", root)
    assert found is not None and found.refuse and "new.txt" in found.text


def test_the_guard_does_not_run_the_judged_repositorys_fsmonitor(tmp_path):
    """The guard asks git about the tree the command names, before anyone allowed the command; that tree's own
    config must not get a program run by the asking (security review F19)."""
    root = plain_repo(tmp_path)
    marker = tmp_path / "fsmonitor-ran"
    git(root, "config", "core.fsmonitor", f"touch {marker} #")
    (root / "README.md").write_text("changed\n", encoding="utf-8")
    check("git checkout -- README.md", root)
    check("git reset --hard", root)
    assert not marker.exists()


def test_a_dry_run_undone_later_in_the_command_is_a_real_clean(tmp_path):
    root = plain_repo(tmp_path)
    (root / "new.txt").write_text("unsaved\n", encoding="utf-8")
    found = check("git clean -n -f --no-dry-run", root)
    assert found is not None and found.refuse and "new.txt" in found.text
    assert (root / "new.txt").exists()


@pytest.mark.parametrize("command", ["git clean --f -d", "git clean -fd -en", "git clean -f -e -n",
                                     "git clean -f -e --dry-run", "git clean -d", "git clean -f --e keep"])
def test_every_form_git_runs_as_a_clean_is_read_as_one(tmp_path, command):
    """git takes any unambiguous prefix (`--f`), takes the word after `-e` as its value even when it looks like a
    flag, and with clean.requireForce=false cleans without -f: each form below removes new.txt, so each is judged
    (review of the F11 fix)."""
    root = plain_repo(tmp_path)
    (root / "new.txt").write_text("unsaved\n", encoding="utf-8")
    found = check(command, root)
    assert found is not None and found.refuse and "new.txt" in found.text, command
    assert (root / "new.txt").exists()


@pytest.mark.parametrize("option", ["--work-tree=elsewhere", "--git-dir=elsewhere/.git"])
def test_a_tree_named_with_an_equals_sign_is_not_mistaken_for_the_shells(tmp_path, option):
    root = plain_repo(tmp_path)
    (root / "new.txt").write_text("unsaved\n", encoding="utf-8")
    found = check(f"git {option} clean -fd", root)
    assert found is not None and "could not tell which tree" in found.text


@pytest.mark.parametrize("command", ["git switch -f main", "git switch --force main",
                                     "git switch --discard-changes main", "git switch --disc main",
                                     "git switch -fc topic"])
def test_a_switch_that_discards_changes_is_refused_like_checkout_f(tmp_path, command):
    root = plain_repo(tmp_path)
    (root / "f.txt").write_text("changed\n", encoding="utf-8")
    found = check(command, root)
    assert found is not None and found.refuse and "f.txt" in found.text


def test_a_switch_that_keeps_changes_or_has_none_to_lose_passes(tmp_path):
    root = plain_repo(tmp_path)
    assert check("git switch -f main", root) is None   # nothing uncommitted
    (root / "f.txt").write_text("changed\n", encoding="utf-8")
    assert check("git switch main", root) is None       # git itself refuses to overwrite, or carries the change
    assert check("git switch -c topic", root) is None


def test_every_revert_verb_reaches_the_guard_through_the_hooks_fast_path():
    from flotilla import hooks
    assert all(any(word in verb for word in hooks.GUARD_TRIGGERS) for verb in revert.VERBS)
