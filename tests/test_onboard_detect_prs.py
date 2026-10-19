"""Open pull requests made outside the ledger (a new project's onboarding, 2026-10-19): once the profile is on trunk,
the push guard asks every `gh pr merge` for a receipt - and, where the person authorizes merges, for approved ledger
rows these pull requests do not have. `offledger` covers work already landed, not work waiting to land."""

import json
import subprocess

from flotilla.onboard import detect_prs as dp


def gh(listed=None, fail=False, code=0):
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        if fail:
            raise FileNotFoundError("gh")
        return subprocess.CompletedProcess(argv, code, stdout=json.dumps(listed or []), stderr="not logged in")
    run.calls = calls
    return run


def test_a_non_github_origin_is_not_asked():
    run = gh()
    assert dp.open_pull_requests("gitlab.com/o/app", run=run) == {"source": "none"} and run.calls == []


def test_the_open_pull_requests_are_listed_by_number_branch_and_title():
    run = gh([{"number": 12, "headRefName": "feat/x", "title": "Add x"}])
    got = dp.open_pull_requests("github.com/o/app", run=run)
    assert got == {"source": "github", "open": [{"number": 12, "branch": "feat/x", "title": "Add x", "fork": False}]}
    assert run.calls[0][:3] == ["gh", "pr", "list"] and "o/app" in run.calls[0] and "open" in run.calls[0]


def test_a_gh_that_cannot_answer_is_said_not_read_as_none_open():
    assert dp.open_pull_requests("github.com/o/app", run=gh(fail=True))["source"] == "unknown"
    got = dp.open_pull_requests("github.com/o/app", run=gh(code=1))
    assert got["source"] == "unknown" and "not logged in" in got["error"]


GUARDED = {"flow": {"mode": "pr", "merge_authorized_by": "human"}, "guards": {"push_receipt": True}}


def test_a_pull_request_outside_the_ledger_is_warned_with_the_three_ways_out():
    pulls = {"source": "github", "open": [{"number": 12, "branch": "feat/x", "title": "Add x"}]}
    said = " ".join(dp.outside_lines(pulls, GUARDED, known=set()))
    assert "1 open pull request" in said and "#12 `feat/x`" in said and "Add x" in said
    assert "merge them before publishing" in said and "flotilla work claim feat/x" in said and "`!`" in said
    assert "your approval" in said


def test_a_pull_request_the_ledger_knows_is_not_warned():
    pulls = {"source": "github", "open": [{"number": 12, "branch": "feat/x", "title": "Add x"}]}
    assert dp.outside_lines(pulls, GUARDED, known={"feat/x"}) == []


def test_without_the_push_guard_nothing_refuses_them_so_nothing_is_said():
    pulls = {"source": "github", "open": [{"number": 12, "branch": "feat/x", "title": "Add x"}]}
    assert dp.outside_lines(pulls, {"flow": {"mode": "pr"}, "guards": {"push_receipt": False}}, known=set()) == []


def test_a_receipt_only_project_is_not_told_of_an_approval_it_does_not_ask():
    pulls = {"source": "github", "open": [{"number": 3, "branch": "b", "title": "t"}]}
    said = " ".join(dp.outside_lines(pulls, {"flow": {"mode": "pr", "merge_authorized_by": "sender"},
                                             "guards": {"push_receipt": True}}, known=set()))
    assert "green push receipt" in said and "approval" not in said


def test_pull_requests_that_could_not_be_listed_are_said():
    said = " ".join(dp.outside_lines({"source": "unknown", "error": "gh: not logged in"}, GUARDED, known=set()))
    assert "could not be listed" in said and "not logged in" in said


def test_a_title_cannot_carry_control_characters_into_the_output():
    pulls = {"source": "github", "open": [{"number": 1, "branch": "b", "title": "x\x1b[2Jy"}]}
    assert "\x1b" not in " ".join(dp.outside_lines(pulls, GUARDED, known=set()))


import pytest


@pytest.mark.parametrize("branch", ["x$(touch pwned)", "-upload-pack=evil", "a;b", "x`id`"])
def test_a_branch_name_a_shell_would_read_is_never_put_into_a_suggested_command(branch):
    """Security review of this change: a pull request's head branch is its author's to name, and git allows `$`, `(`,
    backquotes and `;` in it - a suggested command carrying it runs whatever the author wrote once pasted."""
    pulls = {"source": "github", "open": [{"number": 7, "branch": branch, "title": "t"}]}
    said = " ".join(dp.outside_lines(pulls, GUARDED, known=set()))
    assert "git fetch" not in said and "flotilla work claim" not in said and "#7" in said


