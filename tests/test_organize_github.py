import pytest

from flotilla.organize import github
from organizekit import Hub, pull, stack


def test_pulls_reads_every_field_organize_needs():
    hub = Hub(pulls=[pull(42, "docs/a", "main", "a" * 40, author="me", draft=True, body="Generated with Claude Code")])
    found, truncated = github.pulls("o/r", hub.run)
    assert found == [github.Pull(number=42, branch="docs/a", head="a" * 40, base="main", author="me", draft=True,
                                 fork=False, title="work on docs/a", body="Generated with Claude Code")]
    assert truncated is False
    asked = hub.calls[0]
    assert asked[:4] == ["pr", "list", "-R", "o/r"] and str(github.LIMIT) in asked


def test_a_full_page_is_said_to_be_cut():
    hub = Hub(pulls=[pull(n, f"b{n}", "main", "a" * 40) for n in range(1, github.LIMIT + 1)])
    assert github.pulls("o/r", hub.run)[1] is True


@pytest.mark.parametrize("call", [lambda run: github.pulls("o/r", run), lambda run: github.stacks("o/r", run),
                                  lambda run: github.login(run)])
def test_gh_that_cannot_answer_is_unknown_not_none(call):
    hub = Hub(fail={"pr", "api"})
    with pytest.raises(github.GitHubUnknown):
        call(hub.run)


def test_stacks_lists_open_members_bottom_first():
    hub = Hub(stacks=[stack(7, 5, 6, merged={5}), {**stack(4, 1), "open": False}])
    assert github.stacks("o/r", hub.run) == [github.Stack(number=7, base="main", members=(6,))]


def test_unstack_posts_to_the_stack():
    hub = Hub(stacks=[stack(7, 42, 43)])
    github.unstack("o/r", 7, hub.run)
    assert ["api", "-X", "POST", "repos/o/r/stacks/7/unstack"] in hub.calls
    assert github.stacks("o/r", hub.run) == []


def test_login_asks_gh_for_the_person():
    assert github.login(Hub(login="TensusDS").run) == "TensusDS"


def test_close_and_pull_now():
    hub = Hub(pulls=[pull(43, "b", "a", "c" * 40)])
    assert github.pull_now("o/r", 43, hub.run) == {"state": "OPEN", "head": "c" * 40, "base": "a"}
    github.close("o/r", 43, "delivered", hub.run)
    assert github.pull_now("o/r", 43, hub.run)["state"] == "CLOSED"
