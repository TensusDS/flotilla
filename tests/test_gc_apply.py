"""The apply step (gc design, section 9): only the collector acts on an answer, and only on the answer as recorded -
anything that moved since the person was shown it is asked again."""

from flotilla.core.census import Session
from flotilla.gc import apply, archive, questions, refs, store, trees, twins
from ledgerkit import PROFILE, commit, git, make_ledger, repo_with_origin


def world(tmp_path):
    root = repo_with_origin(tmp_path)
    return root, make_ledger(root, tmp_path / "state", profile=PROFILE, live=())


def session(name, cwd):
    return Session(name=name, session_id=name, kind="background", pid=None, short_id=None, status="idle",
                   state=None, cwd=str(cwd), started_at_ms=None)


def found(ledger, tree, sessions=()):
    finder = twins.Twins(ledger, holders=refs.holders(ledger), cache={},
                         sources=twins.sources(ledger, untouched=[], archive_root=archive.root_of(ledger)))
    verdicts = {tree: trees.judge_tree(ledger, tree, holders=refs.holders(ledger), sessions=list(sessions),
                                       twins=finder, proc_root=tree.parent / "no-proc")}
    return questions.items(verdicts, rows=ledger.rows(), small=set())


def answered(ledger, tree, value):
    data = store.empty()
    shown = found(ledger, tree)
    questions.record(shown, data, questions.all_questions(shown, data)[0]["id"], [value])
    return data


def carry_out(ledger, tree, data, sessions=()):
    return apply.apply(ledger, data, items=found(ledger, tree, sessions), sessions=list(sessions),
                       proc_root=tree.parent / "no-proc")


def lone_tree(root, tmp_path):
    """A tree with an untracked file, a tracked edit and a detached HEAD only it holds."""
    commit(root, "tracked", "t.txt", "t\n")
    tree = tmp_path / "app-main-1"
    git(root, "worktree", "add", "-q", "-b", "fleet/main-1", str(tree))
    head = commit(tree, "only here", "w.txt", "w\n")
    git(tree, "checkout", "-q", "--detach")
    git(root, "branch", "-D", "fleet/main-1")
    (tree / "t.txt").write_text("edited\n", encoding="utf-8")
    (tree / "notes.txt").write_text("mine\n", encoding="utf-8")
    return tree, head


def test_archive_and_remove_saves_files_and_edits_and_parks_head(tmp_path):
    root, ledger = world(tmp_path)
    tree, head = lone_tree(root, tmp_path)
    data = answered(ledger, tree, "archive")
    lines, complete = carry_out(ledger, tree, data)
    assert complete and not tree.exists(), lines
    entry = archive.entries(ledger)[0]
    assert [item["from"] for item in entry.files] == [str(tree / "notes.txt")]
    assert entry.approved_by.startswith("answer work:")
    patch = archive.root_of(ledger) / "entries" / entry.id / entry.trees[0]["patch"]
    assert b"edited" in patch.read_bytes()
    assert head in git(root, "for-each-ref", "--format=%(objectname)", "refs/flotilla/parked/").split()
    assert data["answers"] == {}


def test_junk_removes_for_good_but_parks_the_commits(tmp_path):
    root, ledger = world(tmp_path)
    tree, head = lone_tree(root, tmp_path)
    data = answered(ledger, tree, "junk")
    lines, complete = carry_out(ledger, tree, data)
    assert complete and not tree.exists(), lines
    assert archive.entries(ledger) == []
    assert head in git(root, "for-each-ref", "--format=%(objectname)", "refs/flotilla/parked/").split()
    assert [answer["value"] for answer in data["answers"].values()] == ["junk"]


def test_a_tree_that_changed_since_the_answer_is_asked_again(tmp_path):
    root, ledger = world(tmp_path)
    tree, _ = lone_tree(root, tmp_path)
    data = answered(ledger, tree, "junk")
    (tree / "appeared.txt").write_text("new\n", encoding="utf-8")
    lines, complete = carry_out(ledger, tree, data)
    assert not complete and tree.exists() and (tree / "appeared.txt").exists()
    assert any("changed since you answered" in line for line in lines), lines
    assert data["answers"] == {}


