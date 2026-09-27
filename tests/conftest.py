import pytest


@pytest.fixture(autouse=True)
def state_dir_per_test(tmp_path, monkeypatch):
    """No test writes into the real state directory: each gets its own unless it sets one."""
    monkeypatch.setenv("FLOTILLA_STATE_DIR", str(tmp_path / "flotilla-state"))
