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
    assert got == {"source": "github", "open": [{"number": 12, "branch": "feat/x", "title": "Add x"}]}
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
