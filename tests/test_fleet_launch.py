from pathlib import Path

import pytest

from flotilla.fleet import launch
from flotilla.posts import TEMPLATE_DIR, load_post
from ledgerkit import git, repo_with_origin

POSTS = {p.name: p for p in (load_post(path) for path in TEMPLATE_DIR.glob("*.md"))}
MAIN = Path("/work/app")


def test_a_seat_is_a_sibling_tree_on_a_fleet_branch():
    seat = launch.seat_for(MAIN, POSTS["reviewer"], "review session 3")
    assert (seat.tree, seat.branch, seat.number) == (Path("/work/app-reviewer-3"), "fleet/reviewer-3", 3)


def test_the_main_checkout_is_found_from_any_linked_tree(tmp_path):
    root = repo_with_origin(tmp_path)
    git(root, "worktree", "add", "-q", "-b", "side", str(tmp_path / "side"))
    assert launch.main_checkout(tmp_path / "side") == root.resolve()


def test_the_questionnaire_answer_sets_the_permission_mode():
    post = POSTS["main"]
    assert launch.permission_mode({"permissions": {"mode": "ask"}}, post) == "manual"
    assert launch.permission_mode({"permissions": {"mode": "rules"}}, post) == "dontAsk"
    assert launch.permission_mode({"permissions": {"mode": "auto"}}, post) == "auto"
    with pytest.raises(launch.LaunchError, match="ask, rules or auto"):
        launch.permission_mode({"permissions": {"mode": "sometimes"}}, post)


def test_a_posts_own_permission_mode_wins():
    import dataclasses
    lead = dataclasses.replace(POSTS["orchestrator"], permission_mode="plan")
    assert launch.permission_mode({"permissions": {"mode": "auto"}}, lead) == "plan"


def test_the_strongest_reviewer_answer_reaches_every_post_that_may_accept():
    profile = {"fleet": {"model": "reviewer-strongest"}}
    assert launch.model_for(profile, POSTS["reviewer"]) == "opus"
    assert launch.model_for(profile, POSTS["main"]) == ""
    assert launch.model_for({"fleet": {"model": "one"}}, POSTS["reviewer"]) == ""


def test_the_argv_names_the_session_its_tree_and_its_post():
    seat = launch.seat_for(MAIN, POSTS["reviewer"], "review session 3")
    argv = launch.argv(seat, POSTS["reviewer"], {"permissions": {"mode": "auto"}}, main=MAIN)
    assert argv[:8] == ["claude", "--bg", "-n", "review session 3", "--add-dir", "/work/app-reviewer-3",
                        "--permission-mode", "auto"]
    prompt = argv[argv.index("--append-system-prompt") + 1]
    assert '"review session 3"' in prompt and "`reviewer`" in prompt and str(launch.CLI) in prompt
    assert POSTS["reviewer"].body.strip()[:40] in prompt
    assert argv[-1] == launch.FIRST_PROMPT
    assert launch.CLI.is_file()


def test_a_named_fleet_model_runs_every_post_without_its_own():
    import dataclasses
    own = dataclasses.replace(POSTS["main"], model="opus")
    assert launch.model_for({"fleet": {"model": "sonnet"}}, POSTS["main"]) == "sonnet"
    assert launch.model_for({"fleet": {"model": "sonnet"}}, POSTS["reviewer"]) == "sonnet"
    assert launch.model_for({"fleet": {"model": "sonnet"}}, own) == "opus"


def test_one_for_all_still_inherits():
    assert launch.model_for({"fleet": {"model": "one"}}, POSTS["main"]) == ""
    assert launch.model_for({}, POSTS["main"]) == ""
