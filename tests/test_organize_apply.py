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
    from flotilla.organize import github
    monkeypatch.setattr(caller, "person_refusal", lambda what: "")
    monkeypatch.setattr(github, "slug_of", lambda root, run=None: "o/r")

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


# --- outward answers: unstack, own words, the local trunk, caches -----------------------------------------------

from organizekit import stack  # noqa: E402


def stacked_world(world, stuck=()):
    w = world(names=("docs/a", "docs/b"))
    w.hub.stacks = [stack(7, 42, 43)]
    w.hub.stuck = set(stuck)
    return w


def test_a_native_stack_is_unstacked_then_taken_in_as_a_chain(world):
    w = stacked_world(world)
    w.answer("stack:7", "take")
    w.answer("stack:7:shape", "separate")
    lines, complete = w.apply()
    assert complete, lines
    assert ["api", "-X", "POST", "repos/o/r/stacks/7/unstack"] in w.hub.calls
    a, b = w.rows()
    assert (a.pr, b.pr, b.base, b.retarget) == ("42", "43", w.heads["docs/a"], "main")


def test_a_member_left_stacked_stops_the_take_in(world):
    """Review Focus 3: GitHub leaves a member with auto-merge stacked; nothing else is done for that stack."""
    w = stacked_world(world, stuck={43})
    w.answer("stack:7", "take")
    w.answer("stack:7:shape", "separate")
    lines, complete = w.apply()
    assert not complete and w.rows() == []
    assert any("#43" in line and "still stacked" in line for line in lines)


def propose_steps(w, key, steps):
    w.data["proposals"][key] = {"steps": steps}
    w.answer(f"{key}:steps", "yes")


def test_own_words_close_runs_only_the_confirmed_step(world):
    w = world()
    w.answer("chain:42-43-44", "#43", "is", "stale,", "close", "it")
    propose_steps(w, "chain:42-43-44", [{"do": "close", "pr": 43}])
    lines, complete = w.apply()
    assert complete, lines
    closed = [call for call in w.hub.calls if call[:2] == ["pr", "close"]]
    assert [call[2] for call in closed] == ["43"]
    assert w.rows() == []


def test_own_words_may_not_reach_beyond_their_item(world):
    w = world(names=("docs/a",))
    w.answer("pr:42", "close", "the", "other", "one")
    propose_steps(w, "pr:42", [{"do": "close", "pr": 99}])
    lines, complete = w.apply()
    assert not complete and not any(call[:2] == ["pr", "close"] for call in w.hub.calls)
    assert any("#99" in line for line in lines)


def test_own_words_take_with_a_shape(world):
    w = world()
    w.answer("chain:42-43-44", "take", "it", "as", "one", "piece")
    propose_steps(w, "chain:42-43-44", [{"do": "take", "shape": "whole"}])
    w.apply()
    [row] = w.rows()
    assert (row.branch, row.closes) == ("docs/c", [43])


def test_own_words_delete_a_branch_keep_it_and_leave_the_drop_to_the_person(world):
    w = world(names=())
    git(w.root, "checkout", "-q", "-b", "feat/old")
    (Path(w.root) / "o.txt").write_text("o\n", encoding="utf-8")
    git(w.root, "add", "o.txt")
    git(w.root, "commit", "-q", "-m", "old")
    tip = git(w.root, "rev-parse", "HEAD")
    git(w.root, "checkout", "-q", "main")
    w.answer("branch:feat/old", "delete", "it")
    propose_steps(w, "branch:feat/old", [{"do": "delete-branch", "branch": "feat/old"}])
    lines, _ = w.apply()
    assert git(w.root, "rev-parse", "refs/heads/feat/old") == tip          # still there: the person deletes it
    assert git(w.root, "rev-parse", f"refs/flotilla/dropped/{tip}") == tip
    assert any("! git branch -D feat/old" in line for line in lines)


def local_commit(w, message="local only"):
    (Path(w.root) / "local.txt").write_text(message + "\n", encoding="utf-8")
    git(w.root, "add", "local.txt")
    git(w.root, "commit", "-q", "-m", message)
    return git(w.root, "rev-parse", "HEAD")


def test_moving_local_trunk_commits_never_uses_hard(world):
    w = world(names=())
    head = local_commit(w)
    calls = []
    real = w.hub.run

    def spy(cmd, **kwargs):
        calls.append(list(cmd))
        return real(cmd, **kwargs)
    w.hub.run = spy
    w.answer("trunk:main", "move")
    lines, complete = w.apply()
    assert complete, lines
    assert not any("--hard" in call for call in calls)
    assert git(w.root, "rev-parse", "main") == git(w.root, "rev-parse", "origin/main")
    assert git(w.root, "rev-parse", f"refs/heads/work/local-{head[:7]}") == head
    [row] = w.rows()
    assert row.branch == f"work/local-{head[:7]}"


