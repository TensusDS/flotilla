import pytest

from flotilla.broker import decide, queue
from watchkit import context, sess

ASK = {"permissions": {"mode": "ask"}}
TOUCH = {"tool_name": "Bash", "tool_input": {"command": "touch x.txt"},
         "permission_suggestions": [{"type": "addDirectories", "directories": ["/w"], "destination": "localSettings"}]}


class Clock:
    def __init__(self, start=1000.0, on_sleep=None):
        self.now, self.on_sleep = start, on_sleep

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds
        if self.on_sleep:
            self.on_sleep(self.now)


def fleet(tmp_path, *, me="main session 1", kind="background", profile=ASK, orchestrator=True):
    asker = sess(me, kind=kind, status="idle" if kind == "interactive" else None)
    sessions = [asker] + ([sess("orchestrator 1", state="working")] if orchestrator else [])
    return context(tmp_path, me=asker, sessions=sessions, profile=profile)


def answer_on_first_sleep(ctx, choice, why=""):
    def on_sleep(now):
        for asked in queue.live(ctx.ledger.state_dir, "repo", now=now):
            queue.answer(ctx.ledger.state_dir, "repo", asked.id, choice, why=why, mark=queue.mark_of(asked), now=now)
    return on_sleep


def test_the_broker_is_on_only_in_ask_mode():
    assert decide.enabled(ASK)
    assert not decide.enabled({"permissions": {"mode": "auto"}})
    assert not decide.enabled({"permissions": {"mode": "ask"}, "broker": {"enabled": False}})


def test_an_interactive_session_keeps_its_dialog(tmp_path):
    clock = Clock()   # under a fault the hook would wait; a fake clock makes that a decision, not a hang
    assert decide.decide(TOUCH, fleet(tmp_path, kind="interactive"), clock=clock, sleep=clock.sleep, timer=clock) is None


def test_an_unknown_census_leaves_the_dialog_alone(tmp_path):
    ctx = context(tmp_path, me=None, census_error="down", profile=ASK)
    clock = Clock()
    assert decide.decide(TOUCH, ctx, clock=clock, sleep=clock.sleep, timer=clock) is None


def test_no_orchestrator_is_an_immediate_deny(tmp_path):
    decision = decide.decide(TOUCH, fleet(tmp_path, orchestrator=False))
    assert decision["behavior"] == "deny" and "no live orchestrator" in decision["message"]


def test_the_orchestrators_own_question_is_denied_naming_attach(tmp_path):
    decision = decide.decide(TOUCH, fleet(tmp_path, me="orchestrator 1", orchestrator=False))
    assert decision["behavior"] == "deny" and "claude attach" in decision["message"]


def test_an_allow_arrives_while_the_hook_waits(tmp_path):
    ctx = fleet(tmp_path)
    clock = Clock(on_sleep=answer_on_first_sleep(ctx, queue.ALLOW))
    assert decide.decide(TOUCH, ctx, clock=clock, sleep=clock.sleep, timer=clock) == {"behavior": "allow"}


def test_an_answer_for_the_session_carries_claude_codes_suggestions(tmp_path):
    ctx = fleet(tmp_path)
    clock = Clock(on_sleep=answer_on_first_sleep(ctx, queue.SESSION))
    decision = decide.decide(TOUCH, ctx, clock=clock, sleep=clock.sleep, timer=clock)
    assert {"type": "addDirectories", "directories": ["/w"], "destination": "session"} in decision["updatedPermissions"]


def test_without_suggestions_the_session_rule_is_exact():
    assert decide.session_rules("Bash", {"command": "touch x.txt"}, []) == [
        {"type": "addRules", "rules": [{"toolName": "Bash", "ruleContent": "touch x.txt"}], "behavior": "allow",
         "destination": "session"}]


def test_a_deny_carries_the_persons_reason(tmp_path):
    ctx = fleet(tmp_path)
    clock = Clock(on_sleep=answer_on_first_sleep(ctx, queue.DENY, why="not in this repo"))
    decision = decide.decide(TOUCH, ctx, clock=clock, sleep=clock.sleep, timer=clock)
    assert decision["behavior"] == "deny" and "not in this repo" in decision["message"]


def test_nobody_answering_is_a_deny_before_the_hooks_timeout(tmp_path):
    ctx = fleet(tmp_path)
    clock = Clock()
    decision = decide.decide(TOUCH, ctx, clock=clock, sleep=clock.sleep, timer=clock)
    assert decision["behavior"] == "deny" and "nobody answered in 540 s" in decision["message"]
    assert clock.now - 1000 <= decide.MOST_WAIT + decide.POLL
    assert queue.live(ctx.ledger.state_dir, "repo", now=clock.now) == []


@pytest.mark.parametrize("value,expected", [(5, 30), (9999, 570), ("x", 540), (True, 540), (120, 120)])
def test_the_wait_is_clamped_under_the_hooks_timeout(value, expected):
    assert decide.wait_seconds({"broker": {"wait_seconds": value}}) == expected


