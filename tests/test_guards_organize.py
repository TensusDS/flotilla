from pathlib import Path

import pytest

from flotilla.guards import organize, shell
from flotilla.guards import run as guard_run
from flotilla.ledger.model import Row
from guardkit import onboarded

ROWS = {"r1": Row(id="r1", branch="docs/c", state="claimed", pr="44", closes=[42, 43]),
        "r2": Row(id="r2", branch="feat/x", state="claimed", pr="12")}
NUMBERS = {50}


def judge(command, rows=ROWS, numbers=NUMBERS):
    [segment] = shell.segments(command, Path("/tmp"))
    return organize.check(segment, rows=rows, numbers=numbers)


@pytest.mark.parametrize("command", [
    "gh pr close 43", "gh pr close '#42'", "gh pr edit 44 --base main", "gh pr edit 44 -B main",
    "gh pr edit 44 --base=main", "gh pr close https://github.com/o/r/pull/43", "gh pr close feat/x",
    "gh pr close 50 --comment done", "gh api -X POST repos/o/r/stacks/7/unstack",
    "gh api --method=POST repos/o/r/stacks/7/unstack", "gh stack merge", "gh stack unstack 7", "gh pr close",
    "gh pr edit --base main",
])
def test_outward_commands_on_tracked_work_are_refused(command):
    found = judge(command)
    assert found is not None and found.refuse and "flotilla organize apply" in found.text


@pytest.mark.parametrize("command", [
    "gh pr close 99", "gh pr edit 44 --title x", "gh api repos/o/r/stacks", "gh pr view 43",
    "gh pr close other/branch", "gh stack view", "gh pr edit 99 --base main", "gh api -X GET repos/o/r/stacks",
])
def test_other_commands_pass(command):
    assert judge(command) is None


def test_a_comment_value_is_not_the_target():
    assert judge("gh pr close 99 --comment 43") is None


def test_the_guard_runs_with_the_others_in_a_flotilla_project(tmp_path, monkeypatch):
    root = onboarded(tmp_path)
    monkeypatch.setattr(organize, "tracked", lambda root: (ROWS, NUMBERS))
    found = guard_run.evaluate("gh pr close 43", root, root)
    assert any(item.guard == organize.GUARD and item.refuse for item in found)
    assert not [item for item in guard_run.evaluate("gh pr close 99", root, root) if item.guard == organize.GUARD]


def test_a_ledger_that_cannot_be_read_refuses_a_door(tmp_path, monkeypatch):
    root = onboarded(tmp_path)

    def broken(root):
        raise RuntimeError("no ledger")
    monkeypatch.setattr(organize, "tracked", broken)
    found = guard_run.evaluate("gh pr close 99", root, root)
    assert any(item.guard == organize.GUARD and item.refuse and "cannot tell" in item.text for item in found)
    assert not [item for item in guard_run.evaluate("gh pr view 99", root, root) if item.guard == organize.GUARD]


def test_outside_a_flotilla_project_the_guard_is_quiet(tmp_path):
    found = guard_run.evaluate("gh pr close 43", tmp_path, None)
    assert not [item for item in found if item.guard == organize.GUARD]


# --- background security review of 8e5b605..d2a55bd: what the text cannot tell is refused; equivalent doors -------

@pytest.mark.parametrize("command", [
    'gh pr close "$N"', "gh pr close $(echo 43)", "gh pr close `echo 43`", "gh pr close 4*",
    "gh pr edit ${PR} --base main",
])
def test_a_target_the_shell_expands_cannot_be_told_and_is_refused(command):
    found = judge(command)
    assert found is not None and found.refuse


@pytest.mark.parametrize("command", [
    "gh api -X GET -X POST repos/o/r/stacks/7/unstack",       # gh keeps the last method
    "gh api --method GET --method=POST repos/o/r/stacks/7/unstack",
    "gh api -X PATCH repos/o/r/pulls/43 -f base=main",        # retarget through the REST API
    "gh api repos/o/r/pulls/43 -f state=closed",              # fields mean POST/PATCH-like writes
    "gh api -X PATCH repos/o/r/issues/43 -f state=closed",
    "gh api graphql -f query='mutation { closePullRequest(input: {pullRequestId: \"x\"}) { clientMutationId } }'",
])
def test_equivalent_api_doors_are_refused(command):
    found = judge(command)
    assert found is not None and found.refuse


