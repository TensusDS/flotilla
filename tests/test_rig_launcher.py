import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from flotilla.rig import cron, providers
from rigkit import FakeVast, fake_key

LAUNCHER = Path(__file__).resolve().parents[1] / "flotilla" / "rig" / "launcher.py"


def load():
    spec = importlib.util.spec_from_file_location("rig_launcher_under_test", LAUNCHER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def plugin(root: Path, version: str) -> Path:
    (root / "bin").mkdir(parents=True)
    (root / "bin" / "flotilla").write_text("#!/bin/sh\n")
    (root / "flotilla" / "rig").mkdir(parents=True)
    (root / "flotilla" / "rig" / "reaper.py").write_text("")
    (root / "flotilla" / "__init__.py").write_text(f'__version__ = "{version}"\n')
    return root


def plugins_json(path: Path, entries: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": 2, "plugins": entries}))
    return path


def entry(folder: Path, root: Path, version="0.8.0", marketplace="flotilla@flotilla") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "entry"
    path.write_text(json.dumps({"root": str(root), "version": version, "marketplace": marketplace}))
    return path


class Crontab:
    def __init__(self, text=""):
        self.text = text

    def __call__(self, argv, *, input=None, **kwargs):
        if argv == ["crontab", "-l"]:
            return subprocess.CompletedProcess(argv, 0, self.text, "")
        self.text = input
        return subprocess.CompletedProcess(argv, 0, "", "")


def state_with_adapters(tmp_path, monkeypatch, launcher, fake, machine_key="0123456789ab"):
    """A state directory as `install_launcher` leaves it, the copied adapter talking to the double."""
    state = tmp_path / "state"
    cron.install_launcher(state, tmp_path / "no-plugin", version="0.8.0", marketplace="flotilla@flotilla")
    folder = state / "rig"
    if machine_key:
        (folder / "machine-key").write_text(machine_key + "\n")
    real = launcher._load

    def load_with_double(path):
        module = real(path)
        module.SEND = fake
        return module
    monkeypatch.setattr(launcher, "_load", load_with_double)
    key = fake_key(tmp_path)
    return folder, {"CLAUDE_CONFIG_DIR": str(tmp_path / "none"), "XDG_CONFIG_HOME": str(key.parents[2])}


def test_the_launcher_imports_nothing_from_flotilla():
    import ast
    tree = ast.parse(LAUNCHER.read_text())
    names = {alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    names |= {node.module.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module}
    assert "flotilla" not in names and names <= set(sys.stdlib_module_names), names


def test_the_launcher_and_cron_agree_on_the_mark_and_the_tag(tmp_path):
    launcher = load()
    assert launcher.MARK == cron.MARK and launcher.tag(tmp_path / "state") == cron.tag(tmp_path / "state")


def test_installing_copies_every_adapter_byte_for_byte(tmp_path):
    cron.install_launcher(tmp_path / "state", tmp_path / "no-plugin", version="0.8.0", marketplace="")
    for source in providers.files():
        assert (tmp_path / "state" / "rig" / "providers" / source.name).read_bytes() == source.read_bytes()


def test_an_older_flotilla_never_rewrites_what_a_newer_one_installed(tmp_path):
    state = tmp_path / "state"
    cron.install_launcher(state, tmp_path / "new", version="0.9.0", marketplace="flotilla@flotilla")
    (state / "rig" / "reaper.py").write_text("# the newer launcher\n")
    cron.install_launcher(state, tmp_path / "old", version="0.8.0", marketplace="flotilla@flotilla")
    assert (state / "rig" / "reaper.py").read_text() == "# the newer launcher\n"
    assert json.loads((state / "rig" / "entry").read_text())["version"] == "0.9.0"


def test_only_the_recorded_marketplace_is_trusted_newest_first(tmp_path):
    launcher = load()
    ours_old, ours_new = plugin(tmp_path / "c" / "0.8.0", "0.8.0"), plugin(tmp_path / "c" / "0.8.2", "0.8.2")
    stranger = plugin(tmp_path / "x" / "9.9.9", "9.9.9")
    plugins = plugins_json(tmp_path / "p.json", {
        "flotilla@flotilla": [{"installPath": str(ours_old), "version": "0.8.0"},
                              {"installPath": str(ours_new), "version": "0.8.2"}],
        "flotilla@someone-else": [{"installPath": str(stranger), "version": "9.9.9"}]})
    found = launcher.candidates(plugins, entry(tmp_path / "rig", ours_old))
    assert [path for _, path in found] == [ours_old, ours_new]


def test_a_deleted_version_is_skipped(tmp_path):
    launcher = load()
    kept = plugin(tmp_path / "c" / "0.8.0", "0.8.0")
    plugins = plugins_json(tmp_path / "p.json", {"flotilla@flotilla": [
        {"installPath": str(kept), "version": "0.8.0"},
        {"installPath": str(tmp_path / "c" / "0.9.0"), "version": "0.9.0"}]})
    found = launcher.candidates(plugins, entry(tmp_path / "rig", tmp_path / "gone"))
    assert [path for _, path in found] == [kept]


def test_a_development_checkout_is_trusted_by_its_recorded_root(tmp_path):
    launcher = load()
    dev = plugin(tmp_path / "checkout", "0.8.5")
    found = launcher.candidates(tmp_path / "missing.json", entry(tmp_path / "rig", dev, "0.8.5", marketplace=""))
    assert [path for _, path in found] == [dev]


def test_the_newest_flotilla_that_reaps_wins_and_a_broken_one_falls_through(tmp_path, monkeypatch):
    launcher = load()
    good, broken = plugin(tmp_path / "c" / "0.8.0", "0.8.0"), plugin(tmp_path / "c" / "0.8.1", "0.8.1")
    folder = tmp_path / "state" / "rig"
    entry(folder, good)
    plugins_json(tmp_path / "claude" / "plugins" / "installed_plugins.json", {"flotilla@flotilla": [
        {"installPath": str(good), "version": "0.8.0"}, {"installPath": str(broken), "version": "0.8.1"}]})
    ran = []

    def run(argv):
        ran.append(argv[1])
        if argv[1].startswith(str(good)):
            (folder / "last-reap").write_text("{}")   # a pass that happened leaves its mark
            return 0
        return 3                                        # "flotilla needs Python 3.11": no pass
    monkeypatch.setattr(launcher, "_run", run)
    code = launcher.main({"CLAUDE_CONFIG_DIR": str(tmp_path / "claude")}, folder=folder, run_crontab=Crontab())
    assert code == 0 and ran == [str(broken / "bin" / "flotilla"), str(good / "bin" / "flotilla")]


def test_the_last_resort_waits_for_a_second_pass_without_flotilla(tmp_path, monkeypatch):
    launcher = load()
    fake = FakeVast({"101": {"label": "flotilla:0123456789ab:m1"}})
    folder, env = state_with_adapters(tmp_path, monkeypatch, launcher, fake)
    assert launcher.main(env, folder=folder, run_crontab=Crontab()) == 1
    assert "101" in fake.instances                    # one miss may be a file mid-rewrite
    assert launcher.main(env, folder=folder, run_crontab=Crontab()) == 1
    assert fake.instances == {}


def test_last_resort_destroys_only_this_machines_labels(tmp_path, monkeypatch):
    launcher = load()
    fake = FakeVast({"101": {"label": "flotilla:0123456789ab:m1"}, "201": {"label": "flotilla:ffffffffffff:m1"},
                     "301": {"label": None}, "302": {"label": "echoes render box"},
                     "401": {"label": "flotilla:0123456789ab"}})
    folder, env = state_with_adapters(tmp_path, monkeypatch, launcher, fake)
    launcher.last_resort(folder, env)
    assert set(fake.instances) == {"201", "301", "302", "401"}


def test_last_resort_without_a_machine_key_destroys_nothing(tmp_path, monkeypatch):
    launcher = load()
    fake = FakeVast({"101": {"label": "flotilla:0123456789ab:m1"}})
    folder, env = state_with_adapters(tmp_path, monkeypatch, launcher, fake, machine_key="")
    assert launcher.last_resort(folder, env)[0] == 1 and "101" in fake.instances


def test_last_resort_skips_a_service_with_no_key(tmp_path, monkeypatch):
    launcher = load()
    fake = FakeVast({"101": {"label": "flotilla:0123456789ab:m1"}})
    folder, env = state_with_adapters(tmp_path, monkeypatch, launcher, fake)
    (Path(env["XDG_CONFIG_HOME"]) / "flotilla" / "rig" / "vast.key").unlink()
    launcher.last_resort(folder, env)
    assert fake.requests == []


def test_with_nothing_left_the_last_resort_removes_its_own_line_only(tmp_path, monkeypatch):
    launcher = load()
    fake = FakeVast({"301": {"label": "echoes render box"}})
    folder, env = state_with_adapters(tmp_path, monkeypatch, launcher, fake)
    table = Crontab(f"17 3 * * * /backup.sh\n*/5 * * * * x {cron.tag(folder.parent)}\n"
                    f"*/5 * * * * y {cron.tag(tmp_path / 'other')}\n")
    for _ in range(2):
        launcher.main(env, folder=folder, run_crontab=table)
    assert table.text == f"17 3 * * * /backup.sh\n*/5 * * * * y {cron.tag(tmp_path / 'other')}\n"


def test_a_failed_listing_never_removes_the_line(tmp_path, monkeypatch):
    launcher = load()
    fake = FakeVast({}, listing_status=503)
    folder, env = state_with_adapters(tmp_path, monkeypatch, launcher, fake)
    table = Crontab(f"*/5 * * * * x {cron.tag(folder.parent)}\n")
    for _ in range(2):
        launcher.main(env, folder=folder, run_crontab=table)
    assert cron.tag(folder.parent) in table.text


def test_the_installed_launcher_runs_as_a_real_script(tmp_path):
    state = tmp_path / "state"
    root = plugin(tmp_path / "c" / "0.8.0", "0.8.0")
    (root / "bin" / "flotilla").write_text(
        f"#!{sys.executable}\nimport os, pathlib, sys\nprint('reap via', sys.argv[1:])\n"
        "pathlib.Path(os.environ['FLOTILLA_STATE_DIR'], 'rig', 'last-reap').write_text('{}')\n")
    (root / "bin" / "flotilla").chmod(0o755)
    script = cron.install_launcher(state, root, version="0.8.0", marketplace="")
    assert oct(os.stat(script).st_mode & 0o777) == "0o700"
    done = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, timeout=60,
                          env={**os.environ, "CLAUDE_CONFIG_DIR": str(tmp_path / "none"),
                               "FLOTILLA_STATE_DIR": str(state)})
    assert "reap via ['rig', 'reap']" in done.stdout and done.returncode == 0
