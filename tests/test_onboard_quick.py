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
    assert answers["merge_auth"] == "sender" and answers["review"] == "every" and answers["permissions"] == "auto"
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
    assert "flow: direct" in out and "permissions: auto" in out
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


def _ready(project):
    """Onboarded and written with a green tier, nothing published yet."""
    import sys
    from test_onboard_cli import write_confirmed
    assert run_cli("onboard", "quick", "--root", str(project))[0] == 0
    command = f"{sys.executable} -c \"print('1 passed')\""
    assert run_cli("onboard", "answer", "tiers", command, "--root", str(project))[0] == 0
    assert write_confirmed(project)[0] == 0


def _git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)


def test_publish_never_commits_what_the_person_staged(project, tmp_path):
    """Review of 0.5.0, C1: `git add .flotilla` then a bare `git commit` took the whole index, so a staged secret rode
    in the onboarding commit to origin's trunk. The tree is checked before anything is committed."""
    _ready(project)
    (project / "secret.txt").write_text("token\n", encoding="utf-8")
    _git(project, "add", "secret.txt")
    head = _git(project, "rev-parse", "HEAD").stdout
    code, out = run_cli("onboard", "publish", "--root", str(project))
    assert code == 2 and "secret.txt" in out and "commit or stash your own changes" in out
    assert _git(project, "rev-parse", "HEAD").stdout == head          # nothing committed
    assert _git(tmp_path / "origin.git", "rev-parse", "--verify", "-q", "main").returncode != 0


def test_publish_pushes_nothing_but_the_profile(project, tmp_path):
    """Review of 0.5.0, I1: `git push origin main` pushed every unpushed trunk commit, unseen. Publish refuses while
    trunk carries commits origin has not, and names them."""
    subprocess.run(["git", "-C", str(project), "push", "-q", "origin", "main"], check=True)
    (project / "wip.txt").write_text("half done\n", encoding="utf-8")
    _git(project, "add", "wip.txt")
    _git(project, "commit", "-q", "-m", "wip: not for origin yet")
    _ready(project)
    code, out = run_cli("onboard", "publish", "--root", str(project))
    assert code == 2 and "wip: not for origin yet" in out
    assert "wip.txt" not in _git(tmp_path / "origin.git", "ls-tree", "--name-only", "main").stdout


def test_publish_says_pull_when_origin_is_ahead(project, tmp_path):
    """Review of 0.5.0, M1: a rejected push was answered with "open a pull request"; origin was simply ahead."""
    subprocess.run(["git", "-C", str(project), "push", "-q", "origin", "main"], check=True)
    other = tmp_path / "other"
    # `-b main`: a bare origin's HEAD follows init.defaultBranch, which is `master` on CI runners (v0.5.0 went red)
    subprocess.run(["git", "clone", "-q", "-b", "main", str(tmp_path / "origin.git"), str(other)], check=True)
    (other / "theirs.txt").write_text("x\n", encoding="utf-8")
    _git(other, "add", "theirs.txt")
    _git(other, "commit", "-q", "-m", "a teammate's commit")
    subprocess.run(["git", "-C", str(other), "push", "-q", "origin", "main"], check=True)
    _ready(project)
    head = _git(project, "rev-parse", "HEAD").stdout
    code, out = run_cli("onboard", "publish", "--root", str(project))
    assert code == 2 and "pull" in out and "pull request" not in out
    assert _git(project, "rev-parse", "HEAD").stdout == head


def test_publish_of_an_ignored_profile_is_refused_not_claimed(project):
    """Review of 0.5.0, M7: with `.flotilla/` gitignored, `add` failed unread and publish said the fleet reads it."""
    (project / ".gitignore").write_text(".flotilla/\n", encoding="utf-8")
    _git(project, "add", ".gitignore")
    _git(project, "commit", "-q", "-m", "ignore it")
    _ready(project)
    code, out = run_cli("onboard", "publish", "--root", str(project))
    assert code == 2 and "pushed to origin" not in out and ".flotilla" in out


def test_quick_names_the_test_commands_it_left_out(project):
    """Review of 0.5.0, M6: quick keeps the first four detected tiers and dropped the rest without a word."""
    (project / "pyproject.toml").write_text("[tool.pytest.ini_options]\n", encoding="utf-8")
    (project / "Cargo.toml").write_text("[package]\nname = 'app'\n", encoding="utf-8")
    (project / "go.mod").write_text("module app\n", encoding="utf-8")
    (project / "Makefile").write_text("test:\n\techo ok\n", encoding="utf-8")
    det = detect(project)
    found = [t["name"] for t in det.get("tests") or []]
    assert len(found) > 4, found
    code, out = run_cli("onboard", "quick", "--root", str(project))
    assert code == 0
    left = [name for name in found if name not in out.split("tiers:")[1].splitlines()[0]]
    assert left and all(name in out for name in left) and "project.toml" in out


