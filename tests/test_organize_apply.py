from pathlib import Path

import pytest

from flotilla.core import caller
from flotilla.ledger import takein
from flotilla.organize import apply as applying
from flotilla.organize import questions, store
from flotilla.organize import walk as walking
from ledgerkit import git, make_ledger
from organizekit import MERGE_PROFILE, Hub, chain_pulls, project, pull, stacked_branches


class World:
    def __init__(self, root, ledger, hub, profile):
        self.root, self.ledger, self.hub, self.profile = root, ledger, hub, profile
        self.data = store.empty()

    def walk(self):
        return walking.walk(Path(self.root), rows=self.ledger.rows(), profile=self.profile, run=self.hub.run,
                            slug="o/r", ignored=frozenset(self.data["ignored"]))

    def answer(self, qid, *values):
        return questions.record(self.walk(), self.data, qid, list(values))

    def apply(self, *keys):
        return applying.apply(Path(self.root), self.data, keys=keys, walked=self.walk(), profile=self.profile,
                              run=self.hub.run, ledger=self.ledger)

    def rows(self):
        return sorted(self.ledger.rows().values(), key=lambda row: row.id)


@pytest.fixture()
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(caller, "person_refusal", lambda what: "")

    def build(profile=MERGE_PROFILE, names=("docs/a", "docs/b", "docs/c"), push=True):
        root = project(tmp_path, profile=profile)
        heads = stacked_branches(root, *names, push=push)
        hub = Hub(pulls=chain_pulls(heads))
        ledger = make_ledger(root, tmp_path / "state", profile=profile, run=hub.run)
        out = World(root, ledger, hub, profile)
        out.heads = heads
        out.fork = git(root, "rev-parse", "origin/main")
        return out
    return build


def test_a_single_pull_becomes_one_row_based_on_its_fork_point(world):
    w = world(names=("docs/a",))
    w.answer("pr:42", "take")
    lines, complete = w.apply()
    [row] = w.rows()
    assert complete and (row.branch, row.base, row.pr, row.owner) == ("docs/a", w.fork, "42", takein.TAKEN_IN)
    assert row.retarget == "" and row.closes == []
    assert w.data["answers"] == {}
    assert any("#42" in line for line in lines)


def test_separate_works_give_each_review_only_its_own_commits(world):
    w = world()
    w.answer("chain:42-43-44", "take")
    w.answer("chain:42-43-44:shape", "separate")
    w.apply()
    a, b, c = w.rows()
    assert [row.branch for row in (a, b, c)] == ["docs/a", "docs/b", "docs/c"]
    assert (a.base, b.base, c.base) == (w.fork, w.heads["docs/a"], w.heads["docs/b"])
    assert (a.after, b.after, c.after) == ([], [a.id], [b.id])
    assert (a.retarget, b.retarget, c.retarget) == ("", "main", "main")
    assert all(row.requires == [] for row in (a, b, c))


def test_one_work_in_parts_links_with_requires(world):
    w = world()
    w.answer("chain:42-43-44", "take")
    w.answer("chain:42-43-44:shape", "parts")
    w.apply()
    a, b, c = w.rows()
    assert (a.requires, b.requires, c.requires) == ([], [a.id], [b.id])
    assert b.after == [] and c.after == []


def test_one_work_whole_is_one_row_on_the_top_that_closes_the_middle(world):
    w = world()
    w.answer("chain:42-43-44", "take")
    w.answer("chain:42-43-44:shape", "whole")
    w.apply()
    [row] = w.rows()
    assert (row.branch, row.base, row.pr, row.retarget, row.closes) == ("docs/c", w.fork, "44", "main", [43])


def test_one_work_whole_under_squash_closes_the_bottom_too(world):
    w = world(profile={**MERGE_PROFILE, "pr": {"merge_method": "squash"}})
    w.answer("chain:42-43-44", "take")   # squash offers only one work whole: no shape question
    w.apply()
    [row] = w.rows()
    assert row.closes == [42, 43]


def test_a_branch_only_on_origin_is_created_at_the_pull_requests_head(world):
    w = world(names=("docs/a",))
    git(w.root, "branch", "-D", "docs/a")
    w.answer("pr:42", "take")
    w.apply()
    assert git(w.root, "rev-parse", "refs/heads/docs/a") == w.heads["docs/a"]


def test_a_local_branch_that_differs_from_the_head_is_not_taken(world):
    w = world(names=("docs/a",))
    git(w.root, "branch", "-f", "docs/a", "main")
    w.answer("pr:42", "take")
    lines, complete = w.apply()
    assert not complete and w.rows() == []
    assert any(w.heads["docs/a"][:7] in line and "docs/a" in line for line in lines)


def test_apply_asks_again_when_a_head_moved(world):
    """Review Focus 1: the pull request moved between the question and apply - nothing is done, it is asked again."""
    w = world(names=("docs/a",))
    w.answer("pr:42", "take")
    w.hub.pulls[42]["headRefOid"] = "f" * 40
    lines, complete = w.apply()
    assert not complete and w.rows() == []
    assert "pr:42" not in w.data["answers"]
    assert any("changed since you were asked" in line for line in lines)


def test_an_unanswered_item_changes_nothing(world):
    w = world()
    lines, complete = w.apply()
    assert w.rows() == [] and complete


def test_an_ignored_item_is_left_and_not_asked_again(world):
    w = world(names=("docs/a",))
    w.answer("pr:42", "ignore")
    w.apply()
    assert w.rows() == [] and w.walk().items == []


def test_a_branch_without_a_pull_request_is_taken_in(world, tmp_path):
    w = world(names=())
    git(w.root, "checkout", "-q", "-b", "feat/local")
    (Path(w.root) / "x.txt").write_text("x\n", encoding="utf-8")
    git(w.root, "add", "x.txt")
    git(w.root, "commit", "-q", "-m", "local work")
    git(w.root, "checkout", "-q", "main")
    w.answer("branch:feat/local", "take")
    w.apply()
    [row] = w.rows()
    assert (row.branch, row.pr, row.base) == ("feat/local", "", w.fork)


def test_an_item_gone_since_the_question_is_not_carried_out(world):
    """A part of the chain was claimed between the answer and apply: the chain read then is not there now."""
    w = world()
    w.answer("chain:42-43-44", "take")
    w.answer("chain:42-43-44:shape", "separate")
    from flotilla.ledger import core
    from ledgerkit import actor
    core.claim(w.ledger, actor(w.ledger, "main session 1"), "docs/b")
    lines, complete = w.apply()
    assert not complete and [row.branch for row in w.rows()] == ["docs/b"]
    assert any("chain:42-43-44" in line and "no longer" in line for line in lines)
    assert not any(qid.startswith("chain:42-43-44") for qid in w.data["answers"])
