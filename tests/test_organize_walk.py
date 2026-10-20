from pathlib import Path

import pytest

from flotilla.ledger.model import Row
from flotilla.organize import walk as walking
from ledgerkit import commit, git
from organizekit import MERGE_PROFILE, Hub, chain_pulls, project, pull, stack, stacked_branches


@pytest.fixture()
def proj(tmp_path):
    return project(tmp_path)


def mine(root, message, name):
    """A commit as the person makes it: the repository's own identity (ledgerkit's commit signs as someone else)."""
    (Path(root) / name).write_text(message + "\n", encoding="utf-8")
    git(root, "add", name)
    git(root, "commit", "-q", "-m", message)
    return git(root, "rev-parse", "HEAD")


def walk(root, hub, rows=None, profile=MERGE_PROFILE, **more):
    return walking.walk(Path(root), rows=rows or {}, profile=profile, run=hub.run, slug="o/r", **more)


def keys(found):
    return [item.key for item in found.items]


def test_the_persons_chain_is_an_item_and_anothers_pull_is_listed(proj):
    heads = stacked_branches(proj, "docs/a", "docs/b", "docs/c")
    theirs = stacked_branches(proj, "their/x")
    hub = Hub(pulls=[*chain_pulls(heads), pull(50, "their/x", "main", theirs["their/x"], author="someone")])
    found = walk(proj, hub)
    assert keys(found) == ["chain:42-43-44"]
    chain = found.items[0]
    assert chain.offered == ["separate", "parts", "whole", "claude"]
    assert [line.split()[0] for line in chain.lines] == ["#42", "#43", "#44"]
    assert all("1 commit(s) of its own" in line for line in chain.lines)
    assert any("#50" in line for line in found.listed)


def test_a_second_walk_skips_taken_and_closed_pulls(proj):
    heads = stacked_branches(proj, "docs/a", "docs/b", "docs/c")
    hub = Hub(pulls=chain_pulls(heads))
    rows = {"r1": Row(id="r1", branch="docs/c", state="claimed", pr="44", closes=[42, 43])}
    found = walk(proj, hub, rows=rows)
    assert found.items == []
    assert not any("docs/a" in line or "docs/b" in line for line in found.listed)


def test_an_unsafe_branch_is_listed_not_offered(proj):
    hub = Hub(pulls=[pull(42, "feat/$(x)", "main", "a" * 40)])
    found = walk(proj, hub)
    assert found.items == [] and any("a shell would read" in line for line in found.listed)


def test_a_fork_pull_request_is_listed_not_offered(proj):
    hub = Hub(pulls=[pull(42, "main", "main", "a" * 40, fork=True)])
    found = walk(proj, hub)
    assert found.items == [] and any("from a fork" in line for line in found.listed)


def test_a_checked_out_branch_is_never_offered_for_deletion(proj, tmp_path):
    git(proj, "branch", "docs/codex-project-skills")
    git(proj, "worktree", "add", "-q", str(tmp_path / "codex"), "docs/codex-project-skills")
    git(proj, "branch", "feat/spike-factors")
    found = walk(proj, Hub())
    assert "no commits of their own, may be deleted: feat/spike-factors" in found.listed
    assert not any("codex-project-skills" in line for line in found.listed)


def test_no_own_commits_and_merged_are_told_apart(proj):
    git(proj, "branch", "feat/spike-factors")
    stacked_branches(proj, "feat/done")
    git(proj, "-c", "user.email=me@example.invalid", "merge", "-q", "--no-ff", "-m", "merge done", "feat/done")
    git(proj, "push", "-q", "origin", "main")
    found = walk(proj, Hub(merged=["feat/squashed"]))
    assert "no commits of their own, may be deleted: feat/spike-factors" in found.listed
    assert "merged, may be deleted: feat/done" in found.listed


def test_codex_files_in_the_range_make_it_not_mine_but_agents_md_alone_does_not(proj):
    git(proj, "checkout", "-q", "-b", "docs/codex")
    mine(proj, "codex notes", "CODEX-TO-CLAUDE.md")
    git(proj, "checkout", "-q", "main")
    git(proj, "checkout", "-q", "-b", "docs/agents")
    mine(proj, "agents file", "AGENTS.md")
    git(proj, "checkout", "-q", "main")
    git(proj, "push", "-q", "origin", "docs/codex", "docs/agents")
    found = walk(proj, Hub())
    assert "branch:docs/agents" in keys(found)
    assert "branch:docs/codex" not in keys(found)
    assert any("docs/codex" in line and "another AI" in line for line in found.listed)


def test_another_authors_branch_is_listed(proj):
    git(proj, "checkout", "-q", "-b", "feat/theirs")
    git(proj, "-c", "user.email=someone@example.invalid", "-c", "user.name=s", "commit", "-q", "--allow-empty",
        "-m", "theirs")
    git(proj, "checkout", "-q", "main")
    found = walk(proj, Hub())
    assert "branch:feat/theirs" not in keys(found)
    assert any("someone@example.invalid" in line for line in found.listed)


