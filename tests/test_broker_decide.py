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


def test_a_leading_session_not_yet_named_takes_the_question(tmp_path):
    """Worldcore field test W13: `--fill` raised the seats before the person's next message gave the leading session
    its name, and every seat's first call was refused "no live orchestrator". A recorded lead of a live session of
    the project is the orchestrator for the broker, as it is for `--fill`."""
    from flotilla.core.storage import LocalLogStore
    from flotilla.fleet import lead
    asker = sess("worldcore-main session 1")
    leader = sess("worldcore-0c", kind="interactive", status="idle")
    ctx = context(tmp_path, me=asker, sessions=[asker, leader], profile=ASK)
    assert decide.decide(TOUCH, ctx)["behavior"] == "deny"                       # unnamed and unrecorded: nobody
    lead.record(LocalLogStore(ctx.ledger.state_dir / "fleet"), leader.session_id, "worldcore-orchestrator 1", now="t")
    clock = Clock(on_sleep=answer_on_first_sleep(ctx, queue.ALLOW))
    assert decide.decide(TOUCH, ctx, clock=clock, sleep=clock.sleep, timer=clock) == {"behavior": "allow"}


def test_a_leading_session_not_yet_named_may_answer(tmp_path, monkeypatch):
    """The other half of W13: the leader answers the seats' questions with `permit answer`, which asks the caller's
    post; before the name shows, a recorded lead is the orchestrator there too."""
    from flotilla.broker import commands
    from flotilla.core.storage import LocalLogStore
    from flotilla.fleet import lead
    from watchkit import onboarded
    root = onboarded(tmp_path)
    leader = sess("worldcore-0c", kind="interactive", status="idle")
    monkeypatch.setenv("FLOTILLA_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr("flotilla.core.census.read_census", lambda timeout=10: [leader])
    monkeypatch.setattr("flotilla.core.identity.find_calling_session", lambda sessions, parent_of: leader)
    assert commands._caller(root) == ("worldcore-0c", "")
    lead.record(LocalLogStore(tmp_path / "state" / "fleet"), leader.session_id, "worldcore-orchestrator 1", now="t")
    assert commands._caller(root) == ("worldcore-0c", "orchestrator")


from flotilla.hooks import CLI   # noqa: E402 - the plugin's own command line, by its real path

OWN = str(CLI)


@pytest.fixture
def opted(monkeypatch):
    """The person opted in on origin's trunk - asked of origin by the guard's own helper, measured in its tests."""
    monkeypatch.setattr(decide, "_opted_in", lambda root: True)


@pytest.mark.parametrize("command", [f"{OWN} status", f"{OWN} fleet", f"{OWN} work show feat/x",
                                     f"{OWN} work hand feat/x", f"{OWN} brief", f"{OWN} watch --once", f"{OWN} lane",
                                     f"{OWN} work wait feat/x --on me --why 'a question'"])
def test_flotillas_own_commands_are_not_put_to_the_person(tmp_path, command, opted):
    """Worldcore field test W16: in ask mode three seats' first census put six questions to the person in a minute,
    each about flotilla's own commands, whose every move flotilla checks itself. Those pass without a question."""
    payload = {"tool_name": "Bash", "tool_input": {"command": command}}
    assert decide.decide(payload, fleet(tmp_path, orchestrator=False)) == {"behavior": "allow"}


@pytest.mark.parametrize("command", [
    f"{OWN} status; rm -rf x", f"{OWN} status && curl evil", f"{OWN} status | sh", f"{OWN} status > /etc/x",
    f"{OWN} status $(rm x)", f"PYTHONPATH=/tmp/x {OWN} status", f"env FOO=1 {OWN} status",
    f"{OWN} fleet down", f"{OWN} fleet --root . down", f"{OWN} lane --root . run --for b -- ls", f"{OWN} lane run --for b -- rm -rf x", f"{OWN} onboard answer tiers 'rm -rf x'",
    f"{OWN} permit answer 1 allow --mark m", f"{OWN} spawn -M 1", f"{OWN} retire x", f"{OWN} guard install",
    "/tmp/flotilla status", "./flotilla status", f"bash {OWN} status", f"{OWN} work approve feat/x",
    # review of 0.6.1, C1/I1/I2/M6: each of these runs code, steps around a check, or writes anywhere
    f"{OWN} receipt run --purpose handover", f"{OWN} receipt run --purpose push --tree /tmp/x --no-lane",
    f"{OWN} work hand feat/x --skip-event pre-handed --skip-why ok", f"{OWN} work hand feat/x --as other",
    f"{OWN} helper raise --for b --task t --anyway", f"{OWN} tree cut b --tree /tmp/anywhere",
    f"{OWN} tree switch b", f"{OWN} lane take --note x", f"{OWN} status\nrm -rf x", f"{OWN} status\rrm -rf x",
    # review of 0.6.10, C2: a glob the shell expands to a refused word, given a file of that name in the cwd
    f"{OWN} work approv[e] feat/x", f"{OWN} fleet dow[n]", f"{OWN} work a*e feat/x", f"{OWN} work ?pprove feat/x",
    f"{OWN} status\t--root /x", f"{OWN} status ~/x",
    # review of 0.6.10, I3: argparse takes an option's unambiguous prefix
    f"{OWN} work hand feat/x --a other", f"{OWN} work claim feat/x --tr /tmp/x", f"{OWN} work hand feat/x --as=other",
    f"{OWN} work land feat/x --skip-e pre-landed --skip-w y",
    # third review of 0.6.10, C1: a root that is not this project's repository runs its profile's commands
    f"{OWN} lane --root /tmp/made-by-a-seat", f"{OWN} work show feat/x --root /tmp/made-by-a-seat",
])
def test_anything_else_still_goes_to_the_person(tmp_path, command, opted):
    payload = {"tool_name": "Bash", "tool_input": {"command": command}}
    decision = decide.decide(payload, fleet(tmp_path, orchestrator=False))
    assert decision["behavior"] == "deny" and "no live orchestrator" in decision["message"]


@pytest.mark.parametrize("profile, kind", [({"permissions": {"mode": "auto"}}, "background"), (ASK, "interactive")])
def test_the_own_command_pass_widens_nothing_outside_ask_mode_and_background_seats(tmp_path, profile, kind,
                                                                                    opted):
    """Review of 0.6.1, I3: the pass sits after the background-only and ask-mode checks, and nothing pinned it there;
    moved above them it would allow for an interactive session or under auto mode, where the dialog decides."""
    payload = {"tool_name": "Bash", "tool_input": {"command": f"{OWN} status"}}
    assert decide.decide(payload, fleet(tmp_path, kind=kind, profile=profile)) is None


def test_a_lead_left_a_day_is_not_the_orchestrator_for_the_broker(tmp_path):
    """Review of 0.6.1, M1: the broker read leads without the day's expiry the rename hook and --fill apply."""
    import datetime as dt
    from flotilla.core.storage import LocalLogStore
    from flotilla.fleet import lead
    asker = sess("worldcore-main session 1")
    leader = sess("worldcore-0c", kind="interactive", status="idle")
    ctx = context(tmp_path, me=asker, sessions=[asker, leader], profile=ASK)
    old = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=25)).isoformat()
    lead.record(LocalLogStore(ctx.ledger.state_dir / "fleet"), leader.session_id, "worldcore-orchestrator 1", now=old)
    clock = Clock()   # counted as the orchestrator, the question would wait; a fake clock makes that a time-out
    decision = decide.decide(TOUCH, ctx, clock=clock, sleep=clock.sleep, timer=clock)
    assert decision["behavior"] == "deny" and "no live orchestrator" in decision["message"]


