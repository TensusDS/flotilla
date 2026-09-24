import pytest

from flotilla.onboard import questions as qs


def detection(**overrides):
    base = {
        "remote": True, "trunk": "main",
        "tests": [{"name": "python", "command": "uv run pytest", "source": "pyproject.toml"}],
        "ci": {"provider": "github", "jobs": ["test"], "jobs_source": "last-push-run", "merge_methods": ["squash"]},
        "release": {"version_files": []},
        "signals": {"multi_repo": [], "deployment": [], "shared_files": [], "sequential": []},
    }
    base.update(overrides)
    return base


def ids(question_list):
    return [q["id"] for q in question_list]


def answer_all(det):
    answers = {}
    for _ in range(20):
        batch = qs.next_questions(det, answers)
        if not batch:
            return answers
        for q in batch:
            first = q["options"][0]["value"]
            answers[q["id"]] = [first] if q["multi_select"] else first
    raise AssertionError("the questionnaire never ended")


def test_first_round_for_a_repository_with_a_remote():
    assert ids(qs.next_questions(detection(), {})) == ["flow", "review", "permissions", "ci"]


def test_no_remote_skips_the_flow_question():
    first = qs.next_questions(detection(remote=False, ci={"provider": "none"}), {})
    assert "flow" not in ids(first) and "merge_auth" not in ids(first)


def test_merge_auth_follows_a_sender_merged_flow():
    assert "merge_auth" in ids(qs.all_questions(detection(), {"flow": "pr-sender"}))
    assert "merge_auth" not in ids(qs.all_questions(detection(), {"flow": "pr-human"}))


def test_ci_where_follows_a_ci_answer():
    assert "ci_where" not in ids(qs.all_questions(detection(), {}))
    assert "ci_where" in ids(qs.all_questions(detection(), {"ci": "github"}))


def test_github_is_offered_only_when_detected():
    ci_question = next(q for q in qs.all_questions(detection(ci={"provider": "none"}), {}) if q["id"] == "ci")
    assert [o["value"] for o in ci_question["options"]] == ["command", "none"]


def test_conditional_questions_follow_their_signals():
    quiet = ids(qs.all_questions(detection(), {}))
    loud = ids(qs.all_questions(detection(signals={"multi_repo": ["../core"], "deployment": ["deploy"],
                                                   "shared_files": ["TODO.md"], "sequential": ["migrations"]},
                                          release={"version_files": ["pyproject.toml"]}), {}))
    for conditional in ("repos", "release", "deploy", "shared", "sequential"):
        assert conditional not in quiet and conditional in loud


def test_merge_method_only_when_several_are_allowed_and_a_pr_flow():
    many = detection(ci={"provider": "github", "jobs": [], "jobs_source": "x", "merge_methods": ["merge", "squash"]})
    assert "merge_method" in ids(qs.all_questions(many, {"flow": "pr-sender"}))
    assert "merge_method" not in ids(qs.all_questions(many, {"flow": "direct"}))
    assert "merge_method" not in ids(qs.all_questions(detection(), {"flow": "pr-sender"}))


def test_every_question_fits_ask_user_question_limits():
    rich = detection(tests=[{"name": f"t{n}", "command": f"run {n}", "source": "x"} for n in range(6)],
                     ci={"provider": "github", "jobs": [], "jobs_source": "x", "merge_methods": ["merge", "squash", "rebase"]},
                     release={"version_files": ["pyproject.toml"], "latest_tag": "v1.0.0"},
                     signals={"multi_repo": ["../core"], "deployment": ["deploy"],
                              "shared_files": ["TODO.md"], "sequential": ["migrations"]})
    everything = qs.all_questions(rich, {"flow": "pr-sender", "ci": "command"})
    for q in everything:
        assert len(q["header"]) <= 12, q["id"]
        assert 2 <= len(q["options"]) <= 4, q["id"]
    assert len(next(q for q in everything if q["id"] == "tiers")["options"]) == 4


def test_answers_are_validated():
    ci_question = next(q for q in qs.all_questions(detection(), {}) if q["id"] == "ci")
    assert qs.validate_answer(ci_question, ["github"]) == "github"
    with pytest.raises(qs.AnswerError):
        qs.validate_answer(ci_question, ["jenkins"])
    tiers = next(q for q in qs.all_questions(detection(), {}) if q["id"] == "tiers")
    assert qs.validate_answer(tiers, ["python", "make test-slow"]) == ["python", "make test-slow"]
    with pytest.raises(qs.AnswerError):
        qs.validate_answer(tiers, [])


def test_the_questionnaire_ends():
    answers = answer_all(detection())
    assert qs.next_questions(detection(), answers) == []
    assert {"flow", "review", "permissions", "ci", "tiers", "tracker", "guards", "model"} <= set(answers)


def test_composition_suggestion():
    assert qs.suggest_composition({"review": "every"}) == {"main": 1, "review": 1}
    assert qs.suggest_composition({"review": "none"}) == {"main": 1}
    assert qs.suggest_composition({"review": "every", "deploy": "web"}) == {"main": 1, "review": 1, "judge": 1}


def test_no_guards_is_an_answer():
    guards = next(q for q in qs.all_questions(detection(), {}) if q["id"] == "guards")
    assert qs.validate_answer(guards, ["none"]) == ["none"]


def test_no_remote_asks_no_ci_question():
    det = detection(remote=False)
    assert "ci" not in ids(qs.all_questions(det, {}))


def test_an_option_label_is_not_a_typed_value():
    tracker = next(q for q in qs.all_questions(detection(), {}) if q["id"] == "tracker")
    with pytest.raises(qs.AnswerError, match="label"):
        qs.validate_answer(tracker, ["GitHub Issues"])
    tiers = next(q for q in qs.all_questions(detection(tests=[]), {}) if q["id"] == "tiers")
    with pytest.raises(qs.AnswerError, match="label"):
        qs.validate_answer(tiers, ["Add them later"])


def test_a_typed_tracker_pattern_must_compile():
    tracker = next(q for q in qs.all_questions(detection(), {}) if q["id"] == "tracker")
    with pytest.raises(qs.AnswerError, match="regular expression"):
        qs.validate_answer(tracker, ["[unclosed"])
    assert qs.validate_answer(tracker, ["^CURVE-\\d+$"]) == "^CURVE-\\d+$"


def test_own_register_needs_a_typed_pattern():
    tracker = next(q for q in qs.all_questions(detection(), {}) if q["id"] == "tracker")
    with pytest.raises(qs.AnswerError, match="pattern"):
        qs.validate_answer(tracker, ["own-register"])


def test_stale_answers_are_dropped():
    answers = {"flow": "local", "merge_auth": "sender", "review": "every"}
    assert qs.effective_answers(detection(), answers) == {"flow": "local", "review": "every"}
