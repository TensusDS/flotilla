import subprocess
from pathlib import Path

import pytest

from flotilla.core import census

FIXTURES = Path(__file__).parent / "fixtures" / "agents-json"


def sample(version="2.1.280"):
    return (FIXTURES / f"{version}.json").read_text(encoding="utf-8")


def test_every_recorded_row_parses():
    sessions = census.parse_census(sample())
    assert [s.name for s in sessions] == ["operator", "review session 1", "main session 2", "minor session 4"]


def test_nullable_fields_keep_both_branches():
    by_name = {s.name: s for s in census.parse_census(sample())}
    assert by_name["operator"].short_id is None and by_name["review session 1"].short_id == "1a2b3c4d"
    assert by_name["minor session 4"].pid is None and by_name["main session 2"].pid == 3003
    assert by_name["minor session 4"].status is None and by_name["main session 2"].status == "idle"
    assert by_name["operator"].state is None and by_name["main session 2"].state == "blocked"


def test_unknown_fields_are_ignored():
    text = '[{"sessionId": "s", "name": "n", "kind": "background", "cwd": "/", "future": 1}]'
    assert census.parse_census(text)[0].name == "n"


def test_boolean_pid_is_not_a_pid():
    text = '[{"sessionId": "s", "name": "n", "kind": "background", "cwd": "/", "pid": true}]'
    assert census.parse_census(text)[0].pid is None


@pytest.mark.parametrize("text", ["not json", "{}", "[1]", '[{"name": "no id"}]'])
def test_malformed_output_is_unavailable(text):
    with pytest.raises(census.CensusUnavailable):
        census.parse_census(text)


def fake_run(returncode=0, stdout="[]", stderr="", exc=None):
    def run(argv, **kwargs):
        if exc:
            raise exc
        return subprocess.CompletedProcess(argv, returncode, stdout=stdout, stderr=stderr)
    return run


def test_empty_census_is_an_answer():
    assert census.read_census(run=fake_run(stdout="[]")) == []


def test_missing_cli_is_unavailable_not_empty():
    with pytest.raises(census.CensusUnavailable, match="not on PATH"):
        census.read_census(run=fake_run(exc=FileNotFoundError("claude")))


def test_nonzero_exit_is_unavailable():
    with pytest.raises(census.CensusUnavailable, match="exited 1"):
        census.read_census(run=fake_run(returncode=1, stderr="boom"))


def test_timeout_is_unavailable():
    with pytest.raises(census.CensusUnavailable, match="did not answer"):
        census.read_census(run=fake_run(exc=subprocess.TimeoutExpired("claude", 30)))


def test_unexecutable_cli_is_unavailable():
    with pytest.raises(census.CensusUnavailable, match="cannot be run"):
        census.read_census(run=fake_run(exc=PermissionError(13, "Permission denied", "claude")))


def test_a_background_session_with_no_process_is_gone():
    sessions = census.parse_census(sample())
    kept = census.drop_gone(sessions, pid_alive=lambda pid: pid in (1001, 2002, 3003))
    assert [s.name for s in kept] == ["operator", "review session 1", "main session 2"]


def test_only_a_session_with_no_status_is_judged_by_its_pid():
    sessions = census.parse_census(sample())
    kept = census.drop_gone(sessions, pid_alive=lambda pid: False)   # a pid this process cannot see
    assert [s.name for s in kept] == ["operator", "review session 1", "main session 2"]


def test_a_background_session_with_a_status_but_no_pid_yet_is_kept():
    text = ('[{"sessionId": "s", "name": "main session 9", "kind": "background", "cwd": "/", "pid": null,'
            ' "status": "busy", "state": "working"}]')
    assert [s.name for s in census.drop_gone(census.parse_census(text), pid_alive=lambda pid: False)] == \
        ["main session 9"]


def test_read_census_drops_what_is_gone():
    def run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, sample(), "")
    kept = census.read_census(run=run, pid_alive=lambda pid: pid in (1001, 2002, 3003))
    assert "minor session 4" not in [s.name for s in kept]


def test_a_session_reporting_a_status_is_kept_even_when_its_pid_looks_dead():
    text = ('[{"sessionId": "s", "name": "main session 9", "kind": "background", "cwd": "/", "pid": 4242,'
            ' "status": "busy", "state": "working"}]')
    assert [s.name for s in census.drop_gone(census.parse_census(text), pid_alive=lambda pid: False)] == \
        ["main session 9"]
