"""The fleet's work on a model other than its default, noticed (fleet model design 5.5, plan release 4)."""

import datetime as dt
import io
import json
import os

import pytest

from flotilla import hooks
from flotilla.core.census import Session
from flotilla.fleet import override
from test_fleet_model_cmd import CLI, ask, project, run_cli

NOW = dt.datetime(2026, 11, 15, 12, 0, tzinfo=dt.timezone.utc)


def settings(home, data):
    path = home / ".claude" / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_no_note_without_live_lead():
    assert override.check("opus", lead_live=False, settings_model="fable", lead_models=["fable"]) == override.Notice()


def test_note_when_settings_differ():
    notice = override.check("opus", lead_live=True, settings_model="fable")
    assert "the fleet's default model is opus" in notice.text and "own setting" in notice.text
    assert "`! flotilla fleet model fable`" in notice.text and "`! flotilla fleet model --mute <hours>`" in notice.text
    assert "`/model opus` in any session" in notice.text and "press `s`" not in notice.text   # review of 78c8be7, 3
    assert notice.what == "fable"


def test_note_when_lead_reports_other():
    notice = override.check("opus", lead_live=True, lead_models=["opus", "fable"], lead_name="orchestrator 1")
    assert "orchestrator 1 runs on fable" in notice.text and "own setting" not in notice.text
    assert "`/model opus` in orchestrator 1's session (press `s`" in notice.text


def test_family_match_is_no_note():
    assert override.check("opus", lead_live=True, settings_model="claude-opus-5-5",
                          lead_models=["opus[1m]"]) == override.Notice()


def test_a_post_with_its_own_model_is_compared_with_it():   # review of 78c8be7, 8
    assert override.check("opus", lead_live=True, lead_models=["sonnet"], lead_expected="sonnet") == override.Notice()
    assert "runs on opus" in override.check("opus", lead_live=True, lead_models=["opus"], lead_expected="sonnet").text


def test_settings_never_written(tmp_path):
    path = settings(tmp_path, {"model": "fable", "theme": "dark"})
    before = (path.read_bytes(), os.stat(path).st_mtime_ns)
    assert override.settings_model(env={}, home=tmp_path) == "fable"
    assert (path.read_bytes(), os.stat(path).st_mtime_ns) == before
    assert override.settings_model(env={"CLAUDE_CONFIG_DIR": str(tmp_path / "none")}, home=tmp_path) == ""
    settings(tmp_path, {"model": "default"})   # an alias that moves with Claude Code: no model (review, 9)
    assert override.settings_model(env={}, home=tmp_path) == ""
    path.write_text("{not json", encoding="utf-8")
    assert override.settings_model(env={}, home=tmp_path) == ""


def test_note_at_most_hourly(tmp_path):
    assert override.observe(tmp_path, "repo", "fable", now=NOW, note=True) == (False, True)
    assert override.observe(tmp_path, "repo", "fable", now=NOW + dt.timedelta(minutes=59), note=True) == (False, False)
    assert override.observe(tmp_path, "repo", "fable", now=NOW + dt.timedelta(minutes=61), note=True) == (False, True)
    assert override.observe(tmp_path, "repo", "sonnet", now=NOW + dt.timedelta(minutes=62), note=True) == (False, True)


def test_mute_holds(tmp_path):
    override.mute(tmp_path, "repo", "fable", hours=3, now=NOW)
    assert override.observe(tmp_path, "repo", "fable", now=NOW + dt.timedelta(hours=2), note=True) == (True, False)
    assert override.observe(tmp_path, "repo", "fable", now=NOW + dt.timedelta(hours=3, minutes=1),
                            note=True) == (False, True)


def test_mute_ends_when_model_changes(tmp_path):
    override.mute(tmp_path, "repo", "fable", hours=24, now=NOW)
    assert override.observe(tmp_path, "repo", "sonnet", now=NOW + dt.timedelta(minutes=5), note=True) == (False, True)
    assert override.observe(tmp_path, "repo", "fable", now=NOW + dt.timedelta(minutes=6), note=True) == (False, True)


def test_a_mute_is_gone_once_nothing_differs(tmp_path):   # review of 78c8be7, 2
    override.mute(tmp_path, "repo", "fable", hours=168, now=NOW)
    assert override.observe(tmp_path, "repo", "", now=NOW + dt.timedelta(hours=1), note=True) == (False, False)
    later = NOW + dt.timedelta(days=2)   # a new /model fable for one task, saved as Claude Code's default
    assert override.observe(tmp_path, "repo", "fable", now=later, note=True) == (False, True)


