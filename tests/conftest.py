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
