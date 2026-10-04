"""The records of Claude Code that flotilla reads, checked against the shapes measured on real versions."""

import json
from pathlib import Path

import pytest

from flotilla.core import claude_state

FIXTURES = Path(__file__).parent / "fixtures" / "agents-json"


def rows(version="2.1.289"):
    return json.loads((FIXTURES / f"{version}.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("version", ["2.1.280", "2.1.289"])
def test_every_measured_census_shape_is_accepted(version):
    assert claude_state.census_problems(rows(version)) == []


def without(key, kind=None):
    return [{k: v for k, v in row.items() if not (k == key and kind in (None, row.get("kind")))} for row in rows()]


def changed(key, value, kind=None):
    return [{**row, key: value} if kind in (None, row.get("kind")) and key in row else row for row in rows()]


@pytest.mark.parametrize("broken, says", [
    (without("kind"), "`kind`"),
    (changed("kind", "remote", "background"), "`kind`"),
    (without("state", "background"), "`state`"),
    (changed("state", "paused", "background"), "`state`"),
    (without("status", "interactive"), "`status`"),
    (changed("status", "thinking", "interactive"), "`status`"),
    (changed("status", "thinking", "background"), "`status`"),
    (without("cwd"), "`cwd`"),
    (without("pid"), "`pid`"),
    (without("name"), "`name`"),
])
def test_each_drifted_field_is_named_with_what_stops_working(broken, says):
    found = claude_state.census_problems(broken)
    assert len(found) == 1 and says in found[0] and ":" in found[0], found


def test_an_empty_census_has_nothing_to_measure():
    assert claude_state.census_problems([]) == []


def registry(tmp_path, entries):
    folder = tmp_path / "sessions"
    folder.mkdir(parents=True)
    for pid, entry in entries.items():
        (folder / f"{pid}.json").write_text(json.dumps(entry), encoding="utf-8")
    return tmp_path


def test_the_registry_is_readable_when_a_live_session_has_its_entry(tmp_path):
    config = registry(tmp_path, {1001: {"sessionId": "00000000-0000-4000-8000-000000000001", "entrypoint": "cli"}})
    assert claude_state.registry_readable(rows(), config) is True


def test_the_registry_has_moved_when_no_live_session_has_an_entry(tmp_path):
    assert claude_state.registry_readable(rows(), registry(tmp_path, {})) is False
    stale = registry(tmp_path / "b", {1001: {"sessionId": "someone else", "entrypoint": "cli"}})
    assert claude_state.registry_readable(rows(), stale) is False
    no_entrypoint = registry(tmp_path / "c", {1001: {"sessionId": "00000000-0000-4000-8000-000000000001"}})
    assert claude_state.registry_readable(rows(), no_entrypoint) is False


def test_without_a_session_carrying_a_pid_the_registry_cannot_be_asked(tmp_path):
    no_pids = [{k: v for k, v in row.items() if k != "pid"} for row in rows()]
    assert claude_state.registry_readable(no_pids, registry(tmp_path, {})) is None


def test_the_session_entry_is_one_door_for_the_registry(tmp_path):
    config = registry(tmp_path, {1001: {"sessionId": "s1", "entrypoint": "sdk-cli"}})
    assert claude_state.session_entry(1001, "s1", config) == {"sessionId": "s1", "entrypoint": "sdk-cli"}
    assert claude_state.session_entry(1001, "s2", config) is None   # a reused pid names another session
    assert claude_state.session_entry(1002, "s1", config) is None


def test_the_trust_record_shape(tmp_path):
    assert claude_state.trust_record(tmp_path) is None   # no file: nothing to read
    (tmp_path / ".claude.json").write_text(json.dumps({"projects": {}}), encoding="utf-8")
    assert claude_state.trust_record(tmp_path) is True
    (tmp_path / ".claude.json").write_text(json.dumps({"workspaces": {}}), encoding="utf-8")
    assert claude_state.trust_record(tmp_path) is False
