"""`flotilla fleet model`: the person names the fleet's model (fleet model design 5.2)."""

import io
import json
import tomllib
from contextlib import redirect_stdout

import pytest

from flotilla import cli, hooks
from flotilla.core import caller
from flotilla.fleet import model
from flotilla.onboard.tomlw import render_toml
from flotilla.posts import install_templates
from ledgerkit import commit, git, repo_with_origin

CLI = str(hooks.CLI)
PROFILE = {"schema": 1, "trunk": {"branch": "main"}, "flow": {"mode": "pr"}, "review": {"depth": "every"},
           "permissions": {"mode": "auto"}, "fleet": {"default": {"main": 1}, "model": "one", "name": "app"}}


def run_cli(*args):
    out = io.StringIO()
    with redirect_stdout(out):
        code = cli.main(list(args))
    return code, out.getvalue()


def project(tmp_path, monkeypatch, fleet=None, text=None):
    monkeypatch.setenv("FLOTILLA_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("FLOTILLA_NO_CENSUS", "1")
    monkeypatch.setattr(caller, "person_refusal", lambda what: "")
    root = repo_with_origin(tmp_path)
    (root / ".flotilla").mkdir()
    profile = {**PROFILE, "fleet": fleet or PROFILE["fleet"]}
    (root / ".flotilla" / "project.toml").write_text(text or render_toml(profile), encoding="utf-8")
    install_templates(root)
    git(root, "add", ".flotilla")
    commit(root, "onboard")
    git(root, "push", "-q", "origin", "main")
    return root


def written(root) -> dict:
    return tomllib.loads((root / ".flotilla" / "project.toml").read_text(encoding="utf-8"))


def test_reading_says_the_model_is_not_named(tmp_path, monkeypatch):
    root = project(tmp_path, monkeypatch)
    code, out = run_cli("fleet", "model", "--root", str(root))
    assert code == 0 and "model: not named" in out and "! flotilla fleet model <name>" in out


def test_naming_writes_the_profile_and_the_note(tmp_path, monkeypatch):
    root = project(tmp_path, monkeypatch)
    code, out = run_cli("fleet", "model", "opus", "--root", str(root))
    assert code == 0, out
    assert written(root)["fleet"]["model"] == "opus"
    assert model.note_for(tmp_path / "state", root) == {**model.note_for(tmp_path / "state", root),
                                                       "model": "opus", "replaced": "one"}
    assert "onboard publish" in out
    code, out = run_cli("fleet", "model", "--root", str(root))
    assert "model: opus (from local note (not on trunk yet))" in out


def test_naming_keeps_every_other_key_and_comment(tmp_path, monkeypatch):
    text = ('schema = 1\n# the person\'s own note\n[trunk]\nbranch = "main"\n[flow]\nmode = "pr"\n[review]\n'
            'depth = "every"\n[fleet]\nname = "app"\nmodel = "one"  # inherit\nhelpers_per_seat = 2\n\n'
            '[fleet.default]\nmain = 1\n')
    root = project(tmp_path, monkeypatch, text=text)
    assert run_cli("fleet", "model", "sonnet", "--root", str(root))[0] == 0
    got = (root / ".flotilla" / "project.toml").read_text(encoding="utf-8")
    assert "# the person's own note" in got and 'model = "sonnet"' in got and "inherit" not in got
    assert written(root)["fleet"] == {"name": "app", "model": "sonnet", "helpers_per_seat": 2, "default": {"main": 1}}


def test_a_fleet_table_without_a_model_gets_one(tmp_path, monkeypatch):
    root = project(tmp_path, monkeypatch, fleet={"name": "app", "default": {"main": 1}})
    assert run_cli("fleet", "model", "opus", "--root", str(root))[0] == 0
    assert written(root)["fleet"]["model"] == "opus" and written(root)["fleet"]["default"] == {"main": 1}


def test_reviewer_strongest_becomes_named(tmp_path, monkeypatch):
    root = project(tmp_path, monkeypatch, fleet={"name": "app", "model": "reviewer-strongest"})
    assert run_cli("fleet", "model", "sonnet", "--root", str(root))[0] == 0
    fleet = written(root)["fleet"]
    assert fleet["model"] == "sonnet" and fleet["reviewers"] == "opus"


def test_naming_is_refused_to_a_session_that_is_not_the_person(tmp_path, monkeypatch):
    root = project(tmp_path, monkeypatch)
    monkeypatch.setattr(caller, "person_refusal", lambda what: f"`x` is a background session; only a person {what}")
    code, out = run_cli("fleet", "model", "opus", "--root", str(root))
    assert code == 2 and "only a person names the fleet's model" in out
    assert written(root)["fleet"]["model"] == "one"


def ask(root, command, monkeypatch, tmp_path):
    payload = {"cwd": str(root), "tool_name": "Bash", "tool_input": {"command": command, "description": "d"},
               "permission_mode": "auto", "session_id": "sid"}
    out = io.StringIO()
    assert hooks.run_hook("guard", io.StringIO(json.dumps(payload)), out=out) == 0
    return json.loads(out.getvalue())["hookSpecificOutput"] if out.getvalue() else None


@pytest.mark.parametrize("line", ["fleet model opus", "fleet model opus --root .", "fleet --root . model sonnet"])
def test_a_tool_call_naming_the_model_is_refused(tmp_path, monkeypatch, line):
    root = project(tmp_path, monkeypatch)
    answer = ask(root, f"{CLI} {line}", monkeypatch, tmp_path)
    assert answer and answer["permissionDecision"] == "deny" and "person" in answer["permissionDecisionReason"]


@pytest.mark.parametrize("line", ["fleet model", "fleet model --root .", "fleet"])
def test_reading_the_model_is_not_refused(tmp_path, monkeypatch, line):
    root = project(tmp_path, monkeypatch)
    answer = ask(root, f"{CLI} {line}", monkeypatch, tmp_path)
    assert not answer or answer.get("permissionDecision") != "deny"


def test_wrapped_naming_is_refused(tmp_path, monkeypatch):
    root = project(tmp_path, monkeypatch)
    answer = ask(root, f"bash -c '{CLI} fleet model opus'", monkeypatch, tmp_path)
    assert answer and answer["permissionDecision"] == "deny"


@pytest.mark.parametrize("line", ["{cli} fleet $A opus", "{cli} fleet m*del opus", "{cli} fleet `echo model` opus",
                                  "bash -c '{cli} fleet --root . model opus'"])
def test_a_naming_the_guard_cannot_read_is_refused(tmp_path, monkeypatch, line):   # commit security review
    root = project(tmp_path, monkeypatch)
    answer = ask(root, line.format(cli=CLI), monkeypatch, tmp_path)
    assert answer and answer["permissionDecision"] == "deny", answer


@pytest.mark.parametrize("line", ["{cli} fleet --root . $A opus", "{cli} fleet model --root $A",
                                  "bash -c '{cli} fleet model --json opus'", "bash -c '{cli} fleet model --root . opus'",
                                  "bash -c '{cli} fleet model -- opus'", "echo '{cli} fleet model --root . opus' | bash"])
def test_a_naming_hidden_by_options_or_splitting_is_refused(tmp_path, monkeypatch, line):   # second security review
    root = project(tmp_path, monkeypatch)
    answer = ask(root, line.format(cli=CLI), monkeypatch, tmp_path)
    assert answer and answer["permissionDecision"] == "deny", answer


@pytest.mark.parametrize("line", ["$CLI fleet model opus", 'bash -c "$CLI fleet model opus"',
                                  "~/.local/bin/flot* fleet model opus", "~/.local/bin/flot[i]lla fleet model opus",
                                  "$(ls ~/.local/bin/flot*) fleet model opus"])
def test_a_naming_without_the_word_flotilla_still_reaches_the_guard(tmp_path, monkeypatch, line):   # review I1
    root = project(tmp_path, monkeypatch)
    answer = ask(root, line, monkeypatch, tmp_path)
    assert answer and answer["permissionDecision"] == "deny", answer


def test_the_last_resort_asks_about_a_fleet_line():   # review I1: the guard failing on a model naming
    from flotilla import guards
    assert "fleet" in guards.PERSON_WORDS and hooks.LAST_RESORT_PERSON_WORDS == guards.PERSON_WORDS


def test_a_profile_it_cannot_edit_safely_is_left_alone(tmp_path, monkeypatch):   # review I2
    text = ('fleet = { name = "app", model = "one", default = { main = 1 } }\nschema = 1\n[trunk]\nbranch = "main"\n'
            '[flow]\nmode = "pr"\n[review]\ndepth = "every"\n[permissions]\nmode = "auto"\n')
    root = project(tmp_path, monkeypatch, text=text)
    code, out = run_cli("fleet", "model", "sonnet", "--root", str(root))
    assert code == 2 and 'model = \\"sonnet\\"' not in out and 'model = "sonnet"' in out, out
    assert (root / ".flotilla" / "project.toml").read_text(encoding="utf-8") == text
    assert model.note_for(tmp_path / "state", root) is None


@pytest.mark.parametrize("line", ["python3 -m flotilla.cli fleet model",
                                  "{cli} fleet 2>&1 | python3 -c 'import sys; "
                                  "print(sys.stdin.read().count(\"model\"))'",
                                  "bash -c 'echo the fleet runs one model; {cli} status'"])
def test_a_read_inside_an_interpreter_is_not_refused(tmp_path, monkeypatch, line):   # review M1
    root = project(tmp_path, monkeypatch)
    answer = ask(root, line.format(cli=CLI), monkeypatch, tmp_path)
    assert not answer or answer.get("permissionDecision") != "deny", answer


@pytest.mark.parametrize("line", ["bash -c '{cli} fleet model $X'", "echo opus | xargs {cli} fleet model",
                                  "xargs -a names.txt {cli} fleet model"])
def test_a_name_from_a_variable_or_xargs_is_refused(tmp_path, monkeypatch, line):   # review M1, M2
    root = project(tmp_path, monkeypatch)
    answer = ask(root, line.format(cli=CLI), monkeypatch, tmp_path)
    assert answer and answer["permissionDecision"] == "deny", answer


def test_inherit_is_not_a_models_name(tmp_path, monkeypatch):   # review M7
    root = project(tmp_path, monkeypatch)
    code, out = run_cli("fleet", "model", "inherit", "--root", str(root))
    assert code == 2 and "not a model's name" in out


@pytest.mark.parametrize("action", ["down", "clean", "size"])
def test_a_name_goes_only_with_model(tmp_path, monkeypatch, action):   # review M8
    root = project(tmp_path, monkeypatch, fleet={"name": "app", "model": "opus", "default": {"main": 1}})
    code, out = run_cli("fleet", action, "opus", "--root", str(root))
    assert code == 2 and "only with `fleet model`" in out
