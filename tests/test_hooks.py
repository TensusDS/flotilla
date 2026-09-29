import datetime as dt
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

from flotilla import hooks
from flotilla.watch import fleet
from watchkit import NOW, PR, context, onboarded, row, rows, sess

ROOT = Path(__file__).resolve().parent.parent
ENTRY = ROOT / "scripts" / "flotilla"


def hook(cwd, env_state):
    # Run the entry with THIS interpreter. Through the shebang with a bare PATH, macOS finds its
    # system python3 (3.9.6 on GitHub's macos-latest, 2026-09-23), and the test would measure the
    # interpreter gate instead of the hook.
    return subprocess.run([sys.executable, str(ENTRY), "hook", "session-start"],
                          input=json.dumps({"cwd": str(cwd)}), capture_output=True, text=True,
                          env={"PATH": "/usr/bin:/bin", "FLOTILLA_STATE_DIR": str(env_state)})




def test_garbage_on_stdin_is_treated_as_inactive_elsewhere(tmp_path):
    from flotilla.hooks import run_hook
    out = io.StringIO()
    assert run_hook("session-start", io.StringIO("not json"), out=out) == 0


def test_session_start_reports_broken_config(tmp_path):
    (tmp_path / ".flotilla").mkdir()
    (tmp_path / ".flotilla" / "project.toml").write_text("schema = \n", encoding="utf-8")
    done = hook(tmp_path, tmp_path / "state")
    assert done.returncode == 0
    assert "project.toml" in done.stdout and done.stdout.startswith("flotilla:")



def test_an_internal_error_is_said_not_raised(tmp_path, monkeypatch):
    from flotilla import doctor, hooks
    (tmp_path / ".flotilla").mkdir()
    (tmp_path / ".flotilla" / "project.toml").write_text("schema = 1\n", encoding="utf-8")
    def boom(**kw):
        raise RuntimeError("disk on fire")
    monkeypatch.setattr(doctor, "collect", boom)
    out = io.StringIO()
    assert hooks.run_hook("session-start", io.StringIO(json.dumps({"cwd": str(tmp_path)})), out=out,
                          gather=lambda root, sid: context(tmp_path, me=None)) == 0
    assert "could not check this project" in out.getvalue() and "disk on fire" in out.getvalue()


def test_hook_checks_fit_inside_the_declared_timeout(tmp_path, monkeypatch):
    from flotilla import doctor, hooks
    declared = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]["SessionStart"][0]["hooks"][0]["timeout"]
    (tmp_path / ".flotilla").mkdir()
    (tmp_path / ".flotilla" / "project.toml").write_text("schema = 1\n", encoding="utf-8")
    seen = {}
    def spy(**kw):
        seen.update(kw)
        return []
    monkeypatch.setattr(doctor, "collect", spy)
    hooks.run_hook("session-start", io.StringIO(json.dumps({"cwd": str(tmp_path)})), out=io.StringIO(),
                   gather=lambda root, sid: context(tmp_path, me=None))
    # Three external calls (claude --version and the census in the doctor, the census for the context) plus a
    # margin must finish before the kill.
    assert "timeout" in seen and seen["timeout"] * 3 + 2 <= declared




@pytest.mark.parametrize("event", hooks.EVENTS)
def test_inactive_project_is_silent(tmp_path, event):
    done = subprocess.run([sys.executable, str(ENTRY), "hook", event], input=json.dumps({"cwd": str(tmp_path)}),
                          capture_output=True, text=True,
                          env={"PATH": "/usr/bin:/bin", "FLOTILLA_STATE_DIR": str(tmp_path / "state")})
    assert done.returncode == 0
    assert done.stdout == "" and done.stderr == ""


@pytest.mark.parametrize("event", hooks.EVENTS)
def test_inactive_path_imports_nothing_heavy(tmp_path, event):
    probe = (
        "import io, json, sys\n"
        f"sys.path.insert(0, {str(ROOT)!r})\n"
        "from flotilla.hooks import run_hook\n"
        f"run_hook({event!r}, io.StringIO(json.dumps({{'cwd': {str(tmp_path)!r}}})))\n"
        "heavy = [m for m in ('flotilla.doctor', 'flotilla.core.census', 'flotilla.core.storage',\n"
        "                     'flotilla.watch.context', 'flotilla.ledger.core') if m in sys.modules]\n"
        "print(','.join(heavy))\n"
    )
    done = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, check=True)
    assert done.stdout.strip() == ""


