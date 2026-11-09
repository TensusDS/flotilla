import json
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



HELD_AT_START = census.held_at_start   # taken before the autouse double replaces it for every other test
DIALOG = (Path(__file__).parent / "fixtures" / "claude-logs" / "2.1.319-mcp-dialog.txt").read_text(encoding="utf-8")
NOW_MS = 1_794_226_300_000


def bare(short_id, name="app-main session 1", started_ms=NOW_MS - 10_000):
    """A background row with no process and no status, as Claude Code 2.1.319 lists a session held on a start-up
    question - and, the same, a job long gone (field test 2026-11-07, S3; measured again 2026-11-09)."""
    return {"id": short_id, "cwd": "/work/app-main-1", "kind": "background", "startedAt": started_ms,
            "sessionId": f"{short_id}-0000", "name": name, "state": "blocked"}


def claude_with(rows, screens):
    asked = []

    def run(cmd, **kwargs):
        asked.append(cmd[1:])
        if cmd[1:] == ["agents", "--json"]:
            return subprocess.CompletedProcess(cmd, 0, json.dumps(rows), "")
        if cmd[1] == "logs" and cmd[2] in screens:
            return subprocess.CompletedProcess(cmd, 0, screens[cmd[2]], "")
        return subprocess.CompletedProcess(cmd, 1, "", f"Couldn't read logs for {cmd[2]} - job not found")
    return run, asked


def test_a_seat_held_on_a_start_up_question_is_told_from_a_gone_job_by_its_screen():
    run, _ = claude_with([bare("dead0001", started_ms=NOW_MS - 86_400_000), bare("0204d254")],
                         {"0204d254": DIALOG})
    held = HELD_AT_START({"app-main session 1"}, run=run)
    assert [(s.short_id, s.held_on) for s in held] == \
        [("0204d254", "New MCP server found in this project: probe-dialog")]
    assert census.drop_gone(census.parse_census(json.dumps([bare("0204d254")]))) == []   # the row alone cannot tell


def test_a_screen_that_asks_nothing_is_never_held_however_old():
    """Review of bedb2a3: "the screen answers and the seat is a minute old" rested on a gone job's `claude logs`
    exiting 1, measured once; a Claude Code that kept a gone job's last screen would raise every stale row. Only a
    screen that asks counts."""
    screen = "\x1b[2J Claude Code v2.1.319\n > reply with the word ok\n Processing... (0s)\n"
    run, _ = claude_with([bare("young001"), bare("old00001", started_ms=NOW_MS - 86_400_000)],
                         {"young001": screen, "old00001": screen})
    assert HELD_AT_START({"app-main session 1"}, run=run) == []


def test_the_question_is_made_safe_to_show():
    """A `.mcp.json` server's name is the project's text, shown to the person by watch and inbox."""
    screen = ("\x1b]8;;https://example.invalid\x07New MCP server found in this project: evil\x1b[2Jname\x1b]8;;\x07"
              "\n Enter to confirm\n")
    run, _ = claude_with([bare("0204d254")], {"0204d254": screen})
    [held] = HELD_AT_START({"app-main session 1"}, run=run)
    assert held.held_on == "New MCP server found in this project: evil name"


def test_one_screen_is_asked_with_its_own_short_timeout():
    asked = []

    def run(cmd, **kwargs):
        asked.append((cmd[1], kwargs.get("timeout")))
        if cmd[1] == "logs":
            return subprocess.CompletedProcess(cmd, 0, DIALOG, "")
        return subprocess.CompletedProcess(cmd, 0, json.dumps([bare("0204d254")]), "")
    HELD_AT_START({"app-main session 1"}, run=run, timeout=20)
    assert asked == [("agents", 20), ("logs", census.LOGS_TIMEOUT)] and census.LOGS_TIMEOUT == 10


def test_only_the_named_bare_rows_are_looked_at():
    rows = [bare("other001", name="someone else"), {**bare("live0001"), "pid": 4242, "status": "busy"}]
    run, asked = claude_with(rows, {"other001": DIALOG, "live0001": DIALOG})
    assert HELD_AT_START({"app-main session 1"}, run=run) == []
    assert asked == [["agents", "--json"]]
    assert HELD_AT_START(set(), run=run) == [] and asked == [["agents", "--json"]]   # nobody to ask


def test_a_screen_that_cannot_be_read_is_not_held():
    def run(cmd, **kwargs):
        if cmd[1] == "logs":
            raise subprocess.TimeoutExpired(cmd, 30)
        return subprocess.CompletedProcess(cmd, 0, json.dumps([bare("0204d254")]), "")
    assert HELD_AT_START({"app-main session 1"}, run=run) == []


def test_what_the_strip_leaves_is_still_made_safe_to_show():   # review of 14c5ff8, finding 4: visible() itself
    screen = "New MCP server \x1b(0found\x9b2J here‮: evil\x07\n Enter to confirm\n"
    run, _ = claude_with([bare("0204d254")], {"0204d254": screen})
    [held] = HELD_AT_START({"app-main session 1"}, run=run)
    assert held.held_on.isprintable() and "\x1b" not in held.held_on and "‮" not in held.held_on
