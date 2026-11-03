"""The person's attention mode (person inbox design, goal 6): all or digest, set by the person alone, read from
machine.toml, and never silence when the file says nothing usable."""

import io
import json
from types import SimpleNamespace

import pytest

from flotilla import hooks
from flotilla.core import caller
from flotilla.onboard.machine import read_machine, set_person_keys, write_machine
from flotilla.watch import attention
from guardkit import onboarded

CLI = str(hooks.CLI)


def test_no_machine_file_means_all(tmp_path):
    assert attention.mode(tmp_path) == ("all", 30)


def test_an_unreadable_machine_file_means_all(tmp_path):
    (tmp_path / "machine.toml").write_text("attention = [unclosed", encoding="utf-8")
    assert attention.mode(tmp_path) == ("all", 30)


def test_an_unknown_mode_means_all(tmp_path):
    set_person_keys(tmp_path, attention="quiet")
    assert attention.mode(tmp_path) == ("all", 30)


def test_digest_reads_its_period(tmp_path):
    set_person_keys(tmp_path, attention="digest", attention_every=45)
    assert attention.mode(tmp_path) == ("digest", 45)


@pytest.mark.parametrize("every", [0, -5, "soon", 2.5, True])
def test_a_bad_period_falls_back_to_30(tmp_path, every):
    set_person_keys(tmp_path, attention="digest", attention_every=every)
    assert attention.mode(tmp_path) == ("digest", 30)


def run(tmp_path, monkeypatch, refusal="", **given):
    monkeypatch.setenv("FLOTILLA_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(caller, "person_refusal", lambda what: refusal)
    args = SimpleNamespace(**{"action": None, "every": None, **given})
    return attention.run_attention_command(args)


def test_the_person_sets_digest(tmp_path, monkeypatch, capsys):
    assert run(tmp_path, monkeypatch, action="digest", every=20) == 0
    assert (read_machine(tmp_path)["attention"], read_machine(tmp_path)["attention_every"]) == ("digest", 20)
    assert "digest" in capsys.readouterr().out


def test_setting_a_mode_is_refused_when_not_a_person(tmp_path, monkeypatch, capsys):
    assert run(tmp_path, monkeypatch, refusal="`main session 1` is a background session", action="digest") == 2
    assert read_machine(tmp_path) is None
    assert "refused" in capsys.readouterr().out


def test_reading_the_mode_needs_no_person(tmp_path, monkeypatch, capsys):
    assert run(tmp_path, monkeypatch, refusal="not a person") == 0
    assert "all" in capsys.readouterr().out


def test_a_period_needs_digest(tmp_path, monkeypatch):
    assert run(tmp_path, monkeypatch, action="all", every=20) == 2


def test_a_remeasure_keeps_the_mode(tmp_path):
    set_person_keys(tmp_path, attention="digest", attention_every=15)
    write_machine(tmp_path, {"cpus": 4})
    assert attention.mode(tmp_path) == ("digest", 15)


def ask(root, command, monkeypatch, tmp_path):
    monkeypatch.setenv("FLOTILLA_STATE_DIR", str(tmp_path / "state"))
    payload = {"cwd": str(root), "tool_name": "Bash", "tool_input": {"command": command, "description": "d"},
               "permission_mode": "auto", "session_id": "sid"}
    out = io.StringIO()
    assert hooks.run_hook("guard", io.StringIO(json.dumps(payload)), out=out) == 0
    return json.loads(out.getvalue())["hookSpecificOutput"] if out.getvalue() else None


@pytest.mark.parametrize("line", ["attention digest", "attention all", "attention digest --every 10",
                                  "attention --every 10 digest"])
def test_a_tool_call_setting_attention_is_refused(tmp_path, monkeypatch, line):
    answer = ask(onboarded(tmp_path), f"{CLI} {line}", monkeypatch, tmp_path)
    assert answer and answer["permissionDecision"] == "deny" and "person" in answer["permissionDecisionReason"]


def test_reading_the_mode_is_not_refused(tmp_path, monkeypatch):
    answer = ask(onboarded(tmp_path), f"{CLI} attention", monkeypatch, tmp_path)
    assert not answer or answer.get("permissionDecision") != "deny"


def test_a_huge_period_is_capped_at_a_week(tmp_path):
    """Security review of 5f65ad5, I2: a period past what a timedelta holds failed every prompt after the first."""
    set_person_keys(tmp_path, attention="digest", attention_every=10**13)
    assert attention.mode(tmp_path) == ("digest", 7 * 24 * 60)


def test_setting_a_period_past_a_week_is_refused(tmp_path, monkeypatch):
    assert run(tmp_path, monkeypatch, action="digest", every=7 * 24 * 60 + 1) == 2