def test_the_mute_is_keyed_on_models_not_on_who():   # review of 78c8be7, 4
    a = override.check("opus", lead_live=True, settings_model="fable")
    b = override.check("opus", lead_live=True, settings_model="fable", lead_models=["fable"], lead_name="orch 2")
    assert a.what == b.what == "fable"


def test_session_models_keep_start_and_word(tmp_path):   # review of 78c8be7, 5
    override.record_session_model(tmp_path, "sid-1", "fable", now=NOW)
    override.record_session_model(tmp_path, "sid-1", "opus", now=NOW, said=True)
    override.record_session_model(tmp_path, "sid-2", {"id": "x"}, now=NOW)   # not a string: nothing recorded
    assert override.session_models(tmp_path, "sid-1") == ["fable", "opus"]
    assert override.session_models(tmp_path, "sid-2") == []


def test_session_models_are_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(override, "KEPT_SESSIONS", 5)
    override.record_session_model(tmp_path, "old", "fable", now=NOW)
    for index in range(6):
        override.record_session_model(tmp_path, f"x{index}", "opus", now=NOW + dt.timedelta(seconds=index + 1))
    assert override.session_models(tmp_path, "old") == []


def _lead_world(tmp_path, monkeypatch, lead_kind="interactive"):
    root = project(tmp_path, monkeypatch, fleet={"name": "app", "model": "opus", "default": {"main": 1}})
    monkeypatch.setenv("HOME", str(tmp_path / "home"))   # Claude Code's settings are a test file here
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    lead = Session(name="app-orchestrator 1", session_id="sid-lead", kind=lead_kind, pid=None, short_id="lead01",
                   status="idle", state=None, cwd=str(root), started_at_ms=None)
    return root, lead


def test_borrowed_seat_is_not_an_override(tmp_path, monkeypatch):
    """A seat on a borrowed model is counted on the person's line, never noted as the fleet's work off its model."""
    from flotilla.ledger.commands import open_ledger
    root, lead = _lead_world(tmp_path, monkeypatch)
    seat = Session(name="app-main session 4", session_id="sid-seat", kind="background", pid=None, short_id="seat04",
                   status="idle", state="idle", cwd=str(root), started_at_ms=None)
    override.record_session_model(tmp_path / "state", "sid-seat", "fable", now=NOW)
    override.record_session_model(tmp_path / "state", "sid-lead", "opus", now=NOW)
    assert override.current(open_ledger(root), [lead, seat]) == override.Notice()
    override.record_session_model(tmp_path / "state", "sid-lead", "fable", now=NOW, said=True)
    assert "app-orchestrator 1 runs on fable" in override.current(open_ledger(root), [lead, seat]).text
    assert override.current(open_ledger(root), [seat]) == override.Notice()   # no lead lives: nothing is said


def test_the_settings_model_is_read_from_the_home(tmp_path, monkeypatch):
    from flotilla.ledger.commands import open_ledger
    root, lead = _lead_world(tmp_path, monkeypatch)
    settings(tmp_path / "home", {"model": "fable"})
    notice = override.current(open_ledger(root), [lead])
    assert "own setting" in notice.text and notice.what == "fable"


@pytest.mark.parametrize("line", ["fleet model --mute 3", "fleet model --mute=3 --root .", "fleet --root . model --mu 2",
                                  "fleet model --m 3", "bash -c '{cli} fleet model --mute 3'"])
def test_mute_from_tool_call_refused(tmp_path, monkeypatch, line):
    root = project(tmp_path, monkeypatch)
    command = line.format(cli=CLI) if line.startswith("bash") else f"{CLI} {line}"
    answer = ask(root, command, monkeypatch, tmp_path)
    assert answer and answer["permissionDecision"] == "deny"   # wrapped: refused as code handed to an interpreter
    assert line.startswith("bash") or "--mute" in answer["permissionDecisionReason"]


def test_the_mute_is_refused_to_a_background_session(tmp_path, monkeypatch):
    root, lead = _lead_world(tmp_path, monkeypatch)
    settings(tmp_path / "home", {"model": "fable"})
    monkeypatch.setattr("flotilla.fleet.commands.census", lambda: [lead])
    monkeypatch.setattr("flotilla.core.caller.person_refusal",
                        lambda what: f"`app-main session 2` is a background session; only a person {what}")
    code, out = run_cli("fleet", "model", "--mute", "2", "--root", str(root))
    assert code == 2 and "background session" in out


def test_saying_the_model_one_runs_on_is_not_refused(tmp_path, monkeypatch):
    root = project(tmp_path, monkeypatch)
    answer = ask(root, f"{CLI} fleet model --running fable", monkeypatch, tmp_path)
    assert not answer or answer.get("permissionDecision") != "deny"


