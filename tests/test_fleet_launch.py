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
    given = json.loads(argv[argv.index("--settings") + 1])
    assert given["enabledPlugins"] == {'odd"id@m': False, "pdf@m": False} and given["permissions"]["allow"]
    assert argv.index("--settings") < argv.index("--append-system-prompt") and argv[-1] == launch.FIRST_PROMPT


def test_without_plugin_settings_the_settings_carry_the_seats_permissions_only():
    import json
    seat = launch.seat_for(MAIN, POSTS["reviewer"], "review session 3")
    settings = json.loads((argv := launch.argv(seat, POSTS["reviewer"], {}, main=MAIN))[argv.index("--settings") + 1])
    assert set(settings) == {"permissions"} and settings["permissions"]["allow"]


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


def _rules(post, name, profile=None):
    seat = launch.seat_for(MAIN, POSTS[post], name)
    return launch.own_allow(seat, POSTS[post], profile or {"trunk": {"branch": "main"}})


def test_a_seat_may_run_flotillas_own_moves_without_the_classifier():
    """Twosuns field test of 0.6.7, W11: in auto mode the classifier refused `work land` - a ledger record - as
    "Merge Without Review", and the batch sat half recorded. Each seat is launched with an allow rule for flotilla's
    own command line; flotilla's hooks still refuse what they refuse (measured on Claude Code 2.1.288: a hook's deny
    wins over an allow rule)."""
    rules = _rules("reviewer", "review session 3")
    cli = str(launch.CLI)
    assert f"Bash({cli} work accept *)" in rules and f"Bash({cli} status)" in rules
    assert f"Bash({cli} receipt *)" in rules and f"Bash({cli} lane *)" in rules
    assert f"Bash({cli} work reconcile)" in rules


def test_no_seat_is_allowed_the_persons_moves_or_raising_and_removing_seats():
    cli = str(launch.CLI)
    for post, name in (("reviewer", "review session 3"), ("sender", "sender 1"), ("orchestrator", "orchestrator 1")):
        for rule in _rules(post, name):
            body = rule[len("Bash("):-1]
            for forbidden in ("work approve", "spawn", "retire", "fleet down", "fleet clean", "onboard", "guard",
                              "permit answer"):
                covered = body.endswith(" *") and f"{cli} {forbidden}".startswith(body[:-2] + " ") or \
                    body == f"{cli} {forbidden}" or body.startswith(f"{cli} {forbidden}")
                assert not covered, (post, rule, forbidden)


def test_only_the_sender_may_push_trunk_and_only_from_its_tree():
    """Twosuns field test of 0.6.7, W9: the classifier refused the sender's push of reviewed work as "Merge Without
    Review"; flotilla's push guard - a hook, which the allow rule does not pass - still asks for a receipt."""
    sender = _rules("sender", "sender 1", {"trunk": {"branch": "trunk"}})
    tree = launch.seat_for(MAIN, POSTS["sender"], "sender 1").tree
    assert "Bash(git push origin HEAD:trunk)" in sender and f"Bash(git -C {tree} push origin HEAD:trunk)" in sender
    for post, name in (("reviewer", "review session 3"), ("main", "main session 2"), ("orchestrator", "orchestrator 1")):
        assert not [rule for rule in _rules(post, name) if "git push" in rule], post


def test_the_allowed_moves_are_the_clis_moves_but_the_persons():
    from flotilla import cli
    parser = cli.build_parser()
    sub = next(a for a in parser._actions if a.__class__.__name__ == "_SubParsersAction").choices["work"]
    moves = set(next(a for a in sub._actions if a.__class__.__name__ == "_SubParsersAction").choices)
    assert set(launch.OWN_WORK_MOVES) == moves - {"approve"}
