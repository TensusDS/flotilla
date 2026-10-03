import pytest


@pytest.fixture(autouse=True)
def state_dir_per_test(tmp_path, monkeypatch):
    """No test writes into the real state directory: each gets its own unless it sets one."""
    monkeypatch.setenv("FLOTILLA_STATE_DIR", str(tmp_path / "flotilla-state"))


@pytest.fixture(autouse=True)
def memory_is_not_asked(monkeypatch):
    """No test depends on this machine's free memory; the tests of the floor set it themselves."""
    from flotilla.fleet import spawn
    monkeypatch.setattr(spawn, "available_mb", lambda: None)


@pytest.fixture(autouse=True)
def memory_is_not_this_machines(monkeypatch):
    """The lane asks memory; a test's lane must not close because the machine running the suite is short of it."""
    monkeypatch.setattr("flotilla.lane.machine._meminfo", lambda: None)
    monkeypatch.setattr("flotilla.lane.machine._memtotal", lambda: None)


@pytest.fixture(autouse=True)
def no_real_process_is_stopped(tmp_path, monkeypatch):
    """Retire stops orphans it finds in /proc; no test may look at, or signal, this machine's processes."""
    monkeypatch.setattr("flotilla.fleet.leftovers.PROC_ROOT", tmp_path / "no-proc-in-tests")
    monkeypatch.setattr("flotilla.fleet.leftovers.CONFIG_DIR", tmp_path / "no-claude-config-in-tests")


@pytest.fixture(autouse=True)
def the_caller_is_a_person_in_a_terminal(monkeypatch):
    """Answering a permission question and recording onboarding answers ask who calls (security review F2-F12).
    A test is a person at a terminal in an interactive session unless it says otherwise; no test asks the census."""
    from flotilla.core import caller
    from flotilla.core.census import Session
    monkeypatch.setattr(caller, "has_terminal", lambda: True)
    monkeypatch.setattr(caller, "calling_sessions", lambda: [Session(
        name="", session_id="person", kind="interactive", pid=None, short_id=None, status=None, state=None, cwd="",
        started_at_ms=None)])


@pytest.fixture(autouse=True)
def git_rewrites_switched_off_per_test(monkeypatch):
    """The hook entry points switch off git's replace refs and grafts for their whole process; in a test process
    that would outlive the test. Each test starts without them and leaves none behind."""
    from flotilla.guards.push import NO_REWRITES
    for key in NO_REWRITES:
        monkeypatch.setenv(key, "")
        monkeypatch.delenv(key)
