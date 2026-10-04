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


def test_the_argv_carries_the_plugin_settings_as_one_element():
    import json
    seat = launch.seat_for(MAIN, POSTS["reviewer"], "review session 3")
    settings = json.dumps({"enabledPlugins": {'odd"id@m': False, "pdf@m": False}})
    argv = launch.argv(seat, POSTS["reviewer"], {"permissions": {"mode": "auto"}}, main=MAIN,
                       settings_json=settings)
    assert argv[argv.index("--settings") + 1] == settings
    assert json.loads(argv[argv.index("--settings") + 1]) == {"enabledPlugins": {'odd"id@m': False, "pdf@m": False}}
    assert argv.index("--settings") < argv.index("--append-system-prompt") and argv[-1] == launch.FIRST_PROMPT


def test_no_plugin_settings_means_no_settings_flag():
    seat = launch.seat_for(MAIN, POSTS["reviewer"], "review session 3")
    assert "--settings" not in launch.argv(seat, POSTS["reviewer"], {}, main=MAIN)
    assert "--settings" not in launch.argv(seat, POSTS["reviewer"], {}, main=MAIN, settings_json="")


def test_a_post_may_narrow_the_profiles_permission_mode_never_widen_it():
    """A post's mode came from a file a session can edit; the profile allows ask, rules or auto, and a post's mode
    went past it - bypassPermissions included (security review F6)."""
    import dataclasses
    main = POSTS["main"]
    for mode in ("acceptEdits", "auto"):
        with pytest.raises(launch.LaunchError, match="wider than the profile"):
            launch.permission_mode({"permissions": {"mode": "ask"}}, dataclasses.replace(main, permission_mode=mode))
    with pytest.raises(launch.LaunchError, match="never"):
        launch.permission_mode({"permissions": {"mode": "auto"}},
                               dataclasses.replace(main, permission_mode="bypassPermissions"))
    assert launch.permission_mode({"permissions": {"mode": "auto"}},
                                  dataclasses.replace(main, permission_mode="acceptEdits")) == "acceptEdits"
    assert launch.permission_mode({"permissions": {"mode": "ask"}},
                                  dataclasses.replace(main, permission_mode="plan")) == "plan"


def test_a_posts_default_mode_is_the_asking_mode():
    import dataclasses
    post = dataclasses.replace(POSTS["main"], permission_mode="default")
    assert launch.permission_mode({"permissions": {"mode": "ask"}}, post) == "default"


def post_names():
    from flotilla.posts import TEMPLATE_DIR
    return sorted(path.stem for path in TEMPLATE_DIR.glob("*.md"))


@pytest.mark.parametrize("post_name", post_names())
def test_every_seat_is_told_to_hand_the_person_a_refusal_and_a_safe_proposal(tmp_path, post_name):
    """A refusal only the person can resolve came back as "it was refused" (twosuns update to 0.7.1); what replaces
    it is the refusal's text and a proposal the person can run - never one that skips a check, disables a guard or
    widens a permission, and never an approve (review of 0.7.12)."""
    from types import SimpleNamespace
    seat = launch.Seat(post_name, f"{post_name} session 1", 1, tmp_path / "tree", f"fleet/{post_name}-1")
    post = SimpleNamespace(name=post_name, may=["claim"] if post_name in ("main", "minor") else [], body="Body.")
    text = launch.system_prompt(seat, post, main=tmp_path)
    assert "the refusal's text verbatim and your proposal" in text and "ready to paste with `!`" in text
    assert "the command that checks it worked; never only that it was refused" in text
    assert "Never propose skipping or overriding a check (`--no-verify`, an override variable)" in text
    assert "An approve is never yours to propose" in text and "never worked around" in text
    assert "the orchestrator: the person" in text and "a helper: the session that raised it" in text
