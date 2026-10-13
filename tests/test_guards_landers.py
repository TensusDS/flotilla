"""Only a post that may land pushes to trunk (field finding of 2026-10-12: the README promised it, the push guard
asked for a receipt and for nobody's post)."""

import contextlib
import io
import json
import os
import shutil
import subprocess

import pytest

from flotilla import hooks
from flotilla.core.census import CensusUnavailable, Session
from flotilla.guards import push, rules
from flotilla.guards.push import callers as the_real_callers   # the suite's stand-in replaces the attribute
from guardkit import IDENTITY, first, git, onboarded, receipt

FLEET = {"sid-main": "main session 1", "sid-sender": "sender 1", "sid-reviewer": "review session 1",
         "sid-orchestrator": "orchestrator 1", "sid-mine": "my own session"}


def alive(monkeypatch, live):
    """These sessions are alive, and a call is made by the one whose id it carries."""
    monkeypatch.setattr("flotilla.guards.push.callers", lambda session_id="": (
        [live[session_id]] if session_id in live else [], list(live.values())))


def fleet(tmp_path, monkeypatch, live=FLEET, authorized="sender", named=""):
    """A direct-flow project with the posts on origin's trunk, a green push receipt over HEAD, and these sessions
    alive."""
    from flotilla.posts import install_templates
    monkeypatch.setenv("FLOTILLA_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("FLOTILLA_GATE_OVERRIDE", raising=False)
    root = onboarded(tmp_path, push=False, extra=f'\n[fleet]\nname = "{named}"\n' if named else "")
    text = (root / ".flotilla" / "project.toml").read_text(encoding="utf-8")
    (root / ".flotilla" / "project.toml").write_text(
        text.replace('mode = "direct"', f'mode = "direct"\nmerge_authorized_by = "{authorized}"'), encoding="utf-8")
    install_templates(root)
    git(root, "add", ".flotilla")
    git(root, *IDENTITY, "commit", "-q", "-m", "posts")
    git(root, "push", "-q", "origin", "main")
    receipt(root, tmp_path / "state")
    alive(monkeypatch, live)
    return root


def ask(root, command, session):
    payload = {"cwd": str(root), "tool_name": "Bash", "tool_input": {"command": command, "description": "d"},
               "permission_mode": "auto", "session_id": session}
    out = io.StringIO()
    assert hooks.run_hook("guard", io.StringIO(json.dumps(payload)), out=out) == 0
    return json.loads(out.getvalue())["hookSpecificOutput"] if out.getvalue() else {}


def denied(answer) -> str:
    return answer.get("permissionDecisionReason", "") if answer.get("permissionDecision") == "deny" else ""


def test_a_builders_push_to_trunk_is_refused_though_its_receipt_is_green(tmp_path, monkeypatch):
    root = fleet(tmp_path, monkeypatch)
    why = denied(ask(root, "git push origin HEAD:main", "sid-main"))
    assert "main session 1" in why and "may not land" in why
    assert "sender 1" in why and "work hand" in why          # where the work goes instead, and to whom


def test_a_reviewer_and_an_orchestrator_are_refused_the_same_way(tmp_path, monkeypatch):
    root = fleet(tmp_path, monkeypatch)
    for session in ("sid-reviewer", "sid-orchestrator"):
        assert "may not land" in denied(ask(root, "git push origin HEAD:main", session))


def test_a_post_that_may_not_hand_is_not_told_to_hand(tmp_path, monkeypatch):
    """Final review: the reviewer, the orchestrator, the helper and the judge have no `hand` move; a refusal that
    tells them to make it sends them into the ledger's refusal next."""
    root = fleet(tmp_path, monkeypatch)
    why = denied(ask(root, "git push origin HEAD:main", "sid-reviewer"))
    assert "flotilla work hand" not in why and "sender 1" in why
    assert "flotilla work hand" in denied(ask(root, "git push origin HEAD:main", "sid-main"))


def test_the_refusal_names_the_posts_that_land_not_a_fixed_one(tmp_path, monkeypatch):
    root = fleet(tmp_path, monkeypatch)
    why = denied(ask(root, "git push origin HEAD:main", "sid-main"))
    assert "`sender`" in why and "(the sender)" not in why


def test_the_sender_is_not_refused_by_its_post(tmp_path, monkeypatch):
    root = fleet(tmp_path, monkeypatch)
    assert not denied(ask(root, "git push origin HEAD:main", "sid-sender"))


def test_a_session_that_holds_no_post_pushes_as_before(tmp_path, monkeypatch):
    root = fleet(tmp_path, monkeypatch)
    assert not denied(ask(root, "git push origin HEAD:main", "sid-mine"))        # a name no post matches
    assert not denied(ask(root, "git push origin HEAD:main", "sid-unlisted"))    # a session the census does not name


def test_a_call_that_carries_no_session_id_is_still_asked_who_made_it(tmp_path, monkeypatch):
    """Review: without an id nobody's post was asked. The process's own parents say which seat it runs under."""
    root = fleet(tmp_path, monkeypatch)
    monkeypatch.setattr("flotilla.guards.push.callers",
                        lambda session_id="": (["main session 1"], list(FLEET.values())))
    assert "may not land" in denied(ask(root, "git push origin HEAD:main", ""))


def test_with_no_live_sender_the_seat_is_sent_to_the_orchestrator(tmp_path, monkeypatch):
    live = {key: name for key, name in FLEET.items() if key != "sid-sender"}
    root = fleet(tmp_path, monkeypatch, live=live)
    why = denied(ask(root, "git push origin HEAD:main", "sid-main"))
    assert "may not land" in why and "orchestrator 1" in why and "the person" in why
    assert "sender 1" not in why


def test_an_orchestrator_with_no_live_sender_is_told_to_tell_the_person(tmp_path, monkeypatch):
    live = {key: name for key, name in FLEET.items() if key != "sid-sender"}
    root = fleet(tmp_path, monkeypatch, live=live)
    why = denied(ask(root, "git push origin HEAD:main", "sid-orchestrator"))
    assert "tell the person" in why


def test_the_refusal_has_no_override(tmp_path, monkeypatch):
    root = fleet(tmp_path, monkeypatch)
    why = denied(ask(root, 'FLOTILLA_GATE_OVERRIDE="it is urgent" git push origin HEAD:main', "sid-main"))
    assert "may not land" in why


def test_a_push_whose_tree_cannot_be_told_is_refused_by_post_before_any_override(tmp_path, monkeypatch):
    """Final review, the blocker: what a door carries is worked out before the post is asked, and a door whose
    revisions could not be told fell to the receipt's refusal - which an override opens."""
    root = fleet(tmp_path, monkeypatch)
    for command in ('cd "$D" && git push origin HEAD:main',
                    'cd "$D" && FLOTILLA_GATE_OVERRIDE="x" git push origin HEAD:main'):
        why = denied(ask(root, command, "sid-main"))
        assert "may not land" in why and "FLOTILLA_GATE_OVERRIDE=" not in why.split("\n", 1)[1]


def test_a_tag_is_the_senders_to_push_too(tmp_path, monkeypatch):
    root = fleet(tmp_path, monkeypatch)
    git(root, *IDENTITY, "tag", "-a", "v9.9.9", "-m", "a release")
    assert "may not land" in denied(ask(root, "git push origin v9.9.9", "sid-main"))
    assert not denied(ask(root, "git push origin v9.9.9", "sid-sender"))


def test_a_builders_push_of_its_own_branch_is_untouched(tmp_path, monkeypatch):
    root = fleet(tmp_path, monkeypatch)
    git(root, "checkout", "-q", "-b", "feat/x")
    assert not denied(ask(root, "git push -u origin feat/x", "sid-main"))


def test_a_broad_flag_on_an_own_branch_push_is_named_in_the_refusal(tmp_path, monkeypatch):
    """`--follow-tags` and its kin may carry trunk or a tag, so they are the sender's - and the seat is told which
    word closed its push, not only that "trunk or a tag" was pushed."""
    root = fleet(tmp_path, monkeypatch)
    git(root, "checkout", "-q", "-b", "feat/x")
    why = denied(ask(root, "git push --follow-tags -u origin feat/x", "sid-main"))
    assert "may not land" in why and "`--follow-tags`" in why


def test_when_nobody_can_say_who_calls_a_landing_push_is_closed(tmp_path, monkeypatch):
    root = fleet(tmp_path, monkeypatch)

    def unasked(session_id=""):
        raise RuntimeError("claude agents did not answer")

    monkeypatch.setattr("flotilla.guards.push.callers", unasked)
    why = denied(ask(root, "git push origin HEAD:main", "sid-main"))
    assert "who is calling could not be asked" in why and "did not answer" in why


def test_a_project_without_posts_has_no_such_rule(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOTILLA_STATE_DIR", str(tmp_path / "state"))
    root = onboarded(tmp_path)
    receipt(root, tmp_path / "state")

    def never(session_id=""):
        raise AssertionError("no post lands here, so nobody is asked who calls")

    monkeypatch.setattr("flotilla.guards.push.callers", never)
    assert not denied(ask(root, "git push origin HEAD:main", "sid-main"))


def test_the_posts_are_origins_trunks_and_never_the_trees(tmp_path, monkeypatch):
    """Decision 236: a seat edits its tree. Neither granting itself the move there nor deleting the posts opens it."""
    root = fleet(tmp_path, monkeypatch)
    post = root / ".flotilla" / "posts" / "main.md"
    post.write_text(post.read_text(encoding="utf-8").replace("may: [reserve,", "may: [land, reserve,"),
                    encoding="utf-8")
    assert "may: [land," in post.read_text(encoding="utf-8")
    assert "may not land" in denied(ask(root, "git push origin HEAD:main", "sid-main"))
    shutil.rmtree(root / ".flotilla" / "posts")
    assert "may not land" in denied(ask(root, "git push origin HEAD:main", "sid-main"))


def test_a_named_fleet_judges_its_own_seats_and_names_only_them(tmp_path, monkeypatch):
    live = {"sid-main": "acme-main session 1", "sid-sender": "acme-sender 1", "sid-theirs": "main session 1",
            "sid-their-sender": "other-sender 1", "sid-bare-sender": "sender 1"}
    root = fleet(tmp_path, monkeypatch, live=live, named="acme")
    why = denied(ask(root, "git push origin HEAD:main", "sid-main"))
    assert "may not land" in why and "acme-sender 1" in why
    assert "other-sender 1" not in why and " sender 1" not in why.replace("acme-sender 1", "")
    assert not denied(ask(root, "git push origin HEAD:main", "sid-theirs"))   # a name that is no seat of this fleet


def test_a_caller_whose_post_cannot_be_told_is_closed_and_no_override_opens_it(tmp_path, monkeypatch):
    """Security review: a failure inside the question itself fell to the receipt's refusal, which an override
    opens. Who may land is not guessed, and not waived."""
    from flotilla.posts import PostError
    root = fleet(tmp_path, monkeypatch)

    def two_posts(posts, name):
        raise PostError(f"`{name}` matches more than one post: main, minor")

    monkeypatch.setattr("flotilla.posts.post_for_session", two_posts)
    for command in ("git push origin HEAD:main", 'FLOTILLA_GATE_OVERRIDE="x" git push origin HEAD:main'):
        why = denied(ask(root, command, "sid-main"))
        assert "who may land could not be told" in why and "more than one post" in why


# ---- pull requests: `gh pr merge` has no second barrier

def env(tmp_path):
    return {"FLOTILLA_STATE_DIR": str(tmp_path / "state")}


def gh_answers(head):
    def run(cmd, **kwargs):
        if cmd[0] == "gh":
            return subprocess.CompletedProcess(cmd, 0, head + "\n", "")
        return subprocess.run(cmd, **kwargs)
    return run


def judge(command, root, tmp_path, caller, run, cwd=None):
    profile, _ = rules.rules_for(root)
    return push.guard(first(command, cwd or root), root=root, profile=profile, env=env(tmp_path), run=run,
                      who=lambda: ([caller] if caller else [], list(FLEET.values())))


def test_a_merge_is_refused_by_post_pinned_or_not_and_no_override_opens_it(tmp_path, monkeypatch):
    """Final review, the blocker: `gh pr merge 5` could not be pinned to a revision, so it fell to the receipt's
    refusal before the post was asked - and that refusal names the override that opens it."""
    root = fleet(tmp_path, monkeypatch)
    head = git(root, "rev-parse", "HEAD")
    for command in ("gh pr merge 5", "gh pr merge 5 --auto --squash", 'FLOTILLA_GATE_OVERRIDE="x" gh pr merge 5',
                    f"gh pr merge 5 --match-head-commit {head}"):
        found = judge(command, root, tmp_path, "main session 1", gh_answers(head))
        assert found is not None and found.refuse, command
        assert "may not land" in found.text and 'FLOTILLA_GATE_OVERRIDE="<why>"' not in found.text, command


def test_the_senders_merge_is_judged_by_its_receipt_as_before(tmp_path, monkeypatch):
    root = fleet(tmp_path, monkeypatch)
    head = git(root, "rev-parse", "HEAD")
    assert judge(f"gh pr merge 5 --match-head-commit {head}", root, tmp_path, "sender 1", gh_answers(head)) is None
    unpinned = judge("gh pr merge 5", root, tmp_path, "sender 1", gh_answers(head))
    assert unpinned.refuse and "--match-head-commit" in unpinned.text and "may not land" not in unpinned.text
    assert not judge('FLOTILLA_GATE_OVERRIDE="x" gh pr merge 5', root, tmp_path, "sender 1", gh_answers(head)).refuse


def test_an_unpinned_merge_cannot_waive_the_persons_approval(tmp_path, monkeypatch):
    """Final review, same root: where the person authorizes merges, an override on a merge whose head could not
    be pinned was recorded and the merge ran - the approval was never asked (decision 188: it has no override)."""
    root = fleet(tmp_path, monkeypatch, authorized="human")
    head = git(root, "rev-parse", "HEAD")
    found = judge('FLOTILLA_GATE_OVERRIDE="x" gh pr merge 5', root, tmp_path, "sender 1", gh_answers(head))
    assert found.refuse and "approved" in found.text and "--match-head-commit" in found.text


@pytest.mark.parametrize("command", ["gh pr merge 5 --squash -b --help", "gh pr merge 5 --body --dry-run",
                                     "gh pr merge 5 -t -h --squash"])
def test_a_harmless_word_that_is_an_options_value_frees_no_merge(command, tmp_path):
    assert push.door(first(command, tmp_path)) is not None
    assert push.door(first("gh pr merge 5 --help", tmp_path)) is None


@pytest.mark.parametrize("command", ["gh pr merge 5 -sb --help", "gh pr merge 5 -R --help", "gh pr merge 5 -st -h",
                                     "git push -o -n origin main", "git push -fo --dry-run origin main",
                                     "git push --receive-pack -h origin main", "git push --push-o -n origin main",
                                     "git push --exe -n origin main", "git push origin main -- -n",
                                     "git push -n --no-dry-run origin main", "git push --dry-run --no-dry origin main",
                                     "git push -d --no-delete origin main",
                                     "gh pr create --title --dry-run --body x", "gh pr create -t --help",
                                     "gh pr create --fill -B --help", "gh workflow run ci.yml --ref --help"])
def test_a_harmless_word_after_a_flag_that_takes_a_value_frees_nothing(command, tmp_path):
    """Security review: gh and git read `-sb --help` as a body of "--help"; a guard that reads it as a call for
    help lets the merge through unjudged."""
    assert push.door(first(command, tmp_path)) is not None


@pytest.mark.parametrize("command", ["gh pr merge 5 --help", "gh pr merge --help", "gh pr merge 5 --squash -h",
                                     "git push -n origin main", "git push origin main --dry-run",
                                     "gh pr create --fill --dry-run", "gh workflow run --help"])
def test_a_call_for_help_or_a_dry_run_is_still_no_door(command, tmp_path):
    assert push.door(first(command, tmp_path)) is None


def breaks_on(*words, error=RuntimeError("git fell over")):
    def run(cmd, **kwargs):
        if cmd[0] == "git" and all(any(word in part for part in cmd) for word in words):
            raise error
        return subprocess.run(cmd, **kwargs)
    return run


def test_a_guard_that_fell_before_the_post_was_asked_is_not_opened_by_an_override(tmp_path, monkeypatch):
    """Security review: only a door whose revisions could not be *told* was judged as landing; one where the guard
    itself fell - a git that timed out - still went to the receipt's refusal, and an override opened it."""
    root = fleet(tmp_path, monkeypatch)
    slow = breaks_on("rev-parse", "HEAD^{commit}", error=subprocess.TimeoutExpired("git", 10))
    for command in ("git push origin HEAD:main", 'FLOTILLA_GATE_OVERRIDE="x" git push origin HEAD:main'):
        found = judge(command, root, tmp_path, "main session 1", slow)
        assert found is not None and found.refuse and "may not land" in found.text, command
    waived = judge('FLOTILLA_GATE_OVERRIDE="x" git push origin HEAD:main', root, tmp_path, "sender 1", slow)
    assert waived is not None and not waived.refuse      # the sender's own override of a guard that fell, as before


def test_rules_that_fell_while_being_read_close_an_untold_door_without_override(tmp_path, monkeypatch):
    root = fleet(tmp_path, monkeypatch)
    head = git(root, "rev-parse", "HEAD")

    def run(cmd, **kwargs):
        if cmd[0] == "gh":
            return subprocess.CompletedProcess(cmd, 0, head + "\n", "")
        return breaks_on("show", ".flotilla/project.toml")(cmd, **kwargs)

    found = judge('FLOTILLA_GATE_OVERRIDE="x" gh pr merge 5', root, tmp_path, "main session 1", run)
    assert found is not None and found.refuse and "override recorded" not in found.text


@pytest.mark.parametrize("command", ["gh pr merge https://github.com/o/r/pull/5 --squash",
                                     "gh pr merge --squash -- https://github.com/o/r/pull/5",
                                     "gh pr merge HTTPS://GitHub.com/o/r/pull/5", "gh pr merge 5 -sR o/r",
                                     "gh pr merge 5 -Ro/r --squash", "gh pr merge 5 --repo=o/r"])
def test_a_merge_that_names_its_repository_from_outside_any_project_is_not_let_through_unjudged(command, tmp_path):
    """However it names it - as gh reads the words, not as they look (security reviews of 236)."""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    found = push.guard(first(command, elsewhere), root=None, profile={}, env=env(tmp_path))
    assert found is not None and found.refuse and "names a repository" in found.text


@pytest.mark.parametrize("command", [
    'gh pr create --title x --body "see https://example.com/issue/1"',
    'gh pr create --title fix --body "Generated with [Claude Code](https://claude.com/claude-code)"',
    'gh pr merge 5 --squash --body "closes https://example.com/issue/1"',
    "gh pr merge 5 -sb https://github.com/o/r/pull/5",
    "gh workflow run ci.yml -f target=https://staging.example.com"])
def test_a_url_that_is_not_the_pull_request_named_leaves_a_command_outside_any_project_alone(command, tmp_path):
    """Second review, the blocker of the first fix: any word holding `://` was read as naming a repository, so a
    pull request whose body carries a link was refused in every project that never onboarded."""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    assert push.guard(first(command, elsewhere), root=None, profile={}, env=env(tmp_path)) is None


def test_a_merge_aimed_from_outside_every_project_is_asked_of_the_seats_own(tmp_path, monkeypatch):
    """Second review: a merge has no second barrier, and one whose project cannot be told (-R, a URL, from another
    directory) fell to the overridable refusal. A seat is asked by the project it stands in."""
    root = fleet(tmp_path, monkeypatch)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    for merge in ("gh pr merge https://github.com/o/r/pull/5 --squash", "gh pr merge 5 -R o/r --squash",
                  "GH_REPO=o/r gh pr merge 5 --squash"):
        for command in (f"cd {elsewhere} && {merge}", f'cd {elsewhere} && FLOTILLA_GATE_OVERRIDE="x" {merge}'):
            assert "may not land" in denied(ask(root, command, "sid-main")), command
        assert not denied(ask(root, f'cd {elsewhere} && FLOTILLA_GATE_OVERRIDE="x" {merge}', "sid-sender")), merge
        assert "names a repository" in denied(ask(root, f"cd {elsewhere} && {merge}", "sid-sender")), merge


def test_a_seats_merge_from_outside_every_project_is_asked_for_its_post_however_it_is_spelt(tmp_path, monkeypatch):
    """Third reading: only some spellings of "another repository" were recognised; the rest went unjudged. A merge
    has no second barrier, so a seat's merge from a directory in no project is asked of the project it stands in."""
    root = fleet(tmp_path, monkeypatch)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    for merge in ("gh pr merge 5 --squash", 'gh pr merge "$PR_URL" --squash',
                  "export GH_REPO=o/r && gh pr merge 5 --squash"):
        assert "may not land" in denied(ask(root, f"cd {elsewhere} && {merge}", "sid-main")), merge
        assert not denied(ask(root, f"cd {elsewhere} && {merge}", "sid-sender")), merge
        assert not denied(ask(root, f"cd {elsewhere} && {merge}", "sid-mine")), merge


def test_a_merge_in_an_unrelated_repository_is_not_asked_for_this_projects_approval(tmp_path, monkeypatch):
    """Third reading: asked of the session's own project, a merge elsewhere is asked for the post alone - the
    approval there is another project's matter, and asking it closed every such merge with no way through."""
    root = fleet(tmp_path, monkeypatch, authorized="human")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    command = f'cd {elsewhere} && FLOTILLA_GATE_OVERRIDE="x" gh pr merge 7 -R someone/unrelated --squash'
    assert not denied(ask(root, command, "sid-mine"))
    assert not denied(ask(root, command, "sid-sender"))
    assert "may not land" in denied(ask(root, command, "sid-main"))


def test_a_directory_with_rules_of_its_own_that_cannot_be_read_opens_nothing(tmp_path, monkeypatch):
    """Second review: a seat can write a `.flotilla/project.toml` into a subdirectory; reading it fell the guard
    before the post was asked, and an override opened the push."""
    root = fleet(tmp_path, monkeypatch)
    (root / "scratch" / ".flotilla").mkdir(parents=True)
    (root / "scratch" / ".flotilla" / "project.toml").write_text("not toml [", encoding="utf-8")
    for command in ("cd scratch && git push origin HEAD:main",
                    'cd scratch && FLOTILLA_GATE_OVERRIDE="x" git push origin HEAD:main'):
        assert "may not land" in denied(ask(root, command, "sid-main")), command


def test_an_own_branch_push_whose_tree_cannot_be_told_says_how_to_make_it_told(tmp_path, monkeypatch):
    """Second review: the post's refusal replaced the line that tells the seat what to do - name the tree."""
    root = fleet(tmp_path, monkeypatch)
    git(root, "checkout", "-q", "-b", "feat/x")
    why = denied(ask(root, 'cd "$WT" && git push -u origin feat/x', "sid-main"))
    assert "may not land" in why and "git -C <path>" in why


def test_a_seat_that_stands_in_no_project_is_asked_by_the_project_it_pushes(tmp_path, monkeypatch):
    root = fleet(tmp_path, monkeypatch)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    assert "may not land" in denied(ask(elsewhere, f"git -C {root} push origin HEAD:main", "sid-main"))


def test_a_tree_whose_own_profile_cannot_be_read_still_asks_who_lands(tmp_path, monkeypatch):
    root = fleet(tmp_path, monkeypatch)
    (root / ".flotilla" / "project.toml").write_text("not toml [", encoding="utf-8")
    assert "may not land" in denied(ask(root, "git push origin HEAD:main", "sid-main"))


def test_a_push_whose_tree_cannot_be_told_cannot_waive_the_persons_approval(tmp_path, monkeypatch):
    """Second review: the merge was closed, the push from a tree named through a variable was not."""
    root = fleet(tmp_path, monkeypatch, authorized="human")
    why = denied(ask(root, 'cd "$D" && FLOTILLA_GATE_OVERRIDE="x" git push origin HEAD:main', "sid-sender"))
    assert "approved" in why and "git -C <path>" in why


def test_a_receipt_check_that_fell_does_not_let_an_override_stand_in_for_the_approval(tmp_path, monkeypatch):
    """Security review: the approval was asked after the receipts, inside the same `try` - a receipt check that
    fell skipped it, and the override that waives a fallen guard then waived the approval with it."""
    root = fleet(tmp_path, monkeypatch, authorized="human")
    (root / "w.txt").write_text("work nobody approved\n", encoding="utf-8")
    git(root, "add", "w.txt")
    git(root, *IDENTITY, "commit", "-q", "-m", "work nobody approved")

    def fell(*args, **kwargs):
        raise RuntimeError("the receipts could not be read")

    monkeypatch.setattr("flotilla.guards.push._failures", fell)
    found = judge('FLOTILLA_GATE_OVERRIDE="x" git push origin HEAD:main', root, tmp_path, "sender 1", subprocess.run)
    assert found is not None and found.refuse and "approved" in found.text


def test_a_post_on_trunk_that_cannot_be_read_closes_the_push_and_names_its_file(tmp_path, monkeypatch):
    root = fleet(tmp_path, monkeypatch)
    post = root / ".flotilla" / "posts" / "minor.md"
    post.write_text(post.read_text(encoding="utf-8").replace("may: [", "may: [fly, "), encoding="utf-8")
    git(root, "add", ".flotilla")
    git(root, *IDENTITY, "commit", "-q", "-m", "a post that names a move nobody knows")
    git(root, "push", "-q", "origin", "main")
    why = denied(ask(root, "git push origin HEAD:main", "sid-mine"))
    assert "who may land could not be told" in why
    assert ".flotilla/posts/minor.md" in why and "flotilla-posts-" not in why   # trunk's file, not a temp copy


def test_posts_that_cannot_be_listed_close_the_push_rather_than_switch_the_rule_off(tmp_path, monkeypatch):
    root = fleet(tmp_path, monkeypatch)

    def no_listing(cmd, **kwargs):
        if cmd[0] == "git" and "ls-tree" in cmd and any(".flotilla/posts" in word for word in cmd):
            return subprocess.CompletedProcess(cmd, 128, "", "fatal: not a tree object")
        return subprocess.run(cmd, **kwargs)

    found = judge("git push origin HEAD:main", root, tmp_path, "main session 1", no_listing)
    assert found is not None and found.refuse and "who may land could not be told" in found.text


# ---- the second barrier: git's own hook

def pre_push(root, tmp_path, lines=None, more=None):
    from flotilla.guards.commands import run_githook
    head = git(root, "rev-parse", "HEAD")
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        code = run_githook("pre-push", root, lines or f"refs/heads/main {head} refs/heads/main {'0' * 40}\n",
                           env={"FLOTILLA_STATE_DIR": str(tmp_path / "state"), **(more or {})},
                           hook_args=("origin", ""))
    return code, err.getvalue()


def under(monkeypatch, seat):
    monkeypatch.setattr("flotilla.guards.push.callers",
                        lambda session_id="": ([seat] if seat else [], list(FLEET.values())))


@pytest.mark.parametrize("seat,code", [("main session 1", 1), ("sender 1", 0), ("", 0)])
def test_the_pre_push_hook_asks_the_same_of_a_push_hidden_in_a_script(tmp_path, monkeypatch, seat, code):
    """`seat` is the background seat the push runs under; "" is a person's terminal or their own session."""
    root = fleet(tmp_path, monkeypatch)
    under(monkeypatch, seat)
    got, said = pre_push(root, tmp_path)
    assert got == code and (("may not land" in said) is (code == 1))


def test_the_pre_push_hook_keeps_a_tag_for_the_sender_too(tmp_path, monkeypatch):
    root = fleet(tmp_path, monkeypatch)
    git(root, *IDENTITY, "tag", "-a", "v9.9.9", "-m", "a release")
    tag = f"refs/tags/v9.9.9 {git(root, 'rev-parse', 'v9.9.9')} refs/tags/v9.9.9 {'0' * 40}\n"
    under(monkeypatch, "main session 1")
    got, said = pre_push(root, tmp_path, lines=tag)
    assert got == 1 and "may not land" in said
    under(monkeypatch, "sender 1")
    assert pre_push(root, tmp_path, lines=tag)[0] == 0


def test_the_pre_push_hook_takes_no_override_for_the_post(tmp_path, monkeypatch):
    root = fleet(tmp_path, monkeypatch)
    under(monkeypatch, "main session 1")
    got, said = pre_push(root, tmp_path, more={"FLOTILLA_GATE_OVERRIDE": "it is urgent"})
    assert got == 1 and "may not land" in said and "override recorded" not in said


def test_the_pre_push_hook_closes_when_nobody_can_say_who_pushes_and_names_the_persons_way(tmp_path, monkeypatch):
    """Final review: at a terminal, "push from a terminal" is no way out. The person's is `--no-verify`."""
    root = fleet(tmp_path, monkeypatch)

    def unasked(session_id=""):
        raise CensusUnavailable("`claude` is not on PATH")

    monkeypatch.setattr("flotilla.guards.push.callers", unasked)
    got, said = pre_push(root, tmp_path)
    assert got == 1 and "who is calling could not be asked" in said and "--no-verify" in said


# ---- who calls: the session by its id, and every seat the process runs under

def session(name, sid, kind="background", pid=None):
    return Session(name=name, session_id=sid, kind=kind, pid=pid, short_id=None, status="running", state=None,
                   cwd="", started_at_ms=None)


def test_a_call_answers_to_its_own_session_and_to_every_seat_above_it():
    seat, inner = session("main session 1", "a", pid=10), session("", "b", pid=20)
    names, live = push.identities([seat, inner, session("sender 1", "c")], "b", chain=[inner, seat])
    assert names == ["main session 1"]      # a session a seat started inside itself is that seat's (review, 3 and 7)
    assert live == ["main session 1", "sender 1"]
    assert push.identities([seat], "a", chain=[seat])[0] == ["main session 1"]
    assert push.identities([seat], "", chain=[seat])[0] == ["main session 1"]
    assert push.identities([seat], "gone", chain=[])[0] == []


def test_a_seat_is_not_hidden_by_a_name_its_inner_session_took():
    seat, inner = session("main session 1", "a", pid=10), session("sender 9", "b", pid=20)
    assert push.identities([seat, inner], "b", chain=[inner, seat])[0] == ["sender 9", "main session 1"]


def test_the_persons_own_session_above_is_named_only_by_its_id():
    """Their `!` and their terminal stay theirs, even where their session is named like a seat (the leading one)."""
    lead = session("orchestrator 1", "a", kind="interactive", pid=10)
    assert push.identities([lead], "", chain=[lead])[0] == []
    assert push.identities([lead], "a", chain=[lead])[0] == ["orchestrator 1"]


def test_who_calls_is_asked_of_the_census_and_of_the_process_parents(monkeypatch):
    seat = session("main session 1", "a", pid=4242)
    monkeypatch.setattr("flotilla.core.census.read_census", lambda **kwargs: [seat, session("sender 1", "c", pid=7)])
    monkeypatch.setattr("flotilla.core.platform.parent_pid",
                        lambda pid, source=None: 4242 if pid == os.getpid() else None)
    assert the_real_callers("") == (["main session 1"], ["main session 1", "sender 1"])
    assert the_real_callers("c")[0] == ["sender 1", "main session 1"]


def test_who_calls_is_not_guessed_where_the_census_cannot_be_asked(monkeypatch):
    def down(**kwargs):
        raise CensusUnavailable("`claude` is not on PATH")

    monkeypatch.setattr("flotilla.core.census.read_census", down)
    with pytest.raises(CensusUnavailable):
        the_real_callers("a")


def test_a_seat_that_may_not_land_is_refused_under_any_name_it_answers_to(tmp_path, monkeypatch):
    root = fleet(tmp_path, monkeypatch)
    monkeypatch.setattr("flotilla.guards.push.callers",
                        lambda session_id="": (["sender 9", "main session 1"], list(FLEET.values())))
    assert "main session 1" in denied(ask(root, "git push origin HEAD:main", "sid-inner"))


def test_the_dry_run_command_says_what_the_hook_would(tmp_path, monkeypatch, capsys):
    """Final review: `flotilla guard check` passed no caller, so it told a seat its push was allowed."""
    from flotilla import cli
    root = fleet(tmp_path, monkeypatch)
    under(monkeypatch, "main session 1")
    assert cli.main(["guard", "check", "git push origin HEAD:main", "--cwd", str(root)]) == 1
    assert "may not land" in capsys.readouterr().out
