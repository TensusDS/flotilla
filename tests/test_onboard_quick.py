"""Quick onboarding: the recommended answer to every question that applies, one yes from the person (the person's
request, 2026-10-01: the questionnaire stays for those who want to tune, the rest onboard in one step)."""

import json
import subprocess

import pytest

from flotilla.onboard.detect import detect
from flotilla.onboard.questions import AnswerError, all_questions, next_questions, quick_answers, \
    suggest_composition, validate_answer
from test_onboard_cli import run_cli


@pytest.fixture()
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOTILLA_STATE_DIR", str(tmp_path / "state"))
    for key, value in (("GIT_AUTHOR_NAME", "t"), ("GIT_AUTHOR_EMAIL", "t@example.invalid"),
                       ("GIT_COMMITTER_NAME", "t"), ("GIT_COMMITTER_EMAIL", "t@example.invalid")):
        monkeypatch.setenv(key, value)
    root = tmp_path / "app"
    root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    (root / "package.json").write_text(json.dumps({"name": "app", "scripts": {"test": "vitest run"}}),
                                       encoding="utf-8")
    subprocess.run(["git", "add", "package.json"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "a project with a history"], cwd=root, check=True)
    subprocess.run(["git", "init", "-q", "--bare", str(tmp_path / "origin.git")], check=True)
    subprocess.run(["git", "remote", "add", "origin", str(tmp_path / "origin.git")], cwd=root, check=True)
    return root


def test_quick_answers_every_question_that_applies_with_its_recommendation(project):
    det = detect(project)
    answers = quick_answers(det, {})
    assert next_questions(det, answers) == []
    assert answers["flow"] == "direct"            # a remote that is not GitHub: no pull requests to open
    assert answers["merge_auth"] == "sender" and answers["review"] == "every" and answers["permissions"] == "ask"
    assert answers["tiers"] == [t["name"] for t in det["tests"]] and answers["tiers"]
    assert sorted(answers["guards"]) == ["line_edit", "push_receipt", "revert"] and answers["model"] == "one"
    for question in all_questions(det, answers):   # every recommendation is an answer its question accepts
        validate_answer(question, answers[question["id"]] if isinstance(answers[question["id"]], list)
                        else [answers[question["id"]]])


def test_quick_keeps_what_the_person_already_answered(project):
    det = detect(project)
    answers = quick_answers(det, {"permissions": "auto", "review": "none"})
    assert answers["permissions"] == "auto" and answers["review"] == "none"


def test_quick_through_the_cli_records_every_answer_and_says_them_in_a_few_lines(project):
    code, out = run_cli("onboard", "quick", "--root", str(project))
    assert code == 0, out
    assert "flow: direct" in out and "permissions: ask" in out
    code, out = run_cli("onboard", "next", "--root", str(project))
    assert json.loads(out)["done"] is True


def test_quick_is_the_persons(project, monkeypatch):
    from flotilla.core import caller
    from flotilla.core.census import Session
    monkeypatch.setattr(caller, "calling_sessions", lambda: [Session(
        name="main session 1", session_id="s", kind="background", pid=42, short_id="abc123", status=None,
        state=None, cwd="", started_at_ms=None)])
    code, out = run_cli("onboard", "quick", "--root", str(project))
    assert code == 2 and "background session" in out


def test_the_suggested_fleet_can_deliver(project):
    """It held main, review and judge only: no orchestrator to put a question to the person, no sender to ship
    (field test W2)."""
    composition = suggest_composition({"review": "every", "flow": "direct"})
    assert composition.get("orchestrator") == 1 and composition.get("sender") == 1 and composition.get("main") >= 1