def test_quick_lets_the_classifier_decide_rather_than_ask_the_person_everything(project):
    """Worldcore field test W16 and the person's decision (2026-10-02): with `ask`, three seats' first census put six
    questions to the person in a minute, each about flotilla's own read commands. Quick recommends Claude Code's auto
    mode; `ask` stays an answer for whoever chooses it."""
    det = detect(project)
    assert quick_answers(det, {})["permissions"] == "auto"
    question = next(q for q in all_questions(det, {}) if q["id"] == "permissions")
    assert {o["value"] for o in question["options"]} == {"ask", "rules", "auto"}
    assert question["options"][0]["value"] == "auto" and "Recommended" in question["options"][0]["label"]


def test_write_says_in_plain_words_what_the_person_agrees_to(project):
    """Worldcore field test W12: the 0.6.0 skill told the session to say the answers in words before asking to write,
    and the session skipped it - the person never heard that merges go to trunk without them. The command says it,
    so it reaches the person through the output whatever the model retells."""
    assert run_cli("onboard", "quick", "--root", str(project))[0] == 0
    code, out = run_cli("onboard", "write", "--root", str(project))
    assert code == 5
    said = out[out.index("in plain words"):]
    assert "merges into trunk" in said and "without asking you" in said
    assert "auto mode" in said and "every branch" in said and "npm test" in said


@pytest.mark.parametrize("data, says, never", [
    ({"flow": {"mode": "pr"}, "pr": {"merged_by": "human"}, "review": {"depth": "every"}},
     "you merge each pull request yourself", "without asking you"),
    ({"flow": {"mode": "direct", "merge_authorized_by": "sender"}, "review": {"depth": "none"}},
     "merges work on its own", "reviewed work"),
    ({"flow": {"mode": "direct", "merge_authorized_by": "sender"}, "permissions": {"mode": "ask"}},
     "except flotilla's own", "every tool call waits"),
    ({"flow": {"mode": "direct", "merge_authorized_by": "sender"}, "permissions": {"mode": "auto"}},
     "no question is put to you", "asked only"),
])
def test_plain_words_say_nothing_false(data, says, never):
    """Review of 0.6.1, I4 and M4: a pull-request flow merged by a person was summed up as "the sender merges ...
    without asking you"; review `none` still said "reviewed work"; ask mode claimed every call waits, and auto mode
    promised questions flotilla never routes."""
    from flotilla.onboard.profile import plain_words
    said = " ".join(plain_words(data))
    assert says in said and never not in said


def test_plain_words_name_an_effort_only_when_one_is_set():
    """Effort costs tokens: a higher one is said before the yes; none set says nothing."""
    from flotilla.onboard.profile import plain_words
    said = " ".join(plain_words({"fleet": {"effort": {"reviewer": "max", "main": "high"}}}))
    assert "reviewer at max" in said and "main at high" in said and "Claude Code's default" in said
    assert "effort" not in " ".join(plain_words({"fleet": {"model": "one"}}))


def test_quick_onboarding_leaves_the_effort_to_claude_code(project):
    det = detect(project)
    assert quick_answers(det, {})["effort"] == "default"


def test_plain_words_say_a_posts_own_effort_still_wins():
    from flotilla.onboard.profile import plain_words
    assert "own `effort`" in " ".join(plain_words({"fleet": {"effort": {"reviewer": "max"}}}))


def test_a_stored_effort_that_no_longer_fits_is_refused_at_write_not_a_traceback(project):
    """Review of 0.18.0, M6: the answers file is plain; an effort naming no post raised AnswerError out of write."""
    assert run_cli("onboard", "quick", "--root", str(project))[0] == 0
    code, out = run_cli("onboard", "answer", "effort", "reviewer=max", "--root", str(project))
    assert code == 0, out
    store = next((project.parent / "state" / "onboarding").glob("*.json"))
    store.write_text(store.read_text(encoding="utf-8").replace("reviewer=max", "builder=max"), encoding="utf-8")
    code, out = run_cli("onboard", "write", "--root", str(project))
    assert code == 2 and "builder" in out and "Traceback" not in out


@pytest.mark.parametrize("data, says", [
    ({"flow": {"mode": "pr", "merge_authorized_by": "human"}, "pr": {"merged_by": "sender"}},
     "only the sender runs `gh pr merge`, after you approve"),
    ({"flow": {"mode": "pr", "merge_authorized_by": "sender"}, "pr": {"merged_by": "sender"}},
     "only the sender runs `gh pr merge`;"),
    ({"flow": {"mode": "direct", "merge_authorized_by": "human"}}, "only the sender pushes to trunk, after"),
    ({"flow": {"mode": "local"}}, "only the sender merges into trunk"),
])
def test_plain_words_name_the_door_not_only_the_approval(data, says):
    """A new project's onboarding (2026-10-19): the session read "nothing reaches trunk until you approve it" as the
    person's routine being refused - who runs the merge, and who never does, was in the posts and nowhere before
    the yes."""
    from flotilla.onboard.profile import plain_words
    said = " ".join(plain_words(data))
    assert says in said and "the orchestrator" in said and "never" in said