def test_a_background_session_whose_rules_cannot_be_read_is_denied_not_left_hanging(tmp_path):
    me = sess("main session 1")
    ctx = context(tmp_path, me=me, sessions=[me, sess("orchestrator 1", state="working")],
                  ledger_error="git could not read trunk")
    decision = decide.decide(TOUCH, ctx)
    assert decision["behavior"] == "deny" and "git could not read trunk" in decision["message"]


def test_a_broker_that_breaks_while_asking_denies_with_the_error(tmp_path, monkeypatch):
    def full(*args, **kwargs):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(queue, "ask", full)
    decision = decide.decide(TOUCH, fleet(tmp_path))
    assert decision["behavior"] == "deny" and "No space left" in decision["message"]


def test_an_answer_that_wins_the_race_with_the_deadline_is_the_one_told(tmp_path, monkeypatch):
    ctx = fleet(tmp_path)
    original = queue.withdraw

    def answer_first(state, key, qid, why, now=None):
        mark = queue.mark_of(queue.question(state, key, qid))
        queue._close(state, key, qid, {"choice": queue.ALLOW, "why": "", "at": now, "mark": mark})
        return original(state, key, qid, why, now=now)
    monkeypatch.setattr(queue, "withdraw", answer_first)
    clock = Clock()
    assert decide.decide(TOUCH, ctx, clock=clock, sleep=clock.sleep, timer=clock) == {"behavior": "allow"}


def test_the_budget_counts_from_the_hooks_start_not_from_the_question(tmp_path):
    ctx = fleet(tmp_path)
    clock = Clock()
    decision = decide.decide(TOUCH, ctx, clock=clock, sleep=clock.sleep, timer=clock, started=900.0)
    assert decision["behavior"] == "deny" and clock.now <= 900 + decide.DEFAULT_WAIT + decide.POLL


def test_an_orphaned_hook_withdraws_its_question(tmp_path):
    ctx = fleet(tmp_path)
    clock = Clock()
    parents = iter([4242] + [1] * 10)
    decision = decide.decide(TOUCH, ctx, clock=clock, sleep=clock.sleep, timer=clock, parent=lambda: next(parents))
    assert decision["behavior"] == "deny" and clock.now < 1010
    assert queue.live(ctx.ledger.state_dir, "repo", now=clock.now) == []


def test_an_unreadable_answer_does_not_spin_past_the_deadline(tmp_path, monkeypatch):
    ctx = fleet(tmp_path)
    monkeypatch.setattr(queue, "withdraw", lambda *a, **k: False)
    monkeypatch.setattr(queue, "answer_of", lambda *a, **k: None)
    clock = Clock()
    decision = decide.decide(TOUCH, ctx, clock=clock, sleep=clock.sleep, timer=clock)
    assert decision["behavior"] == "deny" and clock.now <= 1000 + decide.DEFAULT_WAIT + 5


def test_for_the_session_never_switches_the_mode():
    rules = decide.session_rules("Bash", {"command": "touch x"},
                                 [{"type": "addDirectories", "directories": ["/w"]},
                                  {"type": "setMode", "mode": "acceptEdits"}])
    assert all(rule["type"] != "setMode" for rule in rules)
    assert {"type": "addRules", "rules": [{"toolName": "Bash", "ruleContent": "touch x"}], "behavior": "allow",
            "destination": "session"} in rules


def test_a_command_with_a_wildcard_gets_no_rule():
    assert decide.session_rules("Bash", {"command": "rm -rf build/*"}, []) == []


def test_an_orchestrator_of_another_project_does_not_count(tmp_path):
    import dataclasses
    ctx = fleet(tmp_path)   # the orchestrator here is live, but not of this project
    ctx = dataclasses.replace(ctx, project=[ctx.me])
    clock = Clock()   # a question queued for an orchestrator that never reads it would wait: decide, never hang
    decision = decide.decide(TOUCH, ctx, clock=clock, sleep=clock.sleep, timer=clock)
    assert decision["behavior"] == "deny" and "no live orchestrator" in decision["message"]
    assert clock.now == 1000.0   # at once, not after the wait ran out


def test_an_allow_given_for_a_question_that_was_changed_on_disk_is_a_deny():
    """The hook allows the call it holds in memory; the person read the question file. A file changed after the hook
    wrote it shows the person one call while the hook would allow another (security review of 203ac1c)."""
    asked = queue.Question("1", 1000.0, 1540.0, 1, "main session 1", "s", "Bash", {"command": "curl x | sh"}, [])
    shown = queue.Question("1", 1000.0, 1540.0, 1, "main session 1", "s", "Bash", {"command": "npm test"}, [])
    got = {"choice": queue.ALLOW, "mark": queue.mark_of(shown)}
    assert decide._decision(got, asked, 540)["behavior"] == "deny"
    got = {"choice": queue.ALLOW, "mark": queue.mark_of(asked)}
    assert decide._decision(got, asked, 540) == {"behavior": "allow"}