@pytest.mark.parametrize("value", ["orchestrator and reviewers on Opus, the rest on Sonnet", "opus please"])
def test_a_model_answer_in_words_is_refused(project, value):
    """A sentence became `claude --model <sentence>` and no seat would start (field test W3)."""
    det = detect(project)
    question = next(q for q in all_questions(det, {}) if q["id"] == "model")
    with pytest.raises(AnswerError, match="model"):
        validate_answer(question, [value])
    assert validate_answer(question, ["claude-opus-5-5"]) == "claude-opus-5-5"


def test_publish_commits_the_profile_runs_the_tiers_over_that_commit_and_pushes_it(project, tmp_path):
    """Onboarding said "commit .flotilla" and stopped: the rules are read from origin's trunk, and in a session with
    flotilla's guards the push was then refused for want of a receipt over the new commit (field test W5)."""
    import sys
    from test_onboard_cli import write_confirmed
    assert run_cli("onboard", "quick", "--root", str(project))[0] == 0
    command = f"{sys.executable} -c \"print('1 passed')\""
    assert run_cli("onboard", "answer", "tiers", command, "--root", str(project))[0] == 0
    assert write_confirmed(project)[0] == 0
    code, out = run_cli("onboard", "publish", "--root", str(project))
    assert code == 0, out
    pushed = subprocess.run(["git", "--git-dir", str(tmp_path / "origin.git"), "ls-tree", "--name-only", "main",
                             ".flotilla/"], capture_output=True, text=True).stdout
    assert ".flotilla/project.toml" in pushed and "pushed" in out


def test_publish_stops_on_a_red_tier_and_pushes_nothing(project, tmp_path):
    import sys
    from test_onboard_cli import write_confirmed
    assert run_cli("onboard", "quick", "--root", str(project))[0] == 0
    command = f"{sys.executable} -c \"import sys; sys.exit(1)\""
    assert run_cli("onboard", "answer", "tiers", command, "--root", str(project))[0] == 0
    assert write_confirmed(project, "--keep-unmeasured")[0] == 0
    code, out = run_cli("onboard", "publish", "--root", str(project))
    assert code != 0 and "not pushed" in out
    pushed = subprocess.run(["git", "--git-dir", str(tmp_path / "origin.git"), "rev-parse", "--verify", "-q", "main"],
                            capture_output=True, text=True)
    assert pushed.returncode != 0



def test_publish_with_uncommitted_work_says_what_to_do(project):
    import sys
    from test_onboard_cli import write_confirmed
    assert run_cli("onboard", "quick", "--root", str(project))[0] == 0
    command = f"{sys.executable} -c \"print('1 passed')\""
    assert run_cli("onboard", "answer", "tiers", command, "--root", str(project))[0] == 0
    assert write_confirmed(project)[0] == 0
    (project / "draft.txt").write_text("mine\n", encoding="utf-8")
    code, out = run_cli("onboard", "publish", "--root", str(project))
    assert code == 2 and "commit or stash your own changes" in out


def test_write_names_the_step_that_puts_the_profile_where_sessions_read_it(project):
    """The profile does nothing on the person's disk: every session reads origin's trunk. A custom onboarding that
    ends at `write` must say so and name `publish` (field test W5: the session said "commit", not "push")."""
    import sys
    from test_onboard_cli import write_confirmed
    assert run_cli("onboard", "quick", "--root", str(project))[0] == 0
    command = f"{sys.executable} -c \"print('1 passed')\""
    assert run_cli("onboard", "answer", "tiers", command, "--root", str(project))[0] == 0
    code, out = write_confirmed(project)
    assert code == 0, out
    assert "flotilla onboard publish" in out and "origin's trunk" in out


def test_quick_on_a_project_without_github_says_no_ci_rather_than_an_empty_gate(project):
    """worldcore's origin is a bare repository with no CI; the profile asked for a gate command "or nothing checks
    shipped" (field test W5). Quick answers "no CI" there, and the profile carries no gate to fill in."""
    from flotilla.onboard.profile import build_profile
    det = detect(project)
    profile = build_profile(det, quick_answers(det, {}))
    assert profile["ci"] == {"provider": "none"}
