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
            queue.answer(ctx.ledger.state_dir, "repo", asked.id, choice, why=why, now=now)
    return on_sleep


def test_the_broker_is_on_only_in_ask_mode():
    assert decide.enabled(ASK)
    assert not decide.enabled({"permissions": {"mode": "auto"}})
    assert not decide.enabled({"permissions": {"mode": "ask"}, "broker": {"enabled": False}})


def test_an_interactive_session_keeps_its_dialog(tmp_path):
    clock = Clock()   # under a fault the hook would wait; a fake clock makes that a decision, not a hang
    assert decide.decide(TOUCH, fleet(tmp_path, kind="interactive"), clock=clock, sleep=clock.sleep) is None


def test_an_unknown_census_leaves_the_dialog_alone(tmp_path):
    ctx = context(tmp_path, me=None, census_error="down", profile=ASK)
    clock = Clock()
    assert decide.decide(TOUCH, ctx, clock=clock, sleep=clock.sleep) is None


def test_no_orchestrator_is_an_immediate_deny(tmp_path):
    decision = decide.decide(TOUCH, fleet(tmp_path, orchestrator=False))
    assert decision["behavior"] == "deny" and "no live orchestrator" in decision["message"]


def test_the_orchestrators_own_question_is_denied_naming_attach(tmp_path):
    decision = decide.decide(TOUCH, fleet(tmp_path, me="orchestrator 1", orchestrator=False))
    assert decision["behavior"] == "deny" and "claude attach" in decision["message"]


def test_an_allow_arrives_while_the_hook_waits(tmp_path):
    ctx = fleet(tmp_path)
    clock = Clock(on_sleep=answer_on_first_sleep(ctx, queue.ALLOW))
    assert decide.decide(TOUCH, ctx, clock=clock, sleep=clock.sleep) == {"behavior": "allow"}


def test_an_answer_for_the_session_carries_claude_codes_suggestions(tmp_path):
    ctx = fleet(tmp_path)
    clock = Clock(on_sleep=answer_on_first_sleep(ctx, queue.SESSION))
    decision = decide.decide(TOUCH, ctx, clock=clock, sleep=clock.sleep)
    assert decision["updatedPermissions"] == [{"type": "addDirectories", "directories": ["/w"],
                                               "destination": "session"}]


def test_without_suggestions_the_session_rule_is_exact():
    assert decide.session_rules("Bash", {"command": "touch x.txt"}, []) == [
        {"type": "addRules", "rules": [{"toolName": "Bash", "ruleContent": "touch x.txt"}], "behavior": "allow",
         "destination": "session"}]


def test_a_deny_carries_the_persons_reason(tmp_path):
    ctx = fleet(tmp_path)
    clock = Clock(on_sleep=answer_on_first_sleep(ctx, queue.DENY, why="not in this repo"))
    decision = decide.decide(TOUCH, ctx, clock=clock, sleep=clock.sleep)
    assert decision["behavior"] == "deny" and "not in this repo" in decision["message"]


def test_nobody_answering_is_a_deny_before_the_hooks_timeout(tmp_path):
    ctx = fleet(tmp_path)
    clock = Clock()
    decision = decide.decide(TOUCH, ctx, clock=clock, sleep=clock.sleep)
    assert decision["behavior"] == "deny" and "nobody answered in 540 s" in decision["message"]
    assert clock.now - 1000 <= decide.MOST_WAIT
    assert queue.live(ctx.ledger.state_dir, "repo", now=clock.now) == []


@pytest.mark.parametrize("value,expected", [(5, 30), (9999, 590), ("x", 540), (True, 540), (120, 120)])
def test_the_wait_is_clamped_under_the_hooks_timeout(value, expected):
    assert decide.wait_seconds({"broker": {"wait_seconds": value}}) == expected
