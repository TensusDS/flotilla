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


def test_a_test_that_names_no_state_directory_is_refused_the_real_one():
    """twosuns feedback item 21: a test run rewrote the person's machine.toml to two lines. pytest marks every test
    with PYTEST_CURRENT_TEST whatever conftest loads; without an override the real directory is refused, not used."""
    import pytest
    from flotilla.core.paths import RealStateInTest
    with pytest.raises(RealStateInTest, match="FLOTILLA_STATE_DIR"):
        state_dir({"PYTEST_CURRENT_TEST": "tests/test_x.py::test_y (call)"})
    with pytest.raises(RealStateInTest):
        state_dir({"PYTEST_CURRENT_TEST": "t (call)", "XDG_STATE_HOME": "/s"})
    assert state_dir({"PYTEST_CURRENT_TEST": "t (call)", "FLOTILLA_STATE_DIR": "/t"}) == Path("/t")


def test_a_test_file_outside_tests_is_refused_the_real_state_directory(tmp_path):
    """The known road (TODO: a rig test run from outside tests/): a test file outside tests/ does not load
    tests/conftest.py, so the per-test state directory is never set. The refusal holds there too."""
    import os
    import subprocess
    import sys
    repo = Path(__file__).resolve().parents[1]
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    (scratch / "test_stray.py").write_text(
        "from flotilla.core.paths import state_dir\n\n\ndef test_stray():\n    state_dir()\n", encoding="utf-8")
    env = {key: value for key, value in os.environ.items()
           if key not in ("FLOTILLA_STATE_DIR", "XDG_STATE_HOME", "PYTEST_CURRENT_TEST", "PYTEST_ADDOPTS")}
    env.update(HOME=str(tmp_path / "home"), PYTHONPATH=str(repo))
    done = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--rootdir", str(scratch),
                           str(scratch / "test_stray.py")], cwd=scratch, env=env, capture_output=True, text=True,
                          timeout=120)
    assert done.returncode == 1, done.stdout + done.stderr
    assert "RealStateInTest" in done.stdout
    assert not (tmp_path / "home" / ".local").exists()


def test_a_run_that_changes_the_real_machine_file_fails(tmp_path):
    """The suite's own tripwire (tests/conftest.py): a test writing the real machine.toml by a road the refusal does
    not see still turns the run red. Run in a sandbox HOME, so the real file is never at stake here."""
    import os
    import subprocess
    import sys
    repo = Path(__file__).resolve().parents[1]
    home = tmp_path / "home"
    real = home / ".local" / "state" / "flotilla" / "machine.toml"
    real.parent.mkdir(parents=True)
    real.write_text('lane_share = "fair"\n', encoding="utf-8")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    (scratch / "test_writer.py").write_text(
        "import os\nfrom pathlib import Path\n\n\ndef test_writer():\n"
        "    Path(os.environ['HOME'], '.local/state/flotilla/machine.toml').write_text('rig = 1')\n",
        encoding="utf-8")
    env = {key: value for key, value in os.environ.items()
           if key not in ("FLOTILLA_STATE_DIR", "XDG_STATE_HOME", "PYTEST_CURRENT_TEST", "PYTEST_ADDOPTS")}
    env.update(HOME=str(home), PYTHONPATH=os.pathsep.join([str(repo), str(repo / "tests")]))
    done = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "conftest",
                           "--rootdir", str(scratch), str(scratch / "test_writer.py")], cwd=scratch, env=env,
                          capture_output=True, text=True, timeout=120)
    assert "1 passed" in done.stdout, done.stdout + done.stderr
    assert done.returncode == 1 and "changed the person's real" in done.stdout, done.stdout + done.stderr