def test_the_cli_accepts_every_hook_event():
    from flotilla import cli
    for event in hooks.EVENTS:
        assert cli.build_parser().parse_args(["hook", event]).event == event


def test_every_hook_event_is_declared():
    declared = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
    commands = {h["command"].rsplit(" ", 1)[-1]: name for name, groups in declared.items()
                for group in groups for h in group["hooks"]}
    assert commands == {"session-start": "SessionStart", "prompt": "UserPromptSubmit", "stop": "Stop",
                        "guard": "PreToolUse", "permission": "PermissionRequest", "ask": "PreToolUse"}


def call(event, tmp_path, ctx, payload=None, now=NOW):
    onboarded(tmp_path)
    out = io.StringIO()
    data = {"cwd": str(tmp_path), "session_id": ctx.session_id, **(payload or {})}
    assert hooks.run_hook(event, io.StringIO(json.dumps(data)), out=out, gather=lambda root, sid: ctx, now=now) == 0
    return out.getvalue()


@pytest.fixture
def healthy(monkeypatch):
    from flotilla import doctor
    monkeypatch.setattr(doctor, "collect", lambda **kw: [doctor.Finding("ok", "python", "3.12")])


HANDED = rows(row(state="handed", reader="review session 1", tip="abc1234def"))
IDLE = {"background_tasks": [], "session_crons": []}


def test_session_start_names_the_session_its_post_and_peers(tmp_path, healthy):
    me = sess("review session 1")
    said = call("session-start", tmp_path, context(tmp_path, me=me, sessions=[me, sess("main session 1")]))
    assert "you are review session 1 (post reviewer); 1 live peer(s) in this project: main session 1" in said
    assert "python" not in said   # a healthy doctor adds no line


def test_session_start_says_what_was_inherited(tmp_path, healthy):
    waiting = rows(row(state="fixing", waiting_on="the person", note="asked about scope"))
    said = call("session-start", tmp_path, context(tmp_path, me=sess("main session 1"), rows_=waiting))
    assert "inherited" in said and "you wait on the person: asked about scope" in said


def test_session_start_reports_a_broken_event_script(tmp_path, healthy):
    ctx = context(tmp_path, me=sess("main session 1"), events={"pre-handed": (b"#!/nonexistent/python\n", True)})
    said = call("session-start", tmp_path, ctx)
    assert "event script pre-handed" in said and "flotilla events check" in said


def test_session_start_says_what_it_could_not_ask(tmp_path, healthy):
    said = call("session-start", tmp_path, context(tmp_path, me=None, census_error="`claude` is not on PATH",
                                                   ledger_error="trunk carries no .flotilla/project.toml"))
    assert "census could not be asked" in said and "ledger could not be read" in said


def test_prompt_names_the_move_and_its_age(tmp_path):
    said = call("prompt", tmp_path, context(tmp_path, me=sess("review session 1"), rows_=HANDED))
    assert "your move" in said and "feat/x: handed to you to read at abc1234" in said and "(2 h)" in said


def test_prompt_is_quiet_when_nothing_changed(tmp_path):
    ctx = context(tmp_path, me=sess("review session 1"), rows_=HANDED)
    assert call("prompt", tmp_path, ctx) != ""
    assert call("prompt", tmp_path, ctx, now=NOW + dt.timedelta(minutes=10)) == ""


def test_prompt_is_silent_with_nothing_to_say(tmp_path):
    assert call("prompt", tmp_path, context(tmp_path, me=sess("main session 1"))) == ""


def test_the_orchestrator_hears_of_dropped_balls_and_others_do_not(tmp_path):
    fleet_now = [sess("orchestrator 1", state="working"), sess("review session 1"),
                 sess("main session 1", state="working")]
    said = call("prompt", tmp_path, context(tmp_path, me=fleet_now[0], sessions=fleet_now, rows_=HANDED))
    assert "the fleet" in said and "review session 1 holds the move" in said
    said = call("prompt", tmp_path, context(tmp_path, me=fleet_now[2], sessions=fleet_now, rows_=HANDED))
    assert "the fleet" not in said


