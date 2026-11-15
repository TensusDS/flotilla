"""The fleet's work on a model other than its default, noticed (fleet model design 5.5, plan release 4)."""

import datetime as dt
import hashlib
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
    assert override.check("opus", lead_live=False, settings_model="fable", lead_model="fable") == ""


def test_note_when_settings_differ():
    text = override.check("opus", lead_live=True, settings_model="fable", lead_model="")
    assert "the fleet's default model is opus" in text and "own setting" in text and "is fable" in text
    assert "`! flotilla fleet model fable`" in text and "`! flotilla fleet model --mute <hours>`" in text
    assert "`/model opus`" in text and "press `s`" in text


def test_note_when_lead_reports_other():
    text = override.check("opus", lead_live=True, settings_model="", lead_model="fable", lead_name="orchestrator 1")
    assert "orchestrator 1 runs on fable" in text and "own setting" not in text


def test_family_match_is_no_note():
    assert override.check("opus", lead_live=True, settings_model="claude-opus-5-5", lead_model="opus[1m]") == ""


def test_settings_never_written(tmp_path):
    path = settings(tmp_path, {"model": "fable", "theme": "dark"})
    before = (path.read_bytes(), os.stat(path).st_mtime_ns)
    assert override.settings_model(env={}, home=tmp_path) == "fable"
    assert (path.read_bytes(), os.stat(path).st_mtime_ns) == before
    assert override.settings_model(env={"CLAUDE_CONFIG_DIR": str(tmp_path / "none")}, home=tmp_path) == ""
    path.write_text("{not json", encoding="utf-8")
    assert override.settings_model(env={}, home=tmp_path) == ""


def test_note_at_most_hourly(tmp_path):
    text = override.check("opus", lead_live=True, settings_model="fable", lead_model="")
    assert override.due(tmp_path, "repo", override.NOTE, text, now=NOW)
    assert not override.due(tmp_path, "repo", override.NOTE, text, now=NOW + dt.timedelta(minutes=59))
    assert override.due(tmp_path, "repo", override.LINE, text, now=NOW)   # the person's screen has its own clock
    assert override.due(tmp_path, "repo", override.NOTE, text, now=NOW + dt.timedelta(minutes=61))
    other = override.check("opus", lead_live=True, settings_model="sonnet", lead_model="")
    assert override.due(tmp_path, "repo", override.NOTE, other, now=NOW + dt.timedelta(minutes=62))   # it changed
    assert not override.due(tmp_path, "repo", override.NOTE, "", now=NOW + dt.timedelta(hours=5))


def test_mute_holds(tmp_path):
    text = override.check("opus", lead_live=True, settings_model="fable", lead_model="")
    override.mute(tmp_path, "repo", text, hours=3, now=NOW)
    assert not override.due(tmp_path, "repo", override.NOTE, text, now=NOW + dt.timedelta(hours=2))
    assert override.due(tmp_path, "repo", override.NOTE, text, now=NOW + dt.timedelta(hours=3, minutes=1))


def test_mute_ends_when_model_changes(tmp_path):
    text = override.check("opus", lead_live=True, settings_model="fable", lead_model="")
    override.mute(tmp_path, "repo", text, hours=24, now=NOW)
    other = override.check("opus", lead_live=True, settings_model="sonnet", lead_model="")
    assert override.due(tmp_path, "repo", override.NOTE, other, now=NOW + dt.timedelta(minutes=5))


def test_session_models_are_kept_and_bounded(tmp_path):
    override.record_session_model(tmp_path, "sid-1", "fable", now=NOW)
    override.record_session_model(tmp_path, "sid-2", "not a model!", now=NOW)
    assert override.session_model(tmp_path, "sid-1") == "fable" and override.session_model(tmp_path, "sid-2") == ""
    for index in range(override.KEPT_SESSIONS + 5):
        override.record_session_model(tmp_path, f"x{index}", "opus", now=NOW + dt.timedelta(seconds=index))
    assert override.session_model(tmp_path, "sid-1") == ""   # the oldest went first


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
    assert override.current(open_ledger(root), [lead, seat]) == ""
    override.record_session_model(tmp_path / "state", "sid-lead", "fable", now=NOW)
    assert "app-orchestrator 1 runs on fable" in override.current(open_ledger(root), [lead, seat])
    assert override.current(open_ledger(root), [seat]) == ""   # no lead lives: nothing is said


def test_the_settings_model_is_read_from_the_home(tmp_path, monkeypatch):
    from flotilla.ledger.commands import open_ledger
    root, lead = _lead_world(tmp_path, monkeypatch)
    settings(tmp_path / "home", {"model": "fable"})
    text = override.current(open_ledger(root), [lead])
    assert "own setting" in text and "fable" in text


@pytest.mark.parametrize("line", ["fleet model --mute 3", "fleet model --mute=3 --root .", "fleet --root . model --mu 2"])
def test_mute_from_tool_call_refused(tmp_path, monkeypatch, line):
    root = project(tmp_path, monkeypatch)
    answer = ask(root, f"{CLI} {line}", monkeypatch, tmp_path)
    assert answer and answer["permissionDecision"] == "deny" and "--mute" in answer["permissionDecisionReason"]


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
    assert override.session_model(tmp_path / "state", "sid-lead") == "fable"


def test_the_mute_names_what_it_mutes_and_needs_something_to_mute(tmp_path, monkeypatch):
    root, lead = _lead_world(tmp_path, monkeypatch)
    monkeypatch.setattr("flotilla.fleet.commands.census", lambda: [lead])
    code, out = run_cli("fleet", "model", "--mute", "2", "--root", str(root))
    assert code == 0 and "nothing to mute" in out
    settings(tmp_path / "home", {"model": "fable"})
    code, out = run_cli("fleet", "model", "--mute", "2", "--root", str(root))
    assert code == 0 and "muted until" in out and "fable" in out, out
    code, out = run_cli("fleet", "model", "--mute", "0", "--root", str(root))
    assert code == 2 and "1 to 168" in out


def test_the_session_start_keeps_the_model_it_starts_on(tmp_path, monkeypatch):
    root, lead = _lead_world(tmp_path, monkeypatch)
    payload = {"cwd": str(root), "session_id": "sid-new", "hook_event_name": "SessionStart", "source": "startup",
               "model": "claude-fable-5-2"}
    hooks.run_hook("session-start", io.StringIO(json.dumps(payload)), out=io.StringIO())
    assert override.session_model(tmp_path / "state", "sid-new") == "claude-fable-5-2"


def test_the_prompt_hook_tells_the_orchestrator_and_the_person_hourly(tmp_path, monkeypatch):
    from flotilla.ledger.commands import open_ledger
    from flotilla.watch.context import Context
    root, lead = _lead_world(tmp_path, monkeypatch)
    settings(tmp_path / "home", {"model": "fable"})
    ledger = open_ledger(root)
    ctx = Context(root=root, session_id="sid-lead", sessions=[lead], me=lead, ledger=ledger, rows=ledger.rows())
    told, shown = hooks._override(ctx, {"prompt": "how is it going"}, NOW)
    assert "fable" in told and told == shown
    assert hooks._override(ctx, {"prompt": "and now"}, NOW + dt.timedelta(minutes=10)) == ("", "")
    peer = hooks._override(ctx, {"prompt": "<cross-session-message from=x>hi"}, NOW + dt.timedelta(hours=2))
    assert peer[0] and peer[1] == ""   # a peer's message is not the person's prompt: the screen line waits
