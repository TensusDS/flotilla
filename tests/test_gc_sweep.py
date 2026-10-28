"""One pass of the collector (gc design, sections 5 and 9): branches judged first, then trees against the refs that
survive; one report by reason, never one line per kept tree."""

from flotilla.gc import sweep
from ledgerkit import IDENTITY, PROFILE, actor, commit, git, make_ledger, repo_with_origin


def world(tmp_path, profile=None):
    root = repo_with_origin(tmp_path)
    return root, make_ledger(root, tmp_path / "state", profile=profile or PROFILE, live=())


def refs_and_trees(root):
    return git(root, "for-each-ref", "--format=%(refname) %(objectname)"), git(root, "worktree", "list")


def test_a_fleet_like_the_field_one_is_mostly_cleaned(tmp_path):
    root, ledger = world(tmp_path, {**PROFILE, "gc": {"trunks": ["proto/x"]}})
    commit(root, "ignore", ".gitignore", "tools/crystal3d/\n")
    git(root, "push", "-q", "origin", "HEAD:main")
    git(root, "branch", "proto/x")

    def seat(name, *args):
        tree = tmp_path / f"app-{name}"
        git(root, "worktree", "add", "-q", *args, str(tree), *([] if "-b" in args else ["main"]))
        return tree
    dead = seat("main-1", "-b", "fleet/main-1")                    # a dead seat on an unlanded branch
    unlanded = commit(dead, "unlanded work", "w.txt", "x\n")
    reviewer = seat("reviewer-1", "--detach")                      # a reviewer's checkout of a commit
    git(reviewer, "checkout", "-q", "--detach", unlanded)
    trial = seat("main-2", "-b", "fleet/main-2")                   # reflog-only commits
    dropped = commit(trial, "a trial merge")
    git(trial, "reset", "-q", "--hard", "HEAD~1")
    cache = seat("main-3", "-b", "fleet/main-3")                   # the folded `__pycache__` of the field bug
    (cache / "tools" / "crystal3d" / "__pycache__").mkdir(parents=True)
    (cache / "tools" / "crystal3d" / "__pycache__" / "a.pyc").write_bytes(b"x")
    dirty = seat("main-4", "-b", "fleet/main-4")                   # untracked work
    (dirty / "notes.txt").write_text("mine\n", encoding="utf-8")
    sole = seat("main-6", "--detach")                              # the only holder of its HEAD
    commit(sole, "only here")
    git(root, "checkout", "-q", "-b", "feat/s")                    # the sender's batch branch, shipped
    commit(root, "shipped work")
    git(root, "checkout", "-q", "main")
    git(root, *IDENTITY, "merge", "-q", "--no-ff", "--no-edit", "feat/s")
    git(root, "push", "-q", "origin", "HEAD:main")
    merge = git(root, "rev-parse", "HEAD")
    git(root, "branch", "batch/1", merge)
    with ledger.session() as s:
        s.append(actor(ledger, "sender 1"), "r5", "ship", "shipped", fields={"branch": "feat/s", "merge": merge})
    git(root, "checkout", "-q", "-b", "fleet/line-1", "proto/x")   # a branch landed on the declared trunk
    commit(root, "on the line")
    git(root, "checkout", "-q", "proto/x")
    git(root, *IDENTITY, "merge", "-q", "--no-ff", "--no-edit", "fleet/line-1")
    git(root, "checkout", "-q", "main")

    report = sweep.run_pass(ledger, sessions=[], act=True)

    assert not dead.exists() and not reviewer.exists() and not trial.exists() and not cache.exists()
    assert dirty.exists() and sole.exists()
    listed = git(root, "branch", "--list", "--format=%(refname:short)").split()
    assert "batch/1" not in listed and "fleet/line-1" not in listed and "fleet/main-3" not in listed
    assert "fleet/main-1" in listed and "fleet/main-4" in listed and "proto/x" in listed
    assert dropped in git(root, "for-each-ref", "--format=%(objectname)", "refs/flotilla/parked/").split()
    lines = sweep.lines(report)
    assert lines[0].startswith("removed 4 trees")
    kept = [line for line in lines if line.startswith("kept ") and " tree" in line.split(":")[0]]
    assert len(kept) == 1 and kept[0].startswith("kept 2 trees: waits on the person"), lines   # gc design 9
    assert any("only on this disk" in line for line in lines)


def test_a_missing_tree_is_named_not_pruned(tmp_path):
    root, ledger = world(tmp_path)
    tree = tmp_path / "app-main-1"
    git(root, "worktree", "add", "-q", "-b", "fleet/main-1", str(tree))
    import shutil
    shutil.rmtree(tree)
    report = sweep.run_pass(ledger, sessions=[], act=True)
    assert str(tree) in report.missing
    assert str(tree) in git(root, "worktree", "list")
    assert any("not pruned" in line for line in sweep.lines(report))