def test_running_records_the_calling_sessions_model(tmp_path, monkeypatch):
    root, lead = _lead_world(tmp_path, monkeypatch)
    monkeypatch.setattr("flotilla.fleet.commands.census", lambda: [lead])
    monkeypatch.setattr("flotilla.fleet.commands.calling_session", lambda sessions: lead)
    code, out = run_cli("fleet", "model", "--running", "fable", "--root", str(root))
    assert code == 0 and "app-orchestrator 1 runs on fable" in out, out
    assert override.session_models(tmp_path / "state", "sid-lead") == ["fable"]


def test_the_mute_names_what_it_mutes_and_needs_something_to_mute(tmp_path, monkeypatch):
    root, lead = _lead_world(tmp_path, monkeypatch)
    monkeypatch.setattr("flotilla.fleet.commands.census", lambda: [lead])
    code, out = run_cli("fleet", "model", "--mute", "2", "--root", str(root))
    assert code == 0 and "nothing to mute" in out
    settings(tmp_path / "home", {"model": "fable"})
    code, out = run_cli("fleet", "model", "--mute", "2", "--root", str(root))
    assert code == 0 and "muted until" in out and "(fable)" in out, out
    code, out = run_cli("fleet", "model", "--mute", "0", "--root", str(root))
    assert code == 2 and "1 to 168" in out


def test_the_session_start_keeps_the_model_it_starts_on(tmp_path, monkeypatch):
    root, lead = _lead_world(tmp_path, monkeypatch)
    payload = {"cwd": str(root), "session_id": "sid-new", "hook_event_name": "SessionStart", "source": "startup",
               "model": "claude-fable-5-2"}
    hooks.run_hook("session-start", io.StringIO(json.dumps(payload)), out=io.StringIO())
    assert override.session_models(tmp_path / "state", "sid-new") == ["claude-fable-5-2"]


def _prompt(root, lead, prompt, monkeypatch, now):
    """The prompt hook's output for the lead's session, the census answering with it."""
    from flotilla.watch import context
    monkeypatch.setattr(hooks, "_now", lambda: now, raising=False)
    gathered = context.gather
    monkeypatch.setattr(context, "gather", lambda *a, **kw: gathered(*a, **{**kw, "census": lambda: [lead],
                                                                          "parent_of": lambda pid: None}))
    payload = {"cwd": str(root), "session_id": "sid-lead", "hook_event_name": "UserPromptSubmit", "prompt": prompt,
               "prompt_id": f"p-{prompt[:8]}"}
    out = io.StringIO()
    hooks.run_hook("prompt", io.StringIO(json.dumps(payload)), out=out)
    return json.loads(out.getvalue()) if out.getvalue().strip().startswith("{") else {}


def test_the_prompt_hook_tells_the_orchestrator_and_carries_it_on_the_persons_line(tmp_path, monkeypatch):
    from flotilla.ledger.commands import open_ledger
    from flotilla.watch.context import Context
    root, lead = _lead_world(tmp_path, monkeypatch)
    settings(tmp_path / "home", {"model": "fable"})
    ledger = open_ledger(root)
    ctx = Context(root=root, session_id="sid-lead", sessions=[lead], me=lead, ledger=ledger, rows=ledger.rows())
    told, notice = hooks._override(ctx, {"prompt": "how is it going"}, NOW)
    assert "fable" in told and notice.what == "fable"
    told, notice = hooks._override(ctx, {"prompt": "and now"}, NOW + dt.timedelta(minutes=10))
    assert told == "" and notice.what == "fable"   # the note is hourly; the line keeps it, by its own rule
    line = hooks._persons_line(ctx, {"prompt": "and now", "prompt_id": "p1"}, [], NOW, notice)
    assert line.startswith("flotilla: the fleet's default model is opus")
    override.mute(tmp_path / "state", ledger.repo_key, "fable", hours=2, now=NOW)
    assert hooks._override(ctx, {"prompt": "x"}, NOW + dt.timedelta(hours=1, minutes=30))[1] == override.Notice()


def test_a_seats_own_prompt_pays_for_no_notice(tmp_path, monkeypatch):
    from flotilla.ledger.commands import open_ledger
    from flotilla.watch.context import Context
    root, lead = _lead_world(tmp_path, monkeypatch)
    seat = Session(name="app-main session 4", session_id="sid-seat", kind="background", pid=None, short_id="seat04",
                   status="idle", state="idle", cwd=str(root), started_at_ms=None)
    settings(tmp_path / "home", {"model": "fable"})
    ledger = open_ledger(root)
    ctx = Context(root=root, session_id="sid-seat", sessions=[lead, seat], me=seat, ledger=ledger, rows=ledger.rows())
    assert hooks._override(ctx, {"prompt": "go on"}, NOW) == ("", override.Notice())