def test_prompt_says_the_census_could_not_be_asked(tmp_path):
    said = call("prompt", tmp_path, context(tmp_path, me=None, census_error="`claude` is not on PATH"))
    assert "could not ask which session this is" in said


def test_stop_blocks_a_background_session_holding_a_move(tmp_path):
    said = call("stop", tmp_path, context(tmp_path, me=sess("review session 1"), rows_=HANDED), payload=IDLE)
    verdict = json.loads(said)
    assert verdict["decision"] == "block" and "feat/x" in verdict["reason"] and "work wait" in verdict["reason"]


def test_stop_lets_a_session_with_a_task_in_flight_stop(tmp_path):
    busy = {"background_tasks": [{"id": "t1", "type": "shell", "status": "running"}], "session_crons": []}
    assert call("stop", tmp_path, context(tmp_path, me=sess("review session 1"), rows_=HANDED), payload=busy) == ""
    cron = {"background_tasks": [], "session_crons": [{"id": "c1", "schedule": "*/5 * * * *"}]}
    assert call("stop", tmp_path, context(tmp_path, me=sess("review session 1"), rows_=HANDED), payload=cron) == ""


def test_stop_lets_through_when_the_registry_is_unreachable(tmp_path):
    assert call("stop", tmp_path, context(tmp_path, me=sess("review session 1"), rows_=HANDED)) == ""


def test_an_interactive_session_is_never_blocked(tmp_path):
    me = sess("review session 1", kind="interactive", status="idle")
    assert call("stop", tmp_path, context(tmp_path, me=me, rows_=HANDED), payload=IDLE) == ""


def test_a_recorded_wait_lets_it_stop(tmp_path):
    waiting = rows(row(state="handed", reader="review session 1", waiting_on="the person", note="asked"))
    assert call("stop", tmp_path, context(tmp_path, me=sess("review session 1"), rows_=waiting), payload=IDLE) == ""


def test_census_down_never_blocks(tmp_path):
    assert call("stop", tmp_path, context(tmp_path, me=None, census_error="down"), payload=IDLE) == ""


def test_the_stop_guard_can_be_turned_off(tmp_path):
    off = {**PR, "watch": {"stop_guard": False}}
    ctx = context(tmp_path, me=sess("review session 1"), rows_=HANDED, profile=off)
    assert call("stop", tmp_path, ctx, payload=IDLE) == ""


def test_a_second_stop_is_recorded_not_blocked(tmp_path):
    ctx = context(tmp_path, me=sess("review session 1"), rows_=HANDED)
    assert call("stop", tmp_path, ctx, payload={**IDLE, "stop_hook_active": True}) == ""
    found = fleet.open_breaks(tmp_path / "state", "repo", HANDED, PR, ctx.post_of)
    assert [(item.kind, item.branch) for item in found] == [("break", "feat/x")]


def test_a_post_row_is_not_a_move_the_guard_holds(tmp_path):
    post_row = rows(row(branch="post/sender", owner="sender 1", state="reserved"))
    me = sess("sender 1")
    assert call("stop", tmp_path, context(tmp_path, me=me, rows_=post_row), payload=IDLE) == ""
    assert call("prompt", tmp_path, context(tmp_path, me=me, rows_=post_row)) == ""


def test_a_session_with_no_post_is_not_asked_for_a_move_it_may_not_make(tmp_path):
    claimed = rows(row(owner="helper 1"))
    assert call("stop", tmp_path, context(tmp_path, me=sess("helper 1"), rows_=claimed), payload=IDLE) == ""


def test_a_post_that_may_not_wait_is_not_blocked(tmp_path):
    from flotilla.posts import Post
    ctx = context(tmp_path, me=sess("helper 1"), rows_=rows(row(owner="helper 1")))
    ctx.ledger.posts["helper"] = Post("helper", "helper {n}", frozenset({"claim", "hand"}), False, 1,
                                      tmp_path / "helper.md", "")
    assert call("stop", tmp_path, ctx, payload=IDLE) == ""


def test_the_permission_hook_answers_a_background_session_with_no_orchestrator(tmp_path):
    me = sess("main session 1")
    ctx = context(tmp_path, me=me, profile={"permissions": {"mode": "ask"}})
    said = call("permission", tmp_path, ctx, payload={"tool_name": "Bash", "tool_input": {"command": "touch x"}})
    decision = json.loads(said)["hookSpecificOutput"]
    assert decision["hookEventName"] == "PermissionRequest" and decision["decision"]["behavior"] == "deny"


