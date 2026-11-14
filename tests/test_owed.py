"""Decisions an update owes the person (fleet model design 5.2), the fleet's model first."""

import pytest

from flotilla.onboard import owed
from flotilla.watch import fleet, inbox


def test_fleet_model_owed_until_named(tmp_path):
    for word in ("one", "reviewer-strongest", ""):
        found = owed.owed({"fleet": {"model": word}}, state=tmp_path, main=tmp_path / "p")
        assert [(item.id, item.blocks_spawn, item.answer) for item in found] == [
            ("fleet-model", True, "! flotilla fleet model <name>")]
        assert "no longer" in found[0].question
    assert owed.owed({"fleet": {"model": "opus"}}, state=tmp_path, main=tmp_path / "p") == []


def test_claude_codes_model_is_shown_never_chosen(tmp_path):
    found = owed.owed({"fleet": {"model": "one"}}, state=tmp_path, main=tmp_path / "p", settings_model="fable")
    assert "fable" in found[0].question and "not preselected" in found[0].question


def test_settings_model_reads_and_never_fails(tmp_path):
    (tmp_path / "settings.json").write_text('{"model": "fable", "other": 1}', encoding="utf-8")
    assert owed.settings_model({"CLAUDE_CONFIG_DIR": str(tmp_path)}) == "fable"
    (tmp_path / "settings.json").write_text("{broken", encoding="utf-8")
    assert owed.settings_model({"CLAUDE_CONFIG_DIR": str(tmp_path)}) == ""
    assert owed.settings_model({"CLAUDE_CONFIG_DIR": str(tmp_path / "none")}) == ""


def test_an_owed_decision_reaches_the_orchestrator_and_the_persons_queue(tmp_path):
    found = owed.owed({"fleet": {"model": "one"}}, state=tmp_path, main=tmp_path / "p")
    items = fleet.owed_items(found)
    assert [(item.kind, item.who) for item in items] == [("owed", "fleet-model")]
    assert "! flotilla fleet model <name>" in items[0].text
    entry = inbox._item_entry(items[0])
    assert entry.key == "owed:fleet-model" and entry.group == "question" and entry.urgent and entry.blocker


def test_doctor_reports_the_owed_decision(tmp_path, monkeypatch):
    from flotilla import doctor
    monkeypatch.setenv("FLOTILLA_STATE_DIR", str(tmp_path / "state"))
    found = doctor._owed(tmp_path, profile={"fleet": {"model": "one"}})
    assert [(item.status, item.check) for item in found] == [("warn", "decisions")]
    assert "flotilla fleet model" in found[0].fix
    assert doctor._owed(tmp_path, profile={"fleet": {"model": "opus"}})[0].status == "ok"


def test_the_owed_line_the_orchestrator_reads_holds_the_decision_and_its_answer(tmp_path):   # review I3
    """Lines are cut at 200 characters where they are shown: what to do must come first."""
    import datetime as dt
    from flotilla.watch import render
    found = owed.owed({"fleet": {"model": "one"}}, state=tmp_path, main=tmp_path / "p", settings_model="fable")
    shown = "\n".join(render.lines(fleet.owed_items(found), dt.datetime.now(dt.timezone.utc)))
    assert "! flotilla fleet model <name>" in shown and "fleet's model" in shown and "AskUserQuestion" in shown
    entry = inbox._item_entry(fleet.owed_items(found)[0])
    assert "! flotilla fleet model <name>" in entry.why[:200]


def test_the_question_names_the_models_offered(tmp_path):   # review M6
    found = owed.owed({"fleet": {"model": "one"}}, state=tmp_path, main=tmp_path / "p")
    assert "opus" in found[0].question and "sonnet" in found[0].question