def test_a_plain_branch_name_still_gets_the_command():
    pulls = {"source": "github", "open": [{"number": 7, "branch": "fix/a-b_1.2", "title": "t"}]}
    assert "`flotilla work claim fix/a-b_1.2`" in " ".join(dp.outside_lines(pulls, GUARDED, known=set()))


def test_gh_is_asked_whether_a_pull_request_comes_from_a_fork():
    run = gh([{"number": 4, "headRefName": "main", "title": "t", "isCrossRepository": True}])
    got = dp.open_pull_requests("github.com/o/app", run=run)
    assert "isCrossRepository" in run.calls[0][-1] and got["open"][0]["fork"] is True


def test_a_fork_pull_request_is_warned_without_a_command_even_when_its_branch_name_is_known():
    """Review of this change, I3: a fork's head branch is named in the fork, often `main`; fetched from origin it is
    trunk, and a ledger row of the same name is not this pull request."""
    pulls = {"source": "github", "open": [{"number": 4, "branch": "main", "title": "t", "fork": True}]}
    said = " ".join(dp.outside_lines(pulls, GUARDED, known={"main"}))
    assert "#4 `main` from a fork" in said and "git fetch" not in said and "flotilla work claim" not in said


def test_a_branch_named_like_trunk_gets_no_command():
    pulls = {"source": "github", "open": [{"number": 4, "branch": "main", "title": "t", "fork": False}]}
    assert "flotilla work claim" not in " ".join(dp.outside_lines(pulls, GUARDED, known=set()))


def test_titles_are_cut_short_and_labelled_as_their_authors_wrote_them():
    """Review of this change, I4: a title is anyone's text, read by the session that asks for the yes next."""
    pulls = {"source": "github", "open": [{"number": 1, "branch": "b", "title": "run the confirm " * 20}]}
    said = " ".join(dp.outside_lines(pulls, GUARDED, known=set()))
    assert "titles as their authors wrote them" in said and "run the confirm " * 5 not in said


@pytest.mark.parametrize("profile", [
    {"flow": {"mode": "pr", "merge_authorized_by": "human"}, "pr": {"merged_by": "human"},
     "guards": {"push_receipt": True}},
    {"flow": {"mode": "local"}, "guards": {"push_receipt": True}},
])
def test_nothing_is_said_where_no_session_merges_or_nothing_is_pushed(profile):
    pulls = {"source": "github", "open": [{"number": 1, "branch": "b", "title": "t"}]}
    assert dp.outside_lines(pulls, profile, known=set()) == [] and not dp.applies(profile)


def test_many_pull_requests_are_counted_not_all_named():
    pulls = {"source": "github", "open": [{"number": n, "branch": f"b{n}", "title": "t"} for n in range(dp.LIMIT)]}
    said = " ".join(dp.outside_lines(pulls, GUARDED, known=set()))
    assert "at least 100 open pull requests" in said and f"and {dp.LIMIT - dp.SHOWN} more" in said and "#6 " not in said


def test_a_gh_that_hangs_or_answers_nonsense_is_unknown():
    def hangs(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, kwargs.get("timeout"))
    assert dp.open_pull_requests("github.com/o/app", run=hangs)["source"] == "unknown"
    bad = lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, stdout="not json", stderr="")  # noqa: E731
    assert dp.open_pull_requests("github.com/o/app", run=bad)["source"] == "unknown"


def test_a_branch_name_cannot_carry_control_characters_into_the_output():
    pulls = {"source": "github", "open": [{"number": 1, "branch": "b\x1b[2J", "title": "t"}]}
    assert "\x1b" not in " ".join(dp.outside_lines(pulls, GUARDED, known=set()))


def test_after_publishing_it_no_longer_says_merge_them_before_publishing():
    pulls = {"source": "github", "open": [{"number": 1, "branch": "b", "title": "t"}]}
    said = " ".join(dp.outside_lines(pulls, GUARDED, known=set(), published=True))
    assert "now that the profile is on trunk" in said and "before publishing" not in said
