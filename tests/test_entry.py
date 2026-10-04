import ast
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENTRY = ROOT / "bin" / "flotilla"


def run(entry, *args, cwd=None):
    return subprocess.run([sys.executable, str(entry), *args], capture_output=True, text=True, cwd=cwd)


def test_entry_parses_with_an_old_grammar():
    # An old python3 must reach the version message, not a SyntaxError.
    ast.parse(ENTRY.read_text(encoding="utf-8"), feature_version=(3, 7))


def test_entry_is_executable():
    assert os.access(ENTRY, os.X_OK)


def test_version_prints_the_package_version():
    from flotilla import __version__
    done = run(ENTRY, "version")
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == __version__


def test_entry_works_from_any_directory_and_through_a_symlink(tmp_path):
    link = tmp_path / "flotilla"
    link.symlink_to(ENTRY)
    done = run(link, "version", cwd=tmp_path)
    assert done.returncode == 0, done.stderr


def test_old_interpreter_gets_a_message_not_a_traceback(tmp_path):
    source = ENTRY.read_text(encoding="utf-8").replace("MINIMUM = (3, 11)", "MINIMUM = (99, 0)")
    assert "MINIMUM = (99, 0)" in source, "the gate constant moved; update this test"
    fake = tmp_path / "flotilla"
    fake.write_text(source, encoding="utf-8")
    done = run(fake, "version")
    assert done.returncode == 3
    assert "flotilla needs Python 99.0 or newer" in done.stderr
    assert "Traceback" not in done.stderr


def test_unknown_subcommand_is_a_usage_error():
    done = run(ENTRY, "no-such-command")
    assert done.returncode == 2
    # Python also exits 2 when the script is missing; only argparse says "invalid choice".
    assert "invalid choice" in done.stderr


def run_old_hook(tmp_path, cwd):
    source = ENTRY.read_text(encoding="utf-8").replace("MINIMUM = (3, 11)", "MINIMUM = (99, 0)")
    fake = tmp_path / "fake-flotilla"
    fake.write_text(source, encoding="utf-8")
    import json
    return subprocess.run([sys.executable, str(fake), "hook", "session-start"],
                          input=json.dumps({"cwd": str(cwd)}), capture_output=True, text=True)


def test_old_interpreter_hook_outside_a_project_is_silent(tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    done = run_old_hook(tmp_path, elsewhere)
    assert done.returncode == 0 and done.stdout == "" and done.stderr == ""


def test_old_interpreter_hook_in_a_project_tells_the_session(tmp_path):
    project = tmp_path / "app"
    (project / ".flotilla").mkdir(parents=True)
    (project / ".flotilla" / "project.toml").write_text("schema = 1\n", encoding="utf-8")
    done = run_old_hook(tmp_path, project / "src")
    assert done.returncode == 0
    assert "flotilla needs Python 99.0 or newer" in done.stdout


def guard_through(entry, cwd, command, env=None):
    import json
    payload = json.dumps({"cwd": str(cwd), "tool_input": {"command": command}})
    return subprocess.run([sys.executable, str(entry), "hook", "guard"], input=payload, capture_output=True,
                          text=True, env={**os.environ, **(env or {})})


def decision(done):
    import json
    try:
        return json.loads(done.stdout)["hookSpecificOutput"].get("permissionDecision", "")
    except (ValueError, KeyError):
        return ""


def onboarded_dir(tmp_path):
    project = tmp_path / "app"
    (project / ".flotilla").mkdir(parents=True)
    (project / ".flotilla" / "project.toml").write_text("schema = 1\n", encoding="utf-8")
    return project


def test_an_old_interpreter_refuses_a_push_in_a_project(tmp_path):
    """A plain message on stdout is no refusal: the push ran unchecked (review of 0.7.9)."""
    fake = tmp_path / "flotilla"
    fake.write_text(ENTRY.read_text(encoding="utf-8").replace("MINIMUM = (3, 11)", "MINIMUM = (99, 0)"),
                    encoding="utf-8")
    project = onboarded_dir(tmp_path)
    done = guard_through(fake, project, "git push origin main")
    assert done.returncode == 0 and decision(done) == "deny" and "99.0" in done.stdout
    assert decision(guard_through(fake, project, "git status")) == ""
    overridden = guard_through(fake, project, "git push origin main", {"FLOTILLA_GATE_OVERRIDE": "hotfix"})
    assert decision(overridden) == ""


def test_a_package_that_cannot_be_imported_refuses_a_push(tmp_path):
    """An import error before the hook's own code ran exited 1 with a traceback, which Claude Code does not read
    as a refusal (review of 0.7.9)."""
    (tmp_path / "bin").mkdir()
    entry = tmp_path / "bin" / "flotilla"
    entry.write_text(ENTRY.read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "flotilla").mkdir()
    (tmp_path / "flotilla" / "__init__.py").write_text("raise ImportError('broken install')\n", encoding="utf-8")
    done = guard_through(entry, tmp_path, "gh pr merge 12 --squash")
    assert done.returncode == 0 and decision(done) == "deny" and "broken install" in done.stdout
    calm = guard_through(entry, tmp_path, "ls -la")
    assert calm.returncode == 0 and decision(calm) == ""


def test_the_entrys_push_words_are_the_guards():
    import importlib.machinery
    import importlib.util
    from flotilla import guards
    loader = importlib.machinery.SourceFileLoader("flotilla_entry", str(ENTRY))
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader("flotilla_entry", loader))
    loader.exec_module(module)
    assert module.PUSH_WORDS == guards.PUSH_WORDS
