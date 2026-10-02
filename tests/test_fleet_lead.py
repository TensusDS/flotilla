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


def test_a_lead_is_given_until_it_shows(tmp_path):
    store = LocalLogStore(tmp_path)
    lead.record(store, "sid-1", "worldcore-orchestrator 1", now="t")
    assert lead.pending(store) == {"sid-1": "worldcore-orchestrator 1"}
    assert lead.due(store, "sid-1", current="app-3f") == "worldcore-orchestrator 1"
    assert lead.due(store, "sid-1", current="app-3f") == "worldcore-orchestrator 1"
    assert lead.due(store, "sid-1", current="worldcore-orchestrator 1") == "" and lead.pending(store) == {}
    assert lead.due(store, "sid-other", current="x") == ""


def test_lead_names_this_session_without_a_rename(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch)
    me = _a_session("app-3f", root)
    monkeypatch.setattr("flotilla.fleet.commands.census", lambda: [me])
    monkeypatch.setattr("flotilla.fleet.commands.calling_session", lambda sessions: me)
    monkeypatch.setattr("flotilla.core.caller.person_refusal", lambda what: "")
    code, out = run_cli("spawn", "--lead", "--root", str(root))
    assert code == 0 and "/rename" not in out and "next message" in out
    assert "after that message" in out   # W14: seats raised before the rename learn the old name
    assert lead.pending(LocalLogStore(tmp_path / "state" / "fleet")) == {me.session_id: "orchestrator 1"}


def test_lead_falls_back_to_a_rename_when_it_cannot_tell_which_session_calls(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch)
    monkeypatch.setattr("flotilla.fleet.commands.census", lambda: [])
    monkeypatch.setattr("flotilla.fleet.commands.calling_session", lambda sessions: None)
    monkeypatch.setattr("flotilla.core.caller.person_refusal", lambda what: "")
    code, out = run_cli("spawn", "--lead", "--root", str(root))
    assert code == 0 and "/rename orchestrator 1" in out and "then send any message" in out


def _prompt(tmp_path, sid, monkeypatch, me="app-3f", rows_=None, now=None):
    monkeypatch.setenv("FLOTILLA_STATE_DIR", str(tmp_path / "state"))
    watch_onboarded(tmp_path)
    out = io.StringIO()
    payload = {"cwd": str(tmp_path), "session_id": sid, "prompt": "build the orbit tests"}
    ctx = context(tmp_path, me=sess(me), rows_=rows_)
    assert hooks.run_hook("prompt", io.StringIO(json.dumps(payload)), out=out, gather=lambda root, s: ctx,
                          now=now) == 0
    return out.getvalue()


def _store(tmp_path):
    return LocalLogStore(tmp_path / "state" / "fleet")


def test_the_session_is_named_until_the_census_shows_the_name(tmp_path, monkeypatch):
    """Review of 0.6.0, I4: the lead was marked given before Claude Code had applied it, and nothing read it back.
    Now the hook keeps naming the session until the census lists it under that name."""
    lead.record(_store(tmp_path), "sid-me", "worldcore-orchestrator 1", now="t")
    said = json.loads(_prompt(tmp_path, "sid-me", monkeypatch))["hookSpecificOutput"]
    assert said["hookEventName"] == "UserPromptSubmit" and said["sessionTitle"] == "worldcore-orchestrator 1"
    assert "orchestrator" in said["additionalContext"]
    assert "flotilla spawn --fill" in said["additionalContext"]   # W14: the rest is raised once the name shows
    assert "sessionTitle" in _prompt(tmp_path, "sid-me", monkeypatch)                  # not applied yet: again
    assert "sessionTitle" not in _prompt(tmp_path, "sid-me", monkeypatch, me="worldcore-orchestrator 1")
    assert lead.pending(_store(tmp_path)) == {}


def test_the_lead_keeps_what_the_hook_had_to_say(tmp_path, monkeypatch):
    from watchkit import row, rows
    lead.record(_store(tmp_path), "sid-me", "worldcore-orchestrator 1", now="t")
    handed = rows(row(state="handed", reader="app-3f", tip="abc1234def"))
    said = json.loads(_prompt(tmp_path, "sid-me", monkeypatch, rows_=handed))["hookSpecificOutput"]
    assert "your move" in said["additionalContext"]