def test_without_yes_nothing_changes(tmp_path):
    root, ledger = world(tmp_path)
    tree = tmp_path / "app-main-1"
    git(root, "worktree", "add", "-q", "-b", "fleet/main-1", str(tree))
    commit(tree, "a trial")
    git(tree, "reset", "-q", "--hard", "HEAD~1")
    before = refs_and_trees(root)
    report = sweep.run_pass(ledger, sessions=[], act=False)
    assert refs_and_trees(root) == before
    assert sweep.lines(report)[0].startswith("would remove 1 tree")


def test_many_kept_trees_of_one_reason_are_one_line(tmp_path):
    root, ledger = world(tmp_path)
    for index in range(1, 9):
        tree = tmp_path / f"app-main-{index}"
        git(root, "worktree", "add", "-q", "-b", f"fleet/main-{index}", str(tree))
        (tree / "notes.txt").write_text("mine\n", encoding="utf-8")
    lines = sweep.lines(sweep.run_pass(ledger, sessions=[], act=True))
    kept = [line for line in lines if line.startswith("kept 8 trees")]
    assert len(kept) == 1 and "and 3 more" in kept[0], lines



def test_a_branch_a_staying_tree_is_rebasing_is_kept(tmp_path):
    """Final review of gc stage 1, I4: during a rebase `git worktree list` shows the tree detached, so its landed
    branch read as nobody's and was deleted under the rebase."""
    import shlex
    import subprocess
    import sys
    # The todo list gets a leading `break`; BSD sed's -i takes a suffix, so the editor is this Python, not sed.
    editor = shlex.join([sys.executable, "-c",
                         "import sys; p = sys.argv[1]; s = open(p).read(); open(p, 'w').write('break\\n' + s)"])
    root, ledger = world(tmp_path)
    tree = tmp_path / "app-main-1"
    git(root, "worktree", "add", "-q", "-b", "fleet/main-1", str(tree))
    commit(tree, "landed work", "a.txt", "1\n")
    git(root, "merge", "-q", "--ff-only", "fleet/main-1")
    git(root, "push", "-q", "origin", "HEAD:main")
    subprocess.run(["git", "-C", str(tree), *IDENTITY, "-c", f"sequence.editor={editor}", "rebase", "-i",
                    "HEAD~1"], capture_output=True, text=True, check=True)
    report = sweep.run_pass(ledger, sessions=[], act=True, proc_root=tmp_path / "no-proc")
    assert tree.exists()
    assert "fleet/main-1" in git(root, "branch", "--list", "--format=%(refname:short)").split()
    assert "fleet/main-1" not in report.deleted


def test_trees_waiting_on_the_person_share_one_report_line(tmp_path):
    """gc design 9 (stage 2): doubts are put to the person, so the report names them once, not once per tree."""
    root, ledger = world(tmp_path)
    for number in (1, 2, 3):
        tree = tmp_path / f"app-main-{number}"
        git(root, "worktree", "add", "-q", "-b", f"fleet/main-{number}", str(tree))
        (tree / "new.txt").write_text("n\n", encoding="utf-8")
    report = sweep.run_pass(ledger, sessions=[], act=False, proc_root=tmp_path / "no-proc")
    kept = [line for line in sweep.lines(report) if line.startswith("kept 3 trees: ")]
    assert kept and kept[0].startswith("kept 3 trees: waits on the person (flotilla gc next) - ")


# gc design 2 and 7 (stage 2): small unique files go to the archive in the pass, and the person is told after.

def ignored_seat(root, tmp_path, number, data=b"12345", path="out/x.bin"):
    tree = tmp_path / f"app-main-{number}"
    git(root, "worktree", "add", "-q", "-b", f"fleet/main-{number}", str(tree))
    target = tree / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return tree


def ignoring(root):
    commit(root, "ignore", ".gitignore", "out/\n")
    git(root, "push", "-q", "origin", "HEAD:main")


def test_a_small_unique_file_is_archived_and_the_tree_removed(tmp_path):
    from flotilla.gc import archive
    root, ledger = world(tmp_path)
    ignoring(root)
    tree = ignored_seat(root, tmp_path, 1)
    report = sweep.run_pass(ledger, sessions=[], act=True, proc_root=tmp_path / "no-proc")
    assert not tree.exists() and "app-main-1" in report.removed
    assert [path.name for path in (archive.root_of(ledger) / "objects").rglob("*") if path.is_file()]
    shown = sweep.lines(report)
    assert any(line.startswith("archived 1 file (5 B) from app-main-1: restore with flotilla gc restore ")
               for line in shown), shown


