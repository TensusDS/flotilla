import io
import json
import subprocess
import sys
import tomllib
from contextlib import redirect_stdout

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
    assert profile["schema"] == 1 and profile["tests"]["tier"][0]["name"] == "custom-1"
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