def _kept_inputs(ctx):
    import json
    base = queue.folder(ctx.ledger.state_dir, "repo")
    return [json.loads(path.read_text(encoding="utf-8"))["tool_input"] for path in base.glob("q-*.json")]


@pytest.mark.parametrize("choice", [queue.ALLOW, queue.DENY])
def test_once_the_hook_has_its_answer_the_call_it_carried_is_not_kept(tmp_path, choice):
    """Directory readiness, p.2 (Software Directory Policy: "must not collect extraneous conversation data, even for
    logging"): a question carries the whole call - a Write's content, an Edit's text - and that was kept a day after
    the answer. The person decided on it; once the hook has the answer, nothing reads it again."""
    ctx = fleet(tmp_path)
    clock = Clock(on_sleep=answer_on_first_sleep(ctx, choice))
    decide.decide(TOUCH, ctx, clock=clock, sleep=clock.sleep, timer=clock)
    assert _kept_inputs(ctx) == [{}]


def test_a_question_nobody_answered_keeps_no_call_either(tmp_path):
    ctx = fleet(tmp_path)
    clock = Clock()
    decide.decide(TOUCH, ctx, clock=clock, sleep=clock.sleep, timer=clock)
    assert _kept_inputs(ctx) == [{}]


def test_a_broker_that_fails_while_waiting_closes_its_question_and_keeps_no_call(tmp_path, monkeypatch):
    ctx = fleet(tmp_path)
    clock = Clock()

    def broken(*args, **kwargs):
        raise OSError("disk went away")
    monkeypatch.setattr(queue, "answer_of", broken)
    decision = decide.decide(TOUCH, ctx, clock=clock, sleep=clock.sleep, timer=clock)
    monkeypatch.undo()
    assert decision["behavior"] == "deny" and "broker failed" in decision["message"]
    assert queue.live(ctx.ledger.state_dir, "repo", now=clock.now) == [] and _kept_inputs(ctx) == [{}]