def test_before_publish_the_walk_holds_only_the_before_half(tmp_path):
    root = project(tmp_path, published=False)
    (root / ".serena").mkdir()
    (root / ".serena" / "cache.bin").write_text("x", encoding="utf-8")
    heads = stacked_branches(root, "docs/a")
    found = walk(root, Hub(pulls=chain_pulls(heads)))
    assert found.phase == "before"
    assert keys(found) == ["cache:.serena/"]
    assert any("once the profile is on origin's trunk" in note for note in found.notes)


def test_onboard_branch_finds_the_profile_commit_and_what_rides_above_it(tmp_path):
    root = project(tmp_path, published=False)
    git(root, "checkout", "-q", "-b", "chore/flotilla-onboard")
    (root / ".flotilla").mkdir()
    (root / ".flotilla" / "project.toml").write_text("schema = 1\n", encoding="utf-8")
    git(root, "add", ".flotilla")
    profile = commit(root, "chore: onboard flotilla")
    extra = commit(root, "docs: open T-053", "t053.md")
    found = walk(root, Hub())
    item = next(item for item in found.items if item.kind == "onboard")
    assert item.key == "onboard:chore/flotilla-onboard"
    assert (item.facts["profile"], item.facts["extra"], item.facts["direct"]) == (profile, [extra], True)


def test_a_truncated_pull_list_is_said(proj, monkeypatch):
    from flotilla.organize import github
    monkeypatch.setattr(github, "LIMIT", 2)
    hub = Hub(pulls=[pull(n, f"b{n}", "main", "a" * 40, author="someone") for n in (1, 2)])
    found = walk(proj, hub)
    assert any("stopped at 2" in note for note in found.notes)


def test_gh_that_cannot_answer_is_a_note_not_an_empty_walk(proj):
    found = walk(proj, Hub(fail={"pr"}))
    assert any("could not list pull requests" in note for note in found.notes)


def test_a_native_stack_is_one_item(proj):
    heads = stacked_branches(proj, "docs/a", "docs/b")
    found = walk(proj, Hub(pulls=chain_pulls(heads), stacks=[stack(7, 42, 43)]))
    assert keys(found) == ["stack:7"]
    assert "unstacks it on GitHub" in found.items[0].title


def test_squash_offers_a_chain_only_one_work_whole(proj):
    heads = stacked_branches(proj, "docs/a", "docs/b")
    found = walk(proj, Hub(pulls=chain_pulls(heads)), profile={**MERGE_PROFILE, "pr": {"merge_method": "squash"}})
    assert found.items[0].offered == ["whole"]


def test_ignored_items_are_not_asked_again(proj):
    heads = stacked_branches(proj, "docs/a")
    found = walk(proj, Hub(pulls=chain_pulls(heads)), ignored={"pr:42"})
    assert found.items == [] and any("pr:42" in note for note in found.notes)


def test_a_named_pull_request_of_anothers_is_offered(proj):
    theirs = stacked_branches(proj, "their/x")
    found = walk(proj, Hub(pulls=[pull(50, "their/x", "main", theirs["their/x"], author="someone")]), only=("#50",))
    assert keys(found) == ["pr:50"]


def test_uncommitted_edits_are_an_item_and_onboardings_own_are_not(proj):
    (proj / "notes.txt").write_text("half done\n", encoding="utf-8")
    (proj / ".flotilla" / "extra.md").write_text("x\n", encoding="utf-8")
    found = walk(proj, Hub())
    item = next(item for item in found.items if item.kind == "edits")
    assert item.facts["paths"] == ["notes.txt"]


def test_local_trunk_commits_are_an_item(proj):
    commit(proj, "local only", "local.txt")
    found = walk(proj, Hub())
    assert "trunk:main" in keys(found)


def test_a_clean_repository_walks_to_nothing(proj):
    found = walk(proj, Hub())
    assert found.items == [] and found.listed == []


def test_a_branch_reused_after_its_pull_request_merged_is_work_not_merged(proj):
    """Background security review of 5196c72: a merged pull request's name alone called a branch merged, so new
    commits on a reused name would have been offered for deletion. A merged pull request counts only for the head it
    merged."""
    heads = stacked_branches(proj, "feat/reused")
    hub = Hub(merged=[("feat/reused", "f" * 40)])
    found = walk(proj, hub)
    assert not any("feat/reused" in line and "may be deleted" in line for line in found.listed)
    assert any("feat/reused" in line for line in found.listed)   # work - another author's here, so only listed
    hub = Hub(merged=[("feat/reused", heads["feat/reused"])])
    assert "merged, may be deleted: feat/reused" in walk(proj, hub).listed


def test_a_fork_pull_requests_merged_name_says_nothing_of_a_branch_here(proj):
    git(proj, "branch", "feat/forked")
    hub = Hub(merged=[("feat/forked", git(proj, "rev-parse", "HEAD"), True)])
    assert "no commits of their own, may be deleted: feat/forked" in walk(proj, hub).listed


def test_a_shell_active_name_is_never_listed_for_deletion_as_it_stands(proj):
    git(proj, "branch", "feat/a;b")
    found = walk(proj, Hub())
    assert not any("feat/a;b" in line and "may be deleted" in line for line in found.listed)
    assert any("a name a shell would read" in line for line in found.listed)