def test_the_preview_says_what_it_would_archive(tmp_path):
    root, ledger = world(tmp_path)
    ignoring(root)
    tree = ignored_seat(root, tmp_path, 1)
    shown = sweep.lines(sweep.run_pass(ledger, sessions=[], act=False, proc_root=tmp_path / "no-proc"))
    assert tree.exists() and any(line.startswith("would archive 1 file (5 B) from app-main-1") for line in shown)


def test_over_the_tree_threshold_waits_on_the_person(tmp_path):
    root, ledger = world(tmp_path, {**PROFILE, "gc": {"archive_tree_mb": 0}})
    ignoring(root)
    tree = ignored_seat(root, tmp_path, 1)
    report = sweep.run_pass(ledger, sessions=[], act=True, proc_root=tmp_path / "no-proc")
    assert tree.exists() and not report.archived
    assert any(line.startswith("kept 1 tree: waits on the person") for line in sweep.lines(report))


def test_the_pass_threshold_counts_identical_content_once(tmp_path):
    from flotilla.gc import archive
    root, ledger = world(tmp_path, {**PROFILE, "gc": {"archive_pass_mb": 1}})
    ignoring(root)
    data = b"r" * 600 * 1024
    first, second = ignored_seat(root, tmp_path, 1, data), ignored_seat(root, tmp_path, 2, data)
    sweep.run_pass(ledger, sessions=[], act=True, proc_root=tmp_path / "no-proc")
    assert not first.exists() and not second.exists()
    assert len([path for path in (archive.root_of(ledger) / "objects").rglob("*") if path.is_file()]) == 1


def test_a_twin_in_a_store_names_the_last_copy(tmp_path):
    root, ledger = world(tmp_path, {**PROFILE, "gc": {"stores": ["feedback"]}})
    ignoring(root)
    (root.parent / "feedback").mkdir()
    (root.parent / "feedback" / "x.bin").write_bytes(b"12345")
    tree = ignored_seat(root, tmp_path, 1)
    shown = sweep.lines(sweep.run_pass(ledger, sessions=[], act=True, proc_root=tmp_path / "no-proc"))
    assert not tree.exists()
    assert any(line.startswith("the last copy of out/x.bin is now in ") and "feedback" in line for line in shown)


def test_freed_and_moved_are_reported_apart(tmp_path):
    root, ledger = world(tmp_path)
    ignoring(root)
    ignored_seat(root, tmp_path, 1)
    shown = sweep.lines(sweep.run_pass(ledger, sessions=[], act=True, proc_root=tmp_path / "no-proc"))
    line = next(line for line in shown if line.startswith("freed "))
    assert "moved 5 B to the archive" in line and "frees nothing until the archive is emptied" in line


def test_report_lines_show_control_characters_visibly(tmp_path):
    """Security review of 52a9a2f: a file name reaches the person's terminal; its control characters must not."""
    root, ledger = world(tmp_path, {**PROFILE, "gc": {"stores": ["feedback"]}})
    ignoring(root)
    (root.parent / "feedback").mkdir()
    (root.parent / "feedback" / "x.bin").write_bytes(b"12345")
    ignored_seat(root, tmp_path, 1, path="out/a\x1b[31m.bin")
    shown = sweep.lines(sweep.run_pass(ledger, sessions=[], act=True, proc_root=tmp_path / "no-proc"))
    assert any(line.startswith("the last copy of out/a") for line in shown), shown
    assert not any("\x1b" in line for line in shown), shown


# Final review of gc stage 2.

def test_an_unreadable_ignored_file_waits_on_the_person_and_the_pass_goes_on(tmp_path):
    """I4: one chmod 000 file crashed the whole pass, and with it fleet down."""
    root, ledger = world(tmp_path)
    ignoring(root)
    tree = ignored_seat(root, tmp_path, 1)
    (tree / "out" / "x.bin").chmod(0)
    try:
        report = sweep.run_pass(ledger, sessions=[], act=True, proc_root=tmp_path / "no-proc")
    finally:
        (tree / "out" / "x.bin").chmod(0o600)
    assert tree.exists() and any(line.startswith("kept 1 tree: waits on the person") for line in sweep.lines(report))


def test_a_named_pipe_among_ignored_files_does_not_hang_the_pass(tmp_path):
    """I4: open() on a FIFO blocked forever."""
    import os
    import signal

    def hung(*_args):
        raise TimeoutError("the pass hung on a named pipe")

    root, ledger = world(tmp_path)
    ignoring(root)
    tree = ignored_seat(root, tmp_path, 1)
    os.mkfifo(tree / "out" / "pipe")
    os.mkfifo(root / "pipe-in-checkout")
    previous = signal.signal(signal.SIGALRM, hung)
    signal.alarm(20)
    try:
        report = sweep.run_pass(ledger, sessions=[], act=True, proc_root=tmp_path / "no-proc")
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)
    assert tree.exists() and any("waits on the person" in line for line in sweep.lines(report))