def test_plain_words_name_no_merging_session_where_the_person_merges():
    from flotilla.onboard.profile import plain_words
    said = " ".join(plain_words({"flow": {"mode": "pr"}, "pr": {"merged_by": "human"}}))
    assert "only the sender" not in said and "no session merges" in said


@pytest.mark.parametrize("who, approval", [("human", True), ("sender", False)])
def test_plain_words_say_the_push_guard_judges_every_session_the_persons_own_included(who, approval):
    """The same onboarding: a project with its own merge routine learned only from guards/push.py that the guard
    judges an interactive session holding no post too. Said before the yes, with what `!` leaves to the person."""
    from flotilla.onboard.profile import plain_words
    data = {"flow": {"mode": "pr", "merge_authorized_by": who}, "pr": {"merged_by": "sender"},
            "guards": {"push_receipt": True, "revert": True}}
    said = " ".join(plain_words(data))
    assert "every Claude Code session" in said and "your own" in said and "holds no post" in said
    assert "`gh pr merge`" in said and "green push receipt" in said and "after `!`" in said and "pre-push" in said
    assert ("your approval" in said) is approval


def test_plain_words_say_other_guards_judge_every_session_without_promising_a_receipt():
    from flotilla.onboard.profile import plain_words
    said = " ".join(plain_words({"flow": {"mode": "direct", "merge_authorized_by": "sender"},
                                 "guards": {"revert": True, "push_receipt": False}}))
    assert "every Claude Code session" in said and "after `!`" in said and "push receipt" not in said


def test_plain_words_say_nothing_of_guards_when_none_is_on():
    from flotilla.onboard.profile import plain_words
    said = " ".join(plain_words({"flow": {"mode": "direct", "merge_authorized_by": "sender"},
                                 "guards": {"revert": False, "push_receipt": False}}))
    assert "every Claude Code session" not in said


def _open_pull(monkeypatch):
    from flotilla.onboard import detect_prs
    monkeypatch.setattr(detect_prs, "open_pull_requests", lambda origin, run=None: {
        "source": "github", "open": [{"number": 12, "branch": "feat/theirs", "title": "Made by hand"}]})


def test_write_says_open_pull_requests_made_outside_the_ledger_before_the_yes(project, monkeypatch):
    _open_pull(monkeypatch)
    assert run_cli("onboard", "quick", "--root", str(project))[0] == 0
    code, out = run_cli("onboard", "write", "--root", str(project))
    assert code == 5 and "#12 `feat/theirs`" in out and "flotilla work claim feat/theirs" in out
    assert out.index("#12") < out.index("--confirm ")   # said before the line that asks for the yes


def test_publish_warns_of_open_pull_requests_and_still_publishes(project, monkeypatch, tmp_path):
    import sys
    from test_onboard_cli import write_confirmed
    _open_pull(monkeypatch)
    assert run_cli("onboard", "quick", "--root", str(project))[0] == 0
    command = f"{sys.executable} -c \"print('1 passed')\""
    assert run_cli("onboard", "answer", "tiers", command, "--root", str(project))[0] == 0
    assert write_confirmed(project)[0] == 0
    code, out = run_cli("onboard", "publish", "--root", str(project))
    assert code == 0, out
    assert "#12 `feat/theirs`" in out and "pushed" in out


@pytest.mark.parametrize("data, held", [
    ({"flow": {"mode": "pr", "merge_authorized_by": "human"}, "pr": {"merged_by": "sender"},
      "guards": {"push_receipt": True}}, "the push guard refuses it to any other seat"),
    ({"flow": {"mode": "direct", "merge_authorized_by": "sender"}, "guards": {"push_receipt": False}},
     "no guard enforces it here"),
    ({"flow": {"mode": "local"}, "guards": {"push_receipt": True}}, "no guard enforces it here"),
])
def test_the_door_line_says_whether_anything_holds_seats_to_it(data, held):
    """Review of this change, I2 and M2: "never merge" read as a rule for every session, the person's own included,
    while the code lets a session with no post merge, and holds seats to it only with the push guard on and a flow
    that pushes."""
    from flotilla.onboard.profile import plain_words
    said = " ".join(plain_words(data))
    assert "of the fleet's sessions" in said and held in said