def test_dropping_prints_the_command_and_keeps_the_commits(world):
    w = world(names=())
    head = local_commit(w)
    w.answer("trunk:main", "drop")
    lines, _ = w.apply()
    assert git(w.root, "rev-parse", "main") == head
    assert git(w.root, "rev-parse", f"refs/flotilla/dropped/{head}") == head
    assert any("! git reset --keep origin/main" in line for line in lines)


def test_trunk_checked_out_in_another_tree_is_not_moved(world, tmp_path):
    w = world(names=())
    head = local_commit(w)
    git(w.root, "checkout", "-q", "-b", "elsewhere")
    git(w.root, "worktree", "add", "-q", str(tmp_path / "trunk-tree"), "main")
    w.answer("trunk:main", "move")
    lines, complete = w.apply()
    assert not complete and git(w.root, "rev-parse", "main") == head
    assert any("trunk-tree" in line for line in lines)


def test_a_cache_is_excluded_once(tmp_path, monkeypatch):
    monkeypatch.setattr(caller, "person_refusal", lambda what: "")
    root = project(tmp_path, published=False)
    (root / ".flotilla").mkdir()
    (root / ".flotilla" / "project.toml").write_text('schema = 1\n[trunk]\nbranch = "main"\n', encoding="utf-8")
    (root / ".serena").mkdir()
    (root / ".serena" / "c").write_text("x", encoding="utf-8")
    hub = Hub()
    w = World(root, make_ledger(root, tmp_path / "state", run=hub.run), hub, MERGE_PROFILE)
    w.answer("cache:.serena/", "exclude")
    w.apply()
    assert not [item for item in w.walk().items if item.kind == "cache"]
    applying.exclude(root, ".serena/", run=hub.run)   # a second exclude adds nothing
    exclude = (root / ".git" / "info" / "exclude").read_text(encoding="utf-8")
    assert exclude.count("/.serena/") == 1


# --- uncommitted edits ------------------------------------------------------------------------------------------

def edits_world(world):
    w = world(names=())
    (Path(w.root) / "fix.py").write_text("fixed\n", encoding="utf-8")
    (Path(w.root) / "notes.md").write_text("notes\n", encoding="utf-8")
    (Path(w.root) / "config.py").write_text("TOKEN = 'ghp_" + "A1b2C3d4E5" * 4 + "'\n", encoding="utf-8")
    return w


def test_the_plan_is_carried_out_exactly_and_each_branch_is_taken_in(world, monkeypatch):
    from flotilla.organize import secrets
    monkeypatch.setattr(secrets.shutil, "which", lambda name: None)
    w = edits_world(world)
    key = f"edits:{w.root}"
    w.answer(key, "discretion")
    w.data["proposals"][key] = {"plan": [{"branch": "fix/a", "paths": ["fix.py", "config.py"]},
                                         {"branch": "docs/n", "paths": ["notes.md"]}]}
    w.answer(f"{key}:plan", "yes")
    lines, complete = w.apply()
    assert complete, lines
    assert git(w.root, "show", "fix/a:fix.py") == "fixed"
    assert "config.py" not in git(w.root, "ls-tree", "--name-only", "fix/a")       # the secret stayed out
    assert (Path(w.root) / "config.py").exists() and not (Path(w.root) / "fix.py").exists()
    assert any("config.py" in line and "secret" in line for line in lines)
    assert sorted(row.branch for row in w.rows()) == ["docs/n", "fix/a"]
    kept = git(w.root, "for-each-ref", "--format=%(refname)", "refs/flotilla/kept")
    assert kept and git(w.root, "show", f"{kept.split()[0]}:fix.py") == "fixed"


def test_leave_as_is_touches_nothing(world):
    w = edits_world(world)
    key = f"edits:{w.root}"
    before = git(w.root, "status", "--porcelain")
    w.answer(key, "leave")
    lines, complete = w.apply()
    assert complete and git(w.root, "status", "--porcelain") == before and w.rows() == []
    assert any("fleet does not see" in line for line in lines)


def test_a_plan_proposed_against_other_paths_is_refused_at_apply(world):
    w = edits_world(world)
    key = f"edits:{w.root}"
    w.answer(key, "discretion")
    w.data["proposals"][key] = {"plan": [{"branch": "fix/a", "paths": ["nope.py"]}]}
    w.answer(f"{key}:plan", "yes")
    lines, complete = w.apply()
    assert not complete and w.rows() == [] and (Path(w.root) / "fix.py").exists()