def test_a_declared_store_inside_a_judged_tree_is_no_source(tmp_path):
    """I1: a store inside the tree made its own two copies twins of each other, and both went."""
    from flotilla.gc import archive
    root, ledger = world(tmp_path, {**PROFILE, "gc": {"stores": ["app-main-1/out"]}})
    ignoring(root)
    tree = ignored_seat(root, tmp_path, 1, b"only here", "out/a.bin")
    (tree / "out" / "b.bin").write_bytes(b"only here")
    report = sweep.run_pass(ledger, sessions=[], act=True, proc_root=tmp_path / "no-proc")
    objects = [path for path in (archive.root_of(ledger) / "objects").rglob("*") if path.is_file()]
    assert tree.exists() or [path.read_bytes() for path in objects] == [b"only here"]
    assert any("app-main-1/out" in line and "not a store" in line for line in report.warnings), report.warnings


def test_identical_content_goes_once_when_the_archive_already_exists(tmp_path):
    """I7: once objects/ existed, the second tree's file had an archive twin and read as 'changed'."""
    from flotilla.gc import archive
    root, ledger = world(tmp_path)
    ignoring(root)
    ignored_seat(root, tmp_path, 1, b"earlier")
    sweep.run_pass(ledger, sessions=[], act=True, proc_root=tmp_path / "no-proc")
    second, third = ignored_seat(root, tmp_path, 2, b"same"), ignored_seat(root, tmp_path, 3, b"same")
    report = sweep.run_pass(ledger, sessions=[], act=True, proc_root=tmp_path / "no-proc")
    assert not second.exists() and not third.exists(), sweep.lines(report)
    assert len([path for path in (archive.root_of(ledger) / "objects").rglob("*") if path.is_file()]) == 2
    for tree in (second, third):   # each tree's record names its file, so each restores (security review dde4c0a)
        assert any(line.startswith("restored 1 file") for line in archive.restore(ledger, tree.name))
        assert (tree / "out" / "x.bin").read_bytes() == b"same"


# Second review of the branch, I-3: content archived by an earlier pass is an archive twin at the first judgement too;
# counting it as moved during the pass kept the tree as "changed" forever, and small trees are never asked.

def test_content_archived_by_an_earlier_pass_lets_a_later_tree_go(tmp_path):
    from flotilla.gc import archive
    root, ledger = world(tmp_path)
    ignoring(root)
    ignored_seat(root, tmp_path, 1, data=b"same")
    sweep.run_pass(ledger, sessions=[], act=True, proc_root=tmp_path / "no-proc")
    later = ignored_seat(root, tmp_path, 2, data=b"same")
    (later / "out" / "y.bin").write_bytes(b"unique")
    report = sweep.run_pass(ledger, sessions=[], act=True, proc_root=tmp_path / "no-proc")
    assert not later.exists(), sweep.lines(report)
    named = {item["from"] for entry in archive.entries(ledger) for item in entry.files}
    assert {str(later / "out" / "x.bin"), str(later / "out" / "y.bin")} <= named


# Second review of the branch, m-1 and m-10: the pass goes on when the archive fails, and says what it already moved.

def test_a_pass_whose_archive_cannot_be_made_keeps_the_tree_unlocked(tmp_path):
    root, ledger = world(tmp_path)
    ignoring(root)
    tree = ignored_seat(root, tmp_path, 1)
    (tmp_path / "app-gc-archive").write_text("a file where the archive goes", encoding="utf-8")
    report = sweep.run_pass(ledger, sessions=[], act=True, proc_root=tmp_path / "no-proc")
    assert tree.exists() and "locked" not in git(root, "worktree", "list", "--porcelain")
    assert any("app-main-1" in line and line.startswith("kept") for line in sweep.lines(report)), sweep.lines(report)


def test_files_moved_before_an_archive_failure_are_reported(tmp_path, monkeypatch):
    from flotilla.gc import archive
    root, ledger = world(tmp_path)
    ignoring(root)
    tree = ignored_seat(root, tmp_path, 1, data=b"first")
    (tree / "out" / "y.bin").write_bytes(b"second")
    real, calls = archive._store, []

    def failing_second(root_, source):
        calls.append(source)
        if len(calls) == 2:
            raise archive.ArchiveFailed(f"{source} could not be moved: injected")
        return real(root_, source)
    monkeypatch.setattr(archive, "_store", failing_second)
    report = sweep.run_pass(ledger, sessions=[], act=True, proc_root=tmp_path / "no-proc")
    assert tree.exists()
    assert any(line.startswith("archived 1 file") and "flotilla gc restore" in line for line in sweep.lines(report)), \
        sweep.lines(report)
