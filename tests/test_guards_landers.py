"""Only a post that may land pushes to trunk (field finding of 2026-10-12: the README promised it, the push guard
asked for a receipt and for nobody's post)."""

import contextlib
import io
import json

import pytest

from flotilla import hooks
from guardkit import IDENTITY, git, onboarded, receipt

FLEET = {"sid-main": "main session 1", "sid-sender": "sender 1", "sid-reviewer": "review session 1",
         "sid-orchestrator": "orchestrator 1", "sid-mine": "my own session"}


def fleet(tmp_path, monkeypatch, live=FLEET):
    """A direct-flow project with the posts on origin's trunk, a green push receipt over HEAD, and these sessions
    alive."""
    from flotilla.posts import install_templates
    monkeypatch.setenv("FLOTILLA_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("FLOTILLA_GATE_OVERRIDE", raising=False)
    root = onboarded(tmp_path, push=False)
    text = (root / ".flotilla" / "project.toml").read_text(encoding="utf-8")
    (root / ".flotilla" / "project.toml").write_text(
        text.replace('mode = "direct"', 'mode = "direct"\nmerge_authorized_by = "sender"'), encoding="utf-8")
    install_templates(root)
    git(root, "add", ".flotilla")
    git(root, *IDENTITY, "commit", "-q", "-m", "posts")
    git(root, "push", "-q", "origin", "main")
    receipt(root, tmp_path / "state")
    monkeypatch.setattr("flotilla.guards.run._calling",
                        lambda session_id: (live.get(session_id, ""), list(live.values())))
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


def test_the_sender_is_not_refused_by_its_post(tmp_path, monkeypatch):
    root = fleet(tmp_path, monkeypatch)
    assert not denied(ask(root, "git push origin HEAD:main", "sid-sender"))


def test_a_session_that_holds_no_post_pushes_as_before(tmp_path, monkeypatch):
    root = fleet(tmp_path, monkeypatch)
    assert not denied(ask(root, "git push origin HEAD:main", "sid-mine"))        # a name no post matches
    assert not denied(ask(root, "git push origin HEAD:main", "sid-unlisted"))    # a session the census does not name


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


def test_a_tag_is_the_senders_to_push_too(tmp_path, monkeypatch):
    root = fleet(tmp_path, monkeypatch)
    git(root, *IDENTITY, "tag", "-a", "v9.9.9", "-m", "a release")
    assert "may not land" in denied(ask(root, "git push origin v9.9.9", "sid-main"))
    assert not denied(ask(root, "git push origin v9.9.9", "sid-sender"))


def test_a_builders_push_of_its_own_branch_is_untouched(tmp_path, monkeypatch):
    root = fleet(tmp_path, monkeypatch)
    git(root, "checkout", "-q", "-b", "feat/x")
    assert not denied(ask(root, "git push -u origin feat/x", "sid-main"))


def test_when_nobody_can_say_who_calls_a_landing_push_is_closed(tmp_path, monkeypatch):
    root = fleet(tmp_path, monkeypatch)

    def unasked(session_id):
        raise RuntimeError("claude agents did not answer")

    monkeypatch.setattr("flotilla.guards.run._calling", unasked)
    why = denied(ask(root, "git push origin HEAD:main", "sid-main"))
    assert "who is calling could not be asked" in why and "did not answer" in why


def test_a_project_without_posts_has_no_such_rule(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOTILLA_STATE_DIR", str(tmp_path / "state"))
    root = onboarded(tmp_path)
    receipt(root, tmp_path / "state")

    def never(session_id):
        raise AssertionError("no post lands here, so nobody is asked who calls")

    monkeypatch.setattr("flotilla.guards.run._calling", never)
    assert not denied(ask(root, "git push origin HEAD:main", "sid-main"))


def pre_push(root, tmp_path):
    from flotilla.guards.commands import run_githook
    head = git(root, "rev-parse", "HEAD")
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        code = run_githook("pre-push", root, f"refs/heads/main {head} refs/heads/main {'0' * 40}\n",
                           env={"FLOTILLA_STATE_DIR": str(tmp_path / "state")}, hook_args=("origin", ""))
    return code, err.getvalue()


@pytest.mark.parametrize("under,code", [("main session 1", 1), ("sender 1", 0), ("", 0)])
def test_the_pre_push_hook_asks_the_same_of_a_push_hidden_in_a_script(tmp_path, monkeypatch, under, code):
    """`under` is the background seat the push runs under; "" is a person's terminal or their own session."""
    root = fleet(tmp_path, monkeypatch)
    monkeypatch.setattr("flotilla.guards.push.seat_above", lambda: (under, list(FLEET.values())))
    got, said = pre_push(root, tmp_path)
    assert got == code and (("may not land" in said) is (code == 1))
