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


def test_the_state_directory_is_private_to_its_user(tmp_path, monkeypatch):
    """Command text, tool inputs and the ledger live there; it was created 0755, readable by every local user
    (security review F25, F16). Closing the root closes everything under it."""
    import os
    import stat
    from flotilla import cli
    state = tmp_path / "state"
    monkeypatch.setenv("FLOTILLA_STATE_DIR", str(state))
    cli.main(["doctor", "--quiet"])
    assert stat.S_IMODE(os.stat(state).st_mode) == 0o700
    os.chmod(state, 0o755)
    cli.main(["doctor", "--quiet"])
    assert stat.S_IMODE(os.stat(state).st_mode) == 0o700
