import os

from flotilla.core import platform as plat
from flotilla.core.census import Session
from flotilla.core.identity import find_calling_session


def session(name, pid):
    return Session(name=name, session_id=name, kind="background", pid=pid, short_id=None,
                   status="busy", state="working", cwd="/", started_at_ms=None)


def chain(mapping):
    return lambda pid: mapping.get(pid)


def test_first_ancestor_in_the_census_wins():
    sessions = [session("outer", 10), session("inner", 20)]
    found = find_calling_session(sessions, start_pid=40, parent_of=chain({40: 30, 30: 20, 20: 10, 10: 1}))
    assert found.name == "inner"


def test_no_ancestor_in_the_census_is_none_not_an_error():
    found = find_calling_session([session("elsewhere", 99)], start_pid=40, parent_of=chain({40: 30, 30: 1}))
    assert found is None


def test_rows_without_pid_never_match():
    found = find_calling_session([session("gone", None)], start_pid=40, parent_of=chain({40: 1}))
    assert found is None


def test_a_cycle_in_the_process_table_stops():
    found = find_calling_session([], start_pid=40, parent_of=chain({40: 30, 30: 40}))
    assert found is None


def test_real_ancestor_walk_on_this_machine():
    # The pytest process's parent stands in for the Claude Code session process.
    caps = plat.probe()
    parent_of = lambda pid: plat.parent_pid(pid, caps.parent_pid_source)
    found = find_calling_session([session("host", os.getppid())], parent_of=parent_of)
    assert found is not None and found.name == "host"
