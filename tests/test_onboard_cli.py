import io
import json
import subprocess
import sys
import tomllib
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from flotilla import cli


def run_cli(*args):
    out = io.StringIO()
    with redirect_stdout(out):
        code = cli.main(list(args))
    return code, out.getvalue()


@pytest.fixture()
def repo(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOTILLA_STATE_DIR", str(tmp_path / "state"))
    root = tmp_path / "app"
    root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    return root


def answer_everything(root):
    command = f"{sys.executable} -c \"print('1 passed')\""
    for _ in range(20):
        code, out = run_cli("onboard", "next", "--root", str(root))
        assert code == 0
        page = json.loads(out)
        if page["done"]:
            return
        for q in page["questions"]:
            value = command if q["id"] == "tiers" else q["options"][0]["value"]
            code, out = run_cli("onboard", "answer", q["id"], value, "--root", str(root))
            assert code == 0, out
    raise AssertionError("never done")


def test_full_onboarding_writes_a_loadable_profile(repo, tmp_path):
    answer_everything(repo)
    code, out = run_cli("onboard", "write", "--root", str(repo))
    assert code == 0, out
    profile = tomllib.loads((repo / ".flotilla" / "project.toml").read_text(encoding="utf-8"))
    named = Path(sys.executable).name   # a typed tier is named after its command's first word (F6)
    assert profile["schema"] == 1 and profile["tests"]["tier"][0]["name"] == named
    assert "measured_seconds" not in json.dumps(profile)
    assert list((tmp_path / "state" / "measurements").glob("*.toml"))
    assert run_cli("onboard", "check", "--root", str(repo))[0] == 0


def test_second_write_without_force_is_refused(repo):
    answer_everything(repo)
    assert run_cli("onboard", "write", "--root", str(repo))[0] == 0
    answer_everything(repo)
    code, out = run_cli("onboard", "write", "--root", str(repo))
    assert code == 2 and "already exists" in out


def test_write_before_all_answers_is_refused(repo):
    code, out = run_cli("onboard", "write", "--root", str(repo))
    assert code == 2 and "questions remain" in out


def test_a_red_tier_is_not_written(repo):
    for _ in range(20):
        page = json.loads(run_cli("onboard", "next", "--root", str(repo))[1])
        if page["done"]:
            break
        for q in page["questions"]:
            value = "exit 3" if q["id"] == "tiers" else q["options"][0]["value"]
            run_cli("onboard", "answer", q["id"], value, "--root", str(repo))
    code, out = run_cli("onboard", "write", "--root", str(repo))
    assert code == 4 and "red" in out
    assert not (repo / ".flotilla" / "project.toml").exists()


def test_outside_a_repository_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOTILLA_STATE_DIR", str(tmp_path / "state"))
    code, out = run_cli("onboard", "next", "--root", str(tmp_path))
    assert code == 2 and "not inside a git repository" in out


def test_bad_answer_is_refused(repo):
    code, out = run_cli("onboard", "answer", "review", "sometimes", "--root", str(repo))
    assert code == 2 and "not one of" in out


def test_machine_writes_machine_toml(repo, tmp_path):
    code, _ = run_cli("onboard", "machine")
    assert (tmp_path / "state" / "machine.toml").is_file()
    assert code in (0, 1)


def test_write_installs_the_post_templates(repo):
    answer_everything(repo)
    assert run_cli("onboard", "write", "--root", str(repo))[0] == 0
    installed = sorted(p.stem for p in (repo / ".flotilla" / "posts").glob("*.md"))
    assert installed == ["helper", "judge", "main", "minor", "orchestrator", "reviewer", "sender"]


def background_seat(monkeypatch, name="main session 1"):
    from flotilla.core import caller
    from flotilla.core.census import Session
    monkeypatch.setattr(caller, "calling_session", lambda: Session(
        name=name, session_id="s", kind="background", pid=42, short_id="abc123", status=None, state=None, cwd="",
        started_at_ms=None))


@pytest.mark.parametrize("argv", [("answer", "tiers", "git push -f origin HEAD:main"), ("write",), ("reset",)])
def test_a_background_session_may_not_record_run_or_reset_onboarding(repo, monkeypatch, argv):
    """Onboarding runs the commands it records through a shell: only a person may record or run them (security
    review F2, F3, F8)."""
    background_seat(monkeypatch)
    code, out = run_cli("onboard", *argv, "--root", str(repo))
    assert code == 2 and "`main session 1` is a background session" in out


def test_onboarding_from_outside_any_session_needs_a_terminal(repo, monkeypatch):
    from flotilla.core import caller
    monkeypatch.setattr(caller, "calling_session", lambda: None)
    monkeypatch.setattr(caller, "has_terminal", lambda: False)
    code, out = run_cli("onboard", "answer", "tiers", "true", "--root", str(repo))
    assert code == 2 and "a terminal" in out


def test_onboarding_refuses_when_the_census_cannot_say_who_calls(repo, monkeypatch):
    from flotilla.core import caller
    from flotilla.core.census import CensusUnavailable

    def down():
        raise CensusUnavailable("`claude agents --json` did not answer")
    monkeypatch.setattr(caller, "calling_session", down)
    code, out = run_cli("onboard", "write", "--root", str(repo))
    assert code == 2 and "could not tell who" in out


def test_reading_steps_need_no_person(repo, monkeypatch):
    background_seat(monkeypatch)
    code, out = run_cli("onboard", "next", "--root", str(repo))
    assert code == 0


def test_write_names_every_command_before_it_runs_it(repo):
    answer_everything(repo)
    code, out = run_cli("onboard", "write", "--root", str(repo))
    assert code == 0
    will = [line for line in out.splitlines() if line.startswith("will run ")]
    assert will and "print('1 passed')" in will[0]
    assert out.index(will[0]) < out.index("\ngreen")