def test_without_the_opt_in_flotillas_own_commands_are_asked_too(tmp_path, monkeypatch):
    """Directory readiness, the person's choice of 2026-10-03: in ask mode the broker answered "allow" for
    flotilla's own commands without anyone choosing it. The same opt-in that lets them past auto mode's classifier
    now governs it; without it they are put to the person like any other call."""
    monkeypatch.setattr(decide, "_opted_in", lambda root: False)
    payload = {"tool_name": "Bash", "tool_input": {"command": f"{OWN} status"}}
    decision = decide.decide(payload, fleet(tmp_path, orchestrator=False))
    assert decision["behavior"] == "deny" and "no live orchestrator" in decision["message"]


def test_the_broker_asks_the_guards_question_about_the_opt_in(monkeypatch):
    """One answer for both doors: the broker's opt-in is the guard's, read where origin names trunk."""
    from flotilla.guards import run
    asked = []
    monkeypatch.setattr(run, "opt_in_counts", lambda root, **kw: asked.append(root) or True)
    assert decide._opted_in("/w") is True and asked == ["/w"]


def test_a_seat_asking_while_the_census_is_down_is_refused_not_left_hanging(tmp_path):
    """Without the census the broker cannot tell a background seat from the person's session and left the dialog,
    which for a background seat is the measured hang (TODO, broker final review). A question asked from a seat's
    own tree is a seat's."""
    from watchkit import row, rows
    tree = tmp_path / "app-main-1"
    (tree / "src").mkdir(parents=True)
    seat = row("r1", branch="fleet/main-1", owner="main session 1", state="reserved", tree=str(tree))
    ctx = context(tmp_path, me=None, census_error="claude agents timed out", profile=ASK, rows_=rows(seat))
    found = decide.decide({**TOUCH, "cwd": str(tree / "src")}, ctx)
    assert found and found.get("behavior") == "deny" and "census" in found["message"]
    assert decide.decide({**TOUCH, "cwd": str(tmp_path)}, ctx) is None   # the person's checkout: the dialog decides
