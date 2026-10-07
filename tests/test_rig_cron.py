import json
import subprocess

import pytest

from flotilla.rig import cron


class FakeCrontab:
    def __init__(self, text=None, *, broken=False):
        self.text = text
        self.broken = broken
        self.writes = 0

    def __call__(self, argv, *, input=None, **kwargs):
        if self.broken:
            raise FileNotFoundError("crontab")
        if argv == ["crontab", "-l"]:
            if self.text is None:
                return subprocess.CompletedProcess(argv, 1, "", "no crontab for max\n")
            return subprocess.CompletedProcess(argv, 0, self.text, "")
        if argv == ["crontab", "-"]:
            self.text = input
            self.writes += 1
            return subprocess.CompletedProcess(argv, 0, "", "")
        raise AssertionError(argv)


def line(tmp_path, state="state"):
    root = tmp_path / state
    return cron.reaper_line(launcher=root / "rig" / "reaper.py", python="/usr/bin/python3", state=root,
                            env={"PATH": "/usr/bin:/home/max/.local/bin"}), root


def test_the_line_runs_the_launcher_every_five_minutes(tmp_path):
    text, state = line(tmp_path)
    assert text.startswith("*/5 * * * * ") and text.endswith(cron.tag(state))
    assert "PATH=/usr/bin:/home/max/.local/bin" in text and str(state / "rig" / "reap.log") in text
    assert f"FLOTILLA_STATE_DIR={state}" in text
    assert cron.launcher_of(text) == str(state / "rig" / "reaper.py") and cron.python_of(text) == "/usr/bin/python3"


def test_a_percent_sign_anywhere_is_refused(tmp_path):
    root = tmp_path / "50%off"
    with pytest.raises(cron.CronError, match="%"):
        cron.reaper_line(launcher=root / "rig" / "reaper.py", python="/usr/bin/python3", state=root,
                         env={"PATH": "/usr/bin"})


def test_installing_twice_never_duplicates(tmp_path):
    table = FakeCrontab(None)
    text, state = line(tmp_path)
    assert cron.ensure(text, state, run=table) == "installed"
    assert cron.ensure(text, state, run=table) == "unchanged"
    assert table.text.count(cron.MARK) == 1 and table.writes == 1


def test_a_changed_line_replaces_its_own(tmp_path):
    table = FakeCrontab(None)
    text, state = line(tmp_path)
    cron.ensure(text, state, run=table)
    moved = text.replace("/usr/bin/python3", "/usr/local/bin/python3")
    assert cron.ensure(moved, state, run=table) == "replaced"
    assert table.text == moved + "\n"


def test_other_lines_survive_install_and_removal(tmp_path):
    theirs = "# my jobs\nMAILTO=\"\"\n17 3 * * * /home/max/backup.sh\n@reboot echo hi"
    table = FakeCrontab(theirs)
    text, state = line(tmp_path)
    cron.ensure(text, state, run=table)
    assert table.text.startswith(theirs + "\n")
    assert cron.remove(state, run=table) is True
    assert table.text == theirs + "\n"


def test_another_state_directorys_line_is_kept(tmp_path):
    table = FakeCrontab(None)
    mine, state = line(tmp_path, "state")
    theirs, other = line(tmp_path, "other-state")
    cron.ensure(theirs, other, run=table)
    cron.ensure(mine, state, run=table)
    cron.remove(state, run=table)
    assert table.text == theirs + "\n"


def test_removal_when_nothing_is_installed_writes_nothing(tmp_path):
    table = FakeCrontab("17 3 * * * /backup.sh\n")
    _, state = line(tmp_path)
    assert cron.remove(state, run=table) is False and table.writes == 0


def test_installed_reports_this_state_directorys_line_only(tmp_path):
    table = FakeCrontab("17 3 * * * /backup.sh\n")
    text, state = line(tmp_path)
    assert cron.installed(state, run=table) == ""
    cron.ensure(text, state, run=table)
    assert cron.installed(state, run=table) == text