def test_the_permission_hook_says_nothing_to_an_interactive_session(tmp_path):
    me = sess("main session 1", kind="interactive", status="busy")
    ctx = context(tmp_path, me=me, profile={"permissions": {"mode": "ask"}})
    assert call("permission", tmp_path, ctx, payload={"tool_name": "Bash", "tool_input": {"command": "x"}}) == ""


def asked(tmp_path, ctx):
    said = call("ask", tmp_path, ctx, payload={"tool_name": "AskUserQuestion"})
    return json.loads(said)["hookSpecificOutput"] if said else {}


def test_a_background_producer_is_refused_and_told_the_route(tmp_path):
    me = sess("minor session 1")
    body = asked(tmp_path, context(tmp_path, me=me, sessions=[me, sess("orchestrator 1")]))
    reason = body["permissionDecisionReason"]
    assert body["permissionDecision"] == "deny"
    assert "orchestrator 1" in reason and "SendMessage" in reason and '--on "the person"' in reason


def test_with_no_orchestrator_alive_the_question_is_still_refused_and_said_how(tmp_path):
    me = sess("sender 1")
    body = asked(tmp_path, context(tmp_path, me=me, sessions=[me]))
    assert body["permissionDecision"] == "deny" and "No orchestrator is alive" in body["permissionDecisionReason"]


def test_the_orchestrator_asks_freely(tmp_path):
    me = sess("orchestrator 1")
    assert asked(tmp_path, context(tmp_path, me=me)) == {}


def test_an_interactive_session_asks_freely(tmp_path):
    me = sess("minor session 1", kind="interactive")
    assert asked(tmp_path, context(tmp_path, me=me)) == {}


def test_a_session_with_no_post_asks_freely(tmp_path):
    me = sess("somebody else")
    assert asked(tmp_path, context(tmp_path, me=me)) == {}


def test_ask_goes_through_with_a_note_when_the_census_is_down(tmp_path):
    body = asked(tmp_path, context(tmp_path, me=None, census_error="`claude` is not on PATH"))
    assert "permissionDecision" not in body and "could not ask the census" in body["additionalContext"]


def test_ask_goes_through_with_a_note_when_the_ledger_is_unreadable(tmp_path):
    me = sess("minor session 1")
    body = asked(tmp_path, context(tmp_path, me=me, ledger_error="trunk carries no .flotilla"))
    assert "permissionDecision" not in body and "could not read the ledger" in body["additionalContext"]


def test_the_ask_hook_is_declared_with_a_budget():
    declared = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]["PreToolUse"]
    group = next(group for group in declared if group.get("matcher") == "AskUserQuestion")
    assert group["hooks"][0]["command"].endswith("hook ask") and group["hooks"][0]["timeout"] >= 3 * 3 + 2


def test_the_route_says_what_to_do_without_a_row_and_how_to_find_one(tmp_path):
    me = sess("minor session 1")
    reason = asked(tmp_path, context(tmp_path, me=me, sessions=[me]))["permissionDecisionReason"]
    assert "flotilla status" in reason and "hold no row" in reason and "last message" in reason


def test_the_greeting_names_this_projects_peers_and_counts_the_rest(tmp_path, healthy):
    import dataclasses
    me = sess("orchestrator 1")
    here = sess("main session 1")
    there = sess("acceptance judge 1")
    ctx = dataclasses.replace(context(tmp_path, me=me, sessions=[me, here, there]), project=[me, here])
    said = call("session-start", tmp_path, ctx)
    assert "1 live peer(s) in this project: main session 1" in said and "1 more elsewhere on this machine" in said


def test_the_ask_guard_names_only_this_projects_orchestrator(tmp_path):
    import dataclasses
    me = sess("minor session 1")
    ctx = context(tmp_path, me=me, sessions=[me, sess("orchestrator 1"), sess("orchestrator 2")])
    ctx = dataclasses.replace(ctx, project=[me, sess("orchestrator 2")])
    reason = asked(tmp_path, ctx)["permissionDecisionReason"]
    assert "orchestrator 2" in reason and "orchestrator 1" not in reason