@pytest.mark.parametrize("command", [
    "gh api -X POST -X GET repos/o/r/stacks",                 # the last method is GET
    "gh api -X PATCH repos/o/r/pulls/99 -f base=main",        # not tracked
    "gh api graphql -f query='query { viewer { login } }'",
    "gh api repos/o/r/pulls/43",
])
def test_reading_and_untracked_api_calls_pass(command):
    assert judge(command) is None


# --- background security review of the push 67a497f..a82ba84 ------------------------------------------------------

@pytest.mark.parametrize("command", [
    "gh api graphql -F query=@close.graphql",                 # the query comes from a file: not told by the text
    "gh api graphql --input body.json",
    "gh api -X PATCH 'repos/o/r/pulls/43?x=1' -f base=main",   # a query string after the number
    "gh api -X PATCH repos/o/r/pulls/43#x -f base=main",
    "gh api -X PATCH repos/o/r/pulls/$N -f base=main",
    "gh api -X PATCH repos/o/r/pulls/forty-three -f base=main",  # a number that cannot be read: not told
])
def test_what_the_api_text_cannot_tell_is_refused(command):
    found = judge(command)
    assert found is not None and found.refuse


@pytest.mark.parametrize("command", [
    "gh api /graphql -F query=@close.graphql",
    "gh api https://api.github.com/graphql -f query='mutation { x }'",
    "gh api GraphQL --input body.json",
    "gh api -X PATCH /repos/o/r/pulls/43 -f base=main",
    "gh api -X PATCH https://api.github.com/repos/o/r/pulls/43 -f base=main",
    "gh api -X PATCH repos/o/r/PULLS/43 -f base=main",
    "gh api -X POST /repos/o/r/stacks/7/unstack",
    "gh api -X POST repos/o/r/Stacks/7/unstack",
])
def test_every_spelling_of_a_tracked_api_path_is_judged(command):
    """Background security review of 4170045: a leading slash, a full URL or another case passed as another path."""
    found = judge(command)
    assert found is not None and found.refuse


@pytest.mark.parametrize("command", [
    "gh api -X $M repos/o/r/pulls/43 -f base=main",           # the method is the shell's to say
    "gh api -X PATCH $P -f base=main",                         # the path is the shell's to say
    "gh api -X PATCH repos/o/r/pul%6Cs/43 -f base=main",       # percent-encoded
    "gh api -X PATCH repos/o/r/x/../pulls/43 -f base=main",    # dot segments
    "gh api -X PATCH repos/o/r//pulls/43 -f base=main",        # an empty segment
    "gh api graphql -f query=$Q",                              # the query text is the shell's to say
])
def test_an_api_write_whose_target_cannot_be_read_is_refused(command):
    """Background security review of 6d55101: rather than chase each spelling, a `gh api` write whose method, path
    or query cannot be read plainly from the text is refused."""
    found = judge(command)
    assert found is not None and found.refuse


@pytest.mark.parametrize("command", [
    "gh api repos/$OWNER/r/pulls/43",                          # a read: refusing it would protect nothing
    "gh api repos/o/r/pulls?state=open",
])
def test_an_api_read_with_an_unclear_path_still_passes(command):
    assert judge(command) is None


@pytest.mark.parametrize("command", [
    "gh api -iX PATCH repos/o/r/pulls/43 -f base=main",        # a short-flag group whose last letter takes a value
    "gh api -X PATCH -if base=main repos/o/r/pulls/43",
    "gh api -X PATCH repos/o/r/{pulls,issues}/43 -f base=main", # brace expansion
    "gh api -X PATCH repos/o/r/pulls/{43,44} -f base=main",
    "gh pr close {42,43}",
    "gh api -X PATCH repos/o/r/@(pulls)/43 -f base=main",       # an extglob
])
def test_what_gh_or_the_shell_would_read_otherwise_is_refused(command):
    """Background security review of the guard's last fix: grouped short flags and brace expansion read otherwise."""
    found = judge(command)
    assert found is not None and found.refuse


def test_ghs_own_placeholders_are_not_brace_expansion():
    assert judge("gh api -X PATCH repos/{owner}/{repo}/pulls/99 -f base=main") is None
