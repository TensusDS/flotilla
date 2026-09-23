from pathlib import Path

from flotilla.core.paths import state_dir


def test_override_wins():
    assert state_dir({"FLOTILLA_STATE_DIR": "/x/y", "XDG_STATE_HOME": "/s"}) == Path("/x/y")


def test_xdg_state_home():
    assert state_dir({"XDG_STATE_HOME": "/s"}) == Path("/s/flotilla")


def test_default_is_local_state_under_home(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert state_dir({}) == tmp_path / ".local" / "state" / "flotilla"


def test_never_the_plugin_data_directory():
    # That directory is deleted on uninstall (spec, section 3.1).
    assert state_dir({"CLAUDE_PLUGIN_DATA": "/p", "XDG_STATE_HOME": "/s"}) == Path("/s/flotilla")
