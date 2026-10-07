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
                                  "allow-image --for r1", "-- enable --provider vast", "-- disable"])
@pytest.mark.parametrize("word", ["rig", "r?g", "r[i]g", "ri*"])
def test_the_persons_rig_moves_are_refused_to_a_tool_call(tmp_path, monkeypatch, move, word):
    answer = ask(onboarded(tmp_path), f"{CLI} {word} {move}", monkeypatch, tmp_path)
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


@pytest.mark.parametrize("word", ["w?rk", "w[o]rk", "wo*"])
def test_a_globbed_work_still_reaches_approve(tmp_path, monkeypatch, word):
    answer = ask(onboarded(tmp_path), f"{CLI} {word} approve feat/x", monkeypatch, tmp_path)
    assert answer and answer["permissionDecision"] == "deny"


@pytest.mark.parametrize("command", ["{cli} $R open --hours 1 --budget 1", "{cli} `echo rig` open --hours 1",
                                     "{cli} rig $M --hours 1", "{cli} work $M feat/x",
                                     "$F work approve feat/x"])
def test_an_opaque_command_word_is_refused(tmp_path, monkeypatch, command):
    answer = ask(onboarded(tmp_path), command.format(cli=CLI), monkeypatch, tmp_path)
    assert answer and answer["permissionDecision"] == "deny" and "cannot be read" in answer["permissionDecisionReason"]


@pytest.mark.parametrize("command", ["{cli} lane run -- ls * *", "{cli} lane run -- echo approve *",
                                     "{cli} lane run -- echo $HOME", "{cli} work show feat/approve-button",
                                     "$EDITOR work.txt"])
def test_ordinary_globs_after_the_move_are_not_refused(tmp_path, monkeypatch, command):
    answer = ask(onboarded(tmp_path), command.format(cli=CLI), monkeypatch, tmp_path)
    assert not answer or answer.get("permissionDecision") != "deny", answer


def test_the_guard_reads_moves_where_the_parsers_put_them():
    """The guard reads a move as the second word after `flotilla` that is not an option; that holds only while the
    `rig` and `work` parsers take no option of their own before their move (third review of the 2a plan)."""
    import argparse
    from flotilla import cli as flotilla_cli
    parser = flotilla_cli.build_parser()
    commands = next(action for action in parser._actions if isinstance(action, argparse._SubParsersAction))
    for name in ("rig", "work"):
        options = {option for action in commands.choices[name]._actions for option in action.option_strings}
        assert options <= {"-h", "--help"}, (name, options)


@pytest.mark.parametrize("command", ["{cli} r[^x]g open --hours 1 --budget 1", "{cli} rig op[^x]n --hours 1",
                                     "{cli} w[^x]rk approve feat/x", "{cli} work a[^x]prove feat/x"])
def test_a_bash_glob_python_would_read_otherwise_is_refused(tmp_path, monkeypatch, command):
    """`[^x]` is a negation to bash and a literal caret to fnmatch: the guard does not guess what bash expands a glob
    in the command or the move to - it refuses (background scan of the 2a branch)."""
    answer = ask(onboarded(tmp_path), command.format(cli=CLI), monkeypatch, tmp_path)
    assert answer and answer["permissionDecision"] == "deny"