def test_a_lead_left_a_day_is_forgotten(tmp_path, monkeypatch):
    import datetime as dt
    from watchkit import NOW
    old = (NOW - dt.timedelta(hours=25)).isoformat()
    lead.record(_store(tmp_path), "sid-me", "worldcore-orchestrator 1", now=old)
    assert "sessionTitle" not in _prompt(tmp_path, "sid-me", monkeypatch, now=NOW)


def test_another_sessions_prompt_takes_no_name(tmp_path, monkeypatch):
    lead.record(_store(tmp_path), "sid-me", "worldcore-orchestrator 1", now="t")
    assert "sessionTitle" not in _prompt(tmp_path, "sid-someone", monkeypatch)
    assert lead.pending(_store(tmp_path)) == {"sid-me": "worldcore-orchestrator 1"}


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


def test_fill_does_not_count_a_lead_whose_session_is_gone(tmp_path, monkeypatch):
    """Review of 0.6.0, M6: nothing pinned that a dead session's lead stops holding the orchestrator's seat."""
    root = _onboarded_with(tmp_path, monkeypatch, {"orchestrator": 1, "main": 1})
    monkeypatch.setattr("flotilla.fleet.commands.census", lambda: [_a_session("app-3f", root)])
    lead.record(LocalLogStore(tmp_path / "state" / "fleet"), "sid-gone", "orchestrator 1", now="t")
    code, out = run_cli("spawn", "--fill", "--dry-run", "--root", str(root))
    assert code == 0 and "(orchestrator)" in out, out


def test_fleet_lists_the_leading_session(tmp_path, monkeypatch):
    """Worldcore field test W15: `flotilla fleet` listed only spawned seats, so the orchestrator - the person's own
    session - concluded it was missing from the fleet and read flotilla's source to find out why."""
    root = _onboarded_with(tmp_path, monkeypatch, {"orchestrator": 1, "main": 1})
    unnamed = _a_session("app-3f", root)
    monkeypatch.setattr("flotilla.fleet.commands.census", lambda: [unnamed])
    code, out = run_cli("fleet", "--root", str(root))
    assert code == 0 and "leads the fleet" not in out
    lead.record(LocalLogStore(tmp_path / "state" / "fleet"), unnamed.session_id, "orchestrator 1", now="t")
    code, out = run_cli("fleet", "--root", str(root))
    assert code == 0 and "app-3f (orchestrator)" in out and "leads the fleet" in out and "orchestrator 1" in out
    named = _a_session("orchestrator 1", root)
    monkeypatch.setattr("flotilla.fleet.commands.census", lambda: [named])
    code, out = run_cli("fleet", "--root", str(root))
    assert code == 0 and "orchestrator 1 (orchestrator)" in out and "leads the fleet" in out


def test_a_title_the_census_never_shows_turns_into_a_rename(tmp_path, monkeypatch):
    """The hook's title is measured only on Claude Code 2.1.287. A version that ignores it would leave the session
    unnamed and silently stop counting as the orchestrator a day later; so after a few messages the session is told
    to ask the person for `/rename`, and the title keeps being offered meanwhile."""
    lead.record(_store(tmp_path), "sid-me", "worldcore-orchestrator 1", now="t")
    said = [json.loads(_prompt(tmp_path, "sid-me", monkeypatch))["hookSpecificOutput"]
            for _ in range(lead.ASK_AFTER + 1)]
    assert all(s["sessionTitle"] == "worldcore-orchestrator 1" for s in said)
    assert not any("/rename" in s["additionalContext"] for s in said[:lead.ASK_AFTER])
    assert "/rename worldcore-orchestrator 1" in said[lead.ASK_AFTER]["additionalContext"]


def test_offers_are_counted_per_session(tmp_path):
    store = LocalLogStore(tmp_path)
    lead.record(store, "sid-1", "a", now="t")
    lead.record(store, "sid-2", "b", now="t")
    lead.due(store, "sid-1", current="x")
    lead.due(store, "sid-1", current="x")
    lead.due(store, "sid-2", current="x")
    assert lead.offered(store, "sid-1") == 2 and lead.offered(store, "sid-2") == 1
    lead.record(store, "sid-1", "a2", now="t")            # a new lead starts its own count
    assert lead.offered(store, "sid-1") == 0
