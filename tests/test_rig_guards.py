import io
import json

import pytest

from flotilla import hooks
from guardkit import onboarded

CLI = str(hooks.CLI)


def ask(root, command, monkeypatch, tmp_path):
    monkeypatch.setenv("FLOTILLA_STATE_DIR", str(tmp_path / "state"))
    payload = {"cwd": str(root), "tool_name": "Bash", "tool_input": {"command": command, "description": "d"},
               "permission_mode": "auto", "session_id": "sid"}
    out = io.StringIO()
    assert hooks.run_hook("guard", io.StringIO(json.dumps(payload)), out=out) == 0
    return json.loads(out.getvalue())["hookSpecificOutput"] if out.getvalue() else None


@pytest.mark.parametrize("move", ["enable --provider vast", "disable", "open --hours 3 --budget 2", "close",
                                  "-- enable --provider vast", "-- disable"])
def test_the_persons_rig_moves_are_refused_to_a_tool_call(tmp_path, monkeypatch, move):
    answer = ask(onboarded(tmp_path), f"{CLI} rig {move}", monkeypatch, tmp_path)
    assert answer and answer["permissionDecision"] == "deny" and "person" in answer["permissionDecisionReason"]


def test_reading_the_rig_stays_open_to_a_tool_call(tmp_path, monkeypatch):
    root = onboarded(tmp_path)
    for command in (f"{CLI} rig", f"{CLI} rig status", f"{CLI} rig reap"):
        answer = ask(root, command, monkeypatch, tmp_path)
        assert not answer or answer.get("permissionDecision") != "deny", (command, answer)


def edit(tool, path, monkeypatch, tmp_path):
    monkeypatch.setenv("FLOTILLA_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    field = "notebook_path" if tool == "NotebookEdit" else "file_path"
    payload = {"cwd": str(tmp_path), "tool_name": tool, "tool_input": {field: str(path)}, "session_id": "sid"}
    out = io.StringIO()
    assert hooks.run_hook("edit", io.StringIO(json.dumps(payload)), out=out) == 0
    return json.loads(out.getvalue())["hookSpecificOutput"] if out.getvalue() else None


@pytest.mark.parametrize("tool", ["Edit", "Write", "MultiEdit", "NotebookEdit"])
@pytest.mark.parametrize("where", ["machine.toml", "rig/rig.jsonl", "rig/reaper.py", "rig/machine-key", "KEY"])
def test_the_persons_files_are_refused_to_edit_tools(tmp_path, monkeypatch, tool, where):
    target = tmp_path / "config" / "flotilla" / "rig" / "vast.key" if where == "KEY" else tmp_path / "state" / where
    answer = edit(tool, target, monkeypatch, tmp_path)
    assert answer and answer["permissionDecision"] == "deny" and "person" in answer["permissionDecisionReason"]


def test_a_symlink_to_a_protected_file_is_refused_too(tmp_path, monkeypatch):
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "machine.toml").write_text("rig = \"off\"\n")
    link = tmp_path / "harmless.toml"
    link.symlink_to(tmp_path / "state" / "machine.toml")
    answer = edit("Write", link, monkeypatch, tmp_path)
    assert answer and answer["permissionDecision"] == "deny"


def test_ordinary_files_are_left_alone_everywhere(tmp_path, monkeypatch):
    for target in (tmp_path / "src" / "app.py", tmp_path / "state" / "lane" / "lane.jsonl", tmp_path / "README.md"):
        assert edit("Edit", target, monkeypatch, tmp_path) is None


def test_a_malformed_edit_payload_is_never_a_crash():
    out = io.StringIO()
    assert hooks.run_hook("edit", io.StringIO("not json"), out=out) == 0 and out.getvalue() == ""
