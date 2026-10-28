"""The collector's questions (gc design, sections 3 and 9): one per piece of work across its trees, through
onboarding's question door, bound to what the person was shown, with "accept all as proposed" for the archive."""

from flotilla.gc import questions, refs, store, trees, twins
from flotilla.ledger import core
from ledgerkit import PROFILE, actor, drive, git, make_ledger, repo_with_origin


def world(tmp_path, profile=None):
    root = repo_with_origin(tmp_path)
    return root, make_ledger(root, tmp_path / "state", profile=profile or PROFILE, live=())


def seat(root, tmp_path, name, *args):
    tree = tmp_path / name
    git(root, "worktree", "add", "-q", *args, str(tree))
    return tree


def verdicts(ledger, *found):
    finder = twins.Twins(ledger, holders=refs.holders(ledger), cache={},
                         sources=twins.sources(ledger, untouched=[], archive_root=ledger.root.parent / "none"))
    return {tree: trees.judge_tree(ledger, tree, holders=refs.holders(ledger), sessions=[], twins=finder,
                                   proc_root=tree.parent / "no-proc") for tree in found}


def items(ledger, *found, small=()):
    return questions.items(verdicts(ledger, *found), rows=ledger.rows(), small=set(small))


def shown(found, data, index=0):
    """The id of a question as `next` shows it: the item's key and the fingerprint of what was shown."""
    return questions.all_questions(found, data)[index]["id"]


def untracked(tree, name="new.txt"):
    (tree / name).write_text("n\n", encoding="utf-8")


def archive_only(root, tmp_path, number):
    tree = seat(root, tmp_path, f"app-main-{number}", "-b", f"fleet/main-{number}")
    (tree / "out").mkdir()
    (tree / "out" / "big.bin").write_bytes(b"x" * number)
    return tree


def test_two_trees_of_one_row_are_one_question(tmp_path):
    root, ledger = world(tmp_path)
    row = drive(root, ledger, "feat/x", to="handed")
    core.release(ledger, actor(ledger, "main session 1"), "feat/x", why="not wanted")
    builder = tmp_path / "app-main-1"
    git(root, "worktree", "add", "-q", str(builder), "feat/x")
    reviewer = tmp_path / "app-review-1"
    git(root, "worktree", "add", "-q", "--detach", str(reviewer), row.tip)
    untracked(builder)
    untracked(reviewer)
    found = items(ledger, builder, reviewer)
    assert [item.key for item in found] == [f"work:{row.id}"]
    assert sorted(found[0].trees) == sorted([str(builder), str(reviewer)])
    asked = questions.all_questions(found, store.empty())
    assert [question["id"].rsplit("@", 1)[0] for question in asked] == [f"work:{row.id}"]


def test_accept_all_records_the_proposal_per_item(tmp_path):
    root, ledger = world(tmp_path)
    git(root, "commit", "-q", "--allow-empty", "-m", "base")
    (root / ".gitignore").write_text("out/\n", encoding="utf-8")
    git(root, "add", ".gitignore")
    git(root, "-c", "user.email=t@example.invalid", "-c", "user.name=t", "commit", "-q", "-m", "ignore")
    first, second = archive_only(root, tmp_path, 1), archive_only(root, tmp_path, 2)
    found = items(ledger, first, second)
    assert [item.proposed for item in found] == ["archive", "archive"]
    data = store.empty()
    asked = questions.all_questions(found, data)
    assert asked[0]["id"].startswith("gc:all@") and len(asked) == 3
    questions.record(found, data, asked[0]["id"], ["yes"])
    recorded = [data["answers"][item.key] for item in found]
    assert [answer["value"] for answer in recorded] == ["archive", "archive"]
    assert recorded[0]["seen"] != recorded[1]["seen"]
    assert questions.all_questions(found, data) == []


def test_keep_is_not_asked_again_until_something_changes(tmp_path):
    root, ledger = world(tmp_path)
    tree = seat(root, tmp_path, "app-main-1", "-b", "fleet/main-1")
    untracked(tree)
    found = items(ledger, tree)
    data = store.empty()
    assert questions.record(found, data, shown(found, data), ["keep"]).startswith("recorded")
    assert questions.all_questions(items(ledger, tree), data) == []
    untracked(tree, "another.txt")
    asked = questions.all_questions(items(ledger, tree), data)
    assert [question["id"].rsplit("@", 1)[0] for question in asked] == [found[0].key]