def test_no_crontab_program_is_an_error_not_a_silent_skip(tmp_path):
    text, state = line(tmp_path)
    with pytest.raises(cron.CronError, match="crontab"):
        cron.ensure(text, state, run=FakeCrontab(broken=True))


def test_an_unreadable_crontab_is_never_overwritten(tmp_path):
    def run(argv, **kwargs):
        if argv == ["crontab", "-l"]:
            return subprocess.CompletedProcess(argv, 1, "", "crontab: permission denied\n")
        raise AssertionError("must not write over a crontab it could not read")
    text, state = line(tmp_path)
    with pytest.raises(cron.CronError, match="permission denied"):
        cron.ensure(text, state, run=run)


def test_the_interpreter_is_lasting_and_new_enough(tmp_path):
    from flotilla.onboard.tomlw import render_toml
    state = tmp_path / "state"
    state.mkdir()
    versions = {"/opt/py/bin/python3": (3, 12, 1), "/usr/bin/python3": (3, 9, 6), "/usr/local/bin/python3": (3, 11, 4)}
    (state / "machine.toml").write_text(render_toml({"python3": {"ok": True, "path": "/opt/py/bin/python3"}}))
    pick = lambda **kw: cron.interpreter(state, exists=lambda p: p in versions, version_of=versions.get, **kw)
    assert pick(which=lambda n: None) == "/opt/py/bin/python3"
    (state / "machine.toml").write_text(render_toml({"python3": {"ok": True,
                                                                 "path": "/home/max/.cache/uv/env/bin/python3"}}))
    assert pick(which=lambda n: "/usr/local/bin/python3") == "/usr/local/bin/python3"   # 3.9 is skipped
    with pytest.raises(cron.CronError, match="3.11"):
        pick(which=lambda n: None)


def test_the_marketplace_is_the_key_listing_this_root(tmp_path):
    root = tmp_path / "cache" / "flotilla" / "0.8.0"
    plugins = tmp_path / "claude" / "plugins" / "installed_plugins.json"
    plugins.parent.mkdir(parents=True)
    plugins.write_text(json.dumps({"version": 2, "plugins": {
        "flotilla@flotilla": [{"installPath": str(root), "version": "0.8.0"}],
        "flotilla@someone-else": [{"installPath": str(tmp_path / "x"), "version": "9.9.9"}]}}))
    env = {"CLAUDE_CONFIG_DIR": str(tmp_path / "claude")}
    assert cron.marketplace_of(root, env) == "flotilla@flotilla"
    assert cron.marketplace_of(tmp_path / "checkout", env) == ""


def test_a_crontab_with_bytes_that_are_not_utf8_is_read_and_kept(tmp_path):
    """A comment saved as latin-1 must not crash the reaper or be lost (final review of 0.8.0, I-1)."""
    theirs = tmp_path / "theirs"
    theirs.write_bytes(b"# caf\xe9 job\n17 3 * * * /backup.sh\n")
    written = {}

    def run(argv, **kwargs):
        if argv == ["crontab", "-l"]:
            return subprocess.run(["cat", str(theirs)], **kwargs)
        written["bytes"] = kwargs["input"].encode("utf-8", kwargs.get("errors") or "strict")
        return subprocess.CompletedProcess(argv, 0, "", "")
    text, state = line(tmp_path)
    cron.ensure(text, state, run=run)
    assert written["bytes"].startswith(b"# caf\xe9 job\n17 3 * * * /backup.sh\n")


def test_a_carriage_return_in_the_persons_line_is_kept(tmp_path):
    table = FakeCrontab("17 3 * * * /home/max/backup.sh\r\n")
    text, state = line(tmp_path)
    cron.ensure(text, state, run=table)
    assert table.text.startswith("17 3 * * * /home/max/backup.sh\r\n")