def test_a_live_session_appearing_since_stops_the_apply(tmp_path):
    root, ledger = world(tmp_path)
    tree, _ = lone_tree(root, tmp_path)
    data = answered(ledger, tree, "junk")
    lines, complete = carry_out(ledger, tree, data, sessions=[session("minor session 3", tree / "src")])
    assert not complete and tree.exists(), lines


def test_keep_does_nothing(tmp_path):
    root, ledger = world(tmp_path)
    tree, _ = lone_tree(root, tmp_path)
    data = answered(ledger, tree, "keep")
    lines, complete = carry_out(ledger, tree, data)
    assert complete and (tree / "notes.txt").exists() and any("kept" in line for line in lines)
    assert [answer["value"] for answer in data["answers"].values()] == ["keep"]


# Final review of gc stage 2.

def test_a_quoted_file_name_that_changed_since_the_answer_is_asked_again(tmp_path):
    """C1: porcelain quotes `my render.txt`; the fingerprint stat'ed the quoted name and never saw the rewrite."""
    root, ledger = world(tmp_path)
    tree = tmp_path / "app-main-1"
    git(root, "worktree", "add", "-q", "-b", "fleet/main-1", str(tree))
    (tree / "my render.txt").write_text("first\n", encoding="utf-8")
    (tree / "\u0440\u0435\u043d\u0434\u0435\u0440.txt").write_text("x\n", encoding="utf-8")
    data = answered(ledger, tree, "junk")
    (tree / "my render.txt").write_text("rewritten, fifty times more\n" * 50, encoding="utf-8")
    lines, complete = carry_out(ledger, tree, data)
    assert not complete and tree.exists() and any("changed since you answered" in line for line in lines), lines


def test_the_saved_edits_survive_the_persons_diff_settings(tmp_path, monkeypatch):
    """C2: `color.ui=always` (or an external diff) made the patch unusable, and the tree went anyway."""
    root, ledger = world(tmp_path)
    tree, _ = lone_tree(root, tmp_path)
    monkeypatch.setenv("GIT_CONFIG_COUNT", "2")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "color.ui")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "always")
    monkeypatch.setenv("GIT_CONFIG_KEY_1", "diff.noprefix")
    monkeypatch.setenv("GIT_CONFIG_VALUE_1", "true")
    data = answered(ledger, tree, "archive")
    lines, complete = carry_out(ledger, tree, data)
    assert complete and not tree.exists(), lines
    entry = archive.entries(ledger)[0]
    patch = (archive.root_of(ledger) / "entries" / entry.id / entry.trees[0]["patch"]).read_bytes()
    assert b"\x1b[" not in patch and b"a/t.txt" in patch


def test_an_untracked_nested_repository_is_never_offered_to_the_archive(tmp_path):
    """I5: `ls-files --others` names it `sub/`; archiving it crashed apply and left the tree locked."""
    root, ledger = world(tmp_path)
    tree = tmp_path / "app-main-1"
    git(root, "worktree", "add", "-q", "-b", "fleet/main-1", str(tree))
    (tree / "sub").mkdir()
    git(tree / "sub", "init", "-q")
    shown = found(ledger, tree)
    assert "archive" not in shown[0].offered and "nested" in [d.kind for d in shown[0].verdicts[0].doubts]


def test_an_archive_that_fails_leaves_the_tree_unlocked_and_kept(tmp_path, monkeypatch):
    """I5: an OSError inside the archive left the gc lock on the tree, so every later apply saw 'a session locked'."""
    root, ledger = world(tmp_path)
    tree, _ = lone_tree(root, tmp_path)
    data = answered(ledger, tree, "archive")

    def broken(*_args, **_kwargs):
        raise PermissionError("denied")

    monkeypatch.setattr(archive, "_store", broken)
    lines, complete = carry_out(ledger, tree, data)
    assert not complete and tree.exists(), lines
    listed = git(root, "worktree", "list", "--porcelain")
    assert "locked" not in listed.split(f"worktree {tree}", 1)[1].split("worktree ", 1)[0]
