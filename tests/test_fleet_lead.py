"""The person's own session leads the fleet without being asked to rename itself (worldcore field test W9 and the
person's remark, 2026-10-01: "it asks to rename itself - that will read as a bug"). `/rename` is a command only the
person can type, and it does not wake the session; a UserPromptSubmit hook can set the session's title instead
(measured on Claude Code 2.1.287: the census then lists the session under that name)."""

import io
import json

from flotilla import hooks
from flotilla.core.storage import LocalLogStore
from flotilla.fleet import lead
from test_fleet_cli import _a_session, onboarded, run_cli
from watchkit import context, onboarded as watch_onboarded, sess


def test_a_lead_is_taken_once(tmp_path):
    store = LocalLogStore(tmp_path)
    lead.record(store, "sid-1", "worldcore-orchestrator 1", now="t")
    assert lead.pending(store) == {"sid-1": "worldcore-orchestrator 1"}
    assert lead.take(store, "sid-1") == "worldcore-orchestrator 1"
    assert lead.take(store, "sid-1") == "" and lead.pending(store) == {}
    assert lead.take(store, "sid-other") == ""


def test_lead_names_this_session_without_a_rename(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch)
    me = _a_session("app-3f", root)
    monkeypatch.setattr("flotilla.fleet.commands.census", lambda: [me])
    monkeypatch.setattr("flotilla.fleet.commands.calling_session", lambda sessions: me)
    monkeypatch.setattr("flotilla.core.caller.person_refusal", lambda what: "")
    code, out = run_cli("spawn", "--lead", "--root", str(root))
    assert code == 0 and "/rename" not in out and "next message" in out
    assert lead.pending(LocalLogStore(tmp_path / "state" / "fleet")) == {me.session_id: "orchestrator 1"}


def test_lead_falls_back_to_a_rename_when_it_cannot_tell_which_session_calls(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch)
    monkeypatch.setattr("flotilla.fleet.commands.census", lambda: [])
    monkeypatch.setattr("flotilla.fleet.commands.calling_session", lambda sessions: None)
    monkeypatch.setattr("flotilla.core.caller.person_refusal", lambda what: "")
    code, out = run_cli("spawn", "--lead", "--root", str(root))
    assert code == 0 and "/rename orchestrator 1" in out and "then send any message" in out


def _prompt(tmp_path, sid, monkeypatch):
    monkeypatch.setenv("FLOTILLA_STATE_DIR", str(tmp_path / "state"))
    watch_onboarded(tmp_path)
    out = io.StringIO()
    payload = {"cwd": str(tmp_path), "session_id": sid, "prompt": "build the orbit tests"}
    ctx = context(tmp_path, me=sess("app-3f"))
    assert hooks.run_hook("prompt", io.StringIO(json.dumps(payload)), out=out, gather=lambda root, s: ctx) == 0
    return out.getvalue()


def test_the_next_prompt_gives_the_session_its_name_once(tmp_path, monkeypatch):
    lead.record(LocalLogStore(tmp_path / "state" / "fleet"), "sid-me", "worldcore-orchestrator 1", now="t")
    said = json.loads(_prompt(tmp_path, "sid-me", monkeypatch))["hookSpecificOutput"]
    assert said["hookEventName"] == "UserPromptSubmit" and said["sessionTitle"] == "worldcore-orchestrator 1"
    assert "orchestrator" in said["additionalContext"]
    assert "sessionTitle" not in _prompt(tmp_path, "sid-me", monkeypatch)


def test_another_sessions_prompt_takes_no_name(tmp_path, monkeypatch):
    lead.record(LocalLogStore(tmp_path / "state" / "fleet"), "sid-me", "worldcore-orchestrator 1", now="t")
    assert "sessionTitle" not in _prompt(tmp_path, "sid-someone", monkeypatch)
    assert lead.pending(LocalLogStore(tmp_path / "state" / "fleet")) == {"sid-me": "worldcore-orchestrator 1"}


def _onboarded_with(tmp_path, monkeypatch, default):
    from flotilla.onboard.tomlw import render_toml
    from flotilla.posts import install_templates
    from ledgerkit import commit, git, repo_with_origin
    from test_fleet_cli import PROFILE
    monkeypatch.setenv("FLOTILLA_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("FLOTILLA_NO_CENSUS", "1")
    root = repo_with_origin(tmp_path)
    (root / ".flotilla").mkdir()
    profile = {**PROFILE, "fleet": {"default": default, "model": "one"}}
    (root / ".flotilla" / "project.toml").write_text(render_toml(profile), encoding="utf-8")
    install_templates(root)
    git(root, "add", ".flotilla")
    commit(root, "onboard")
    git(root, "push", "-q", "origin", "main")
    return root


def test_fill_counts_a_leading_session_before_its_name_shows(tmp_path, monkeypatch):
    """Between --lead and the person's next message the session still carries its old name; --fill must not raise
    a second orchestrator in that gap."""
    root = _onboarded_with(tmp_path, monkeypatch, {"orchestrator": 1, "main": 1})
    me = _a_session("app-3f", root)
    monkeypatch.setattr("flotilla.fleet.commands.census", lambda: [me])
    code, out = run_cli("spawn", "--fill", "--dry-run", "--root", str(root))
    assert code == 0 and "(orchestrator)" in out, out          # without the lead, the gap would raise one
    lead.record(LocalLogStore(tmp_path / "state" / "fleet"), me.session_id, "orchestrator 1", now="t")
    code, out = run_cli("spawn", "--fill", "--dry-run", "--root", str(root))
    assert code == 0 and "(orchestrator)" not in out and "(main)" in out, out