def test_archive_is_not_offered_for_a_nested_repository(tmp_path):
    root, ledger = world(tmp_path)
    (root / ".gitignore").write_text("vendor/\n", encoding="utf-8")
    git(root, "add", ".gitignore")
    git(root, "-c", "user.email=t@example.invalid", "-c", "user.name=t", "commit", "-q", "-m", "ignore")
    tree = seat(root, tmp_path, "app-main-1", "-b", "fleet/main-1")
    (tree / "vendor" / "lib" / ".git").mkdir(parents=True)
    found = items(ledger, tree)
    assert found[0].offered == ["junk", "keep"]
    options = [option["value"] for option in questions.all_questions(found, store.empty())[0]["options"]]
    assert options == ["junk", "keep"]


def test_typed_words_wait_on_the_session(tmp_path):
    root, ledger = world(tmp_path)
    tree = seat(root, tmp_path, "app-main-1", "-b", "fleet/main-1")
    untracked(tree)
    found = items(ledger, tree)
    data = store.empty()
    questions.record(found, data, shown(found, data), ["commit", "these", "first"])
    assert data["answers"][found[0].key]["value"] == "words"
    waiting = questions.waiting(found, data)
    assert len(waiting) == 1 and found[0].key in waiting[0] and "commit these first" in waiting[0]


def test_a_tier_three_item_has_no_proposal(tmp_path):
    root, ledger = world(tmp_path)
    first = seat(root, tmp_path, "app-main-1", "-b", "fleet/main-1")
    second = seat(root, tmp_path, "app-main-2", "-b", "fleet/main-2")
    untracked(first)
    untracked(second)
    found = items(ledger, first, second)
    assert [item.proposed for item in found] == ["", ""]
    assert not [question for question in questions.all_questions(found, store.empty())
                if question["id"].startswith("gc:all")]


def test_a_small_tree_is_not_asked(tmp_path):
    root, ledger = world(tmp_path)
    (root / ".gitignore").write_text("out/\n", encoding="utf-8")
    git(root, "add", ".gitignore")
    git(root, "-c", "user.email=t@example.invalid", "-c", "user.name=t", "commit", "-q", "-m", "ignore")
    tree = archive_only(root, tmp_path, 1)
    assert items(ledger, tree, small=[tree]) == []


def test_the_store_round_trips(tmp_path):
    data = store.empty()
    data["answers"]["work:r1"] = {"value": "keep", "seen": "x", "at": "now"}
    store.save(tmp_path / "state", "key", data)
    assert store.load(tmp_path / "state", "key") == data
    assert store.load(tmp_path / "state", "other") == store.empty()


# Security review of 089743b: consent is bound to what was shown, and what is shown cannot drive the terminal.

def test_an_answer_to_what_changed_since_it_was_shown_is_refused(tmp_path):
    import pytest
    root, ledger = world(tmp_path)
    tree = seat(root, tmp_path, "app-main-1", "-b", "fleet/main-1")
    untracked(tree)
    found = items(ledger, tree)
    data = store.empty()
    asked = shown(found, data)
    untracked(tree, "appeared-after.txt")
    with pytest.raises(questions.AnswerError, match="changed since it was shown"):
        questions.record(items(ledger, tree), data, asked, ["junk"])
    assert data["answers"] == {}


def test_accept_all_covers_only_the_proposals_shown(tmp_path):
    import pytest
    root, ledger = world(tmp_path)
    (root / ".gitignore").write_text("out/\n", encoding="utf-8")
    git(root, "add", ".gitignore")
    git(root, "-c", "user.email=t@example.invalid", "-c", "user.name=t", "commit", "-q", "-m", "ignore")
    first, second = archive_only(root, tmp_path, 1), archive_only(root, tmp_path, 2)
    data = store.empty()
    asked = shown(items(ledger, first, second), data)
    third = archive_only(root, tmp_path, 3)
    with pytest.raises(questions.AnswerError, match="changed since it was shown"):
        questions.record(items(ledger, first, second, third), data, asked, ["yes"])
    assert data["answers"] == {}


def test_the_question_shows_control_characters_visibly(tmp_path):
    root, ledger = world(tmp_path)
    tree = seat(root, tmp_path, "app-\x1b[2J-1", "-b", "fleet/main-1")   # git quotes file names, not this
    untracked(tree)
    text = questions.all_questions(items(ledger, tree), store.empty())[0]["question"]
    assert "\x1b" not in text and "app-\\x1b[2J-1" in text