def test_the_guard_line_says_approval_is_a_ledger_row_and_asked_of_trunk_and_merges_only():
    """Review of this change, I2, M1 and M3: approval is asked only of a push to trunk and `gh pr merge`, only through
    a ledger row, so the person's own work with no row is merged by them after `!`; pre-push never sees gh pr merge."""
    from flotilla.onboard.profile import plain_words
    said = " ".join(plain_words({"flow": {"mode": "pr", "merge_authorized_by": "human"}, "pr": {"merged_by": "sender"},
                                 "guards": {"push_receipt": True}}))
    assert "a push to trunk and `gh pr merge` also need your approval of the work's ledger row" in said
    assert "you merge yourself after `!`" in said and "never a `gh pr merge`" in said


def test_write_does_not_ask_gh_where_no_session_merge_would_be_refused(project, monkeypatch):
    from flotilla.onboard import detect_prs

    def asked(*args, **kwargs):
        raise AssertionError("gh asked although nothing would refuse a merge")
    monkeypatch.setattr(detect_prs, "open_pull_requests", asked)
    assert run_cli("onboard", "quick", "--root", str(project))[0] == 0
    assert run_cli("onboard", "answer", "guards", "revert", "--root", str(project))[0] == 0
    assert run_cli("onboard", "write", "--root", str(project))[0] == 5


def test_detection_does_not_ask_gh_for_pull_requests(project, monkeypatch):
    """Review of this change, M6: detection runs on every onboarding command; the list is asked by write and publish."""
    from flotilla.onboard import detect_prs
    monkeypatch.setattr(detect_prs, "open_pull_requests", lambda *a, **k: (_ for _ in ()).throw(AssertionError()))
    assert run_cli("onboard", "next", "--root", str(project))[0] == 0


def test_a_branch_only_a_closed_row_names_is_outside_the_ledger_again(project, monkeypatch):
    """Review of this change, M5: a released or closed row approves nothing, so an open pull request on a reused
    branch is still made outside the ledger; an open row's branch is not."""
    from types import SimpleNamespace
    from flotilla.ledger import commands as ledger_commands
    from flotilla.onboard.commands import _pull_request_lines
    rows = {"1": SimpleNamespace(branch="feat/old", is_open=False), "2": SimpleNamespace(branch="feat/live",
                                                                                          is_open=True)}
    monkeypatch.setattr(ledger_commands, "open_ledger", lambda root: SimpleNamespace(rows=lambda: rows))
    from flotilla.onboard import detect_prs
    monkeypatch.setattr(detect_prs, "open_pull_requests", lambda origin, run=None: {"source": "github", "open": [
        {"number": 1, "branch": "feat/old", "title": "a"}, {"number": 2, "branch": "feat/live", "title": "b"}]})
    said = " ".join(_pull_request_lines({"root": str(project), "origin": "github.com/o/app"},
                                        {"flow": {"mode": "pr", "merge_authorized_by": "human"},
                                         "pr": {"merged_by": "sender"}, "guards": {"push_receipt": True}}))
    assert "#1 `feat/old`" in said and "#2" not in said


def test_publish_names_open_pull_requests_after_the_push_not_before(project, monkeypatch):
    """Review of this change, M8: said before the push in the same command, "merge them before publishing" could not
    be acted on; said after it, it speaks of what to do now the rules are on trunk."""
    import sys
    from test_onboard_cli import write_confirmed
    _open_pull(monkeypatch)
    assert run_cli("onboard", "quick", "--root", str(project))[0] == 0
    command = f"{sys.executable} -c \"print('1 passed')\""
    assert run_cli("onboard", "answer", "tiers", command, "--root", str(project))[0] == 0
    assert write_confirmed(project)[0] == 0
    code, out = run_cli("onboard", "publish", "--root", str(project))
    assert code == 0, out
    assert out.index("pushed to origin") < out.index("#12") and "now that the profile is on trunk" in out
    assert "merge them before publishing" not in out


def test_publish_that_cannot_push_tells_a_person_who_authorizes_merges_to_merge_the_pull_request_themselves(
        project, monkeypatch, tmp_path):
    """Review of this change, I1: until trunk carries a profile the approval cannot be asked of a ledger, and that
    refusal has no override - where the person authorizes merges, no session can merge the onboarding's pull
    request, and "open one and merge it" sent the session into it."""
    import sys
    from test_onboard_cli import write_confirmed
    hook = tmp_path / "origin.git" / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\necho 'trunk takes pull requests only' >&2\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)
    assert run_cli("onboard", "quick", "--root", str(project))[0] == 0
    assert run_cli("onboard", "answer", "merge_auth", "human", "--root", str(project))[0] == 0
    command = f"{sys.executable} -c \"print('1 passed')\""
    assert run_cli("onboard", "answer", "tiers", command, "--root", str(project))[0] == 0
    assert write_confirmed(project)[0] == 0
    code, out = run_cli("onboard", "publish", "--root", str(project))
    assert code == 2 and "merge it yourself" in out and "no session can merge it" in out
