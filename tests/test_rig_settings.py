import os
import stat

import pytest

from flotilla.onboard.machine import PERSON_KEYS, read_machine, set_person_keys, write_machine
from flotilla.onboard.tomlw import render_toml
from flotilla.rig import settings as rs


def write(state, text):
    state.mkdir(parents=True, exist_ok=True)
    (state / "machine.toml").write_text(text, encoding="utf-8")


def test_a_fresh_machine_has_rig_off(tmp_path):
    got = rs.settings(tmp_path / "state")
    assert got.on is False and got.provider == ""
    assert (got.max_machines, got.max_hourly, got.max_hours) == (1, 0.60, 8.0)


def test_measuring_the_machine_writes_rig_off_and_keeps_the_persons_rig_keys(tmp_path):
    state = tmp_path / "state"
    write_machine(state, {"schema": 1})
    data = read_machine(state)
    assert data["rig"] == "off" and data["rig_provider"] == ""
    set_person_keys(state, rig="on", rig_provider="vast", rig_max_hourly=0.4)
    write_machine(state, {"schema": 1, "cpu_count": 8})
    data = read_machine(state)
    assert (data["rig"], data["rig_provider"], data["rig_max_hourly"], data["cpu_count"]) == ("on", "vast", 0.4, 8)
    for key in ("rig", "rig_provider", "rig_max_machines", "rig_max_hourly", "rig_max_hours"):
        assert key in PERSON_KEYS


def test_setting_person_keys_on_a_torn_file_refuses_and_keeps_it(tmp_path):
    state = tmp_path / "state"
    write(state, "cpu_count = 8\n[[[")
    with pytest.raises(ValueError):
        set_person_keys(state, rig="on")
    assert (state / "machine.toml").read_text() == "cpu_count = 8\n[[["


def test_setting_person_keys_keeps_every_other_key(tmp_path):
    state = tmp_path / "state"
    write(state, render_toml({"schema": 1, "lane_capacity": 2, "cpu_count": 8}))
    set_person_keys(state, rig="on")
    assert read_machine(state) == {"schema": 1, "lane_capacity": 2, "cpu_count": 8, "rig": "on"}


def test_only_the_exact_word_on_turns_rig_on(tmp_path):
    state = tmp_path / "state"
    for word in ("ON", "yes", "true", 1, ""):
        write(state, render_toml({"rig": word, "rig_provider": "vast"}))
        assert rs.settings(state).on is False
    write(state, render_toml({"rig": "on", "rig_provider": "vast"}))
    assert rs.settings(state).on is True and rs.settings(state).provider == "vast"


def test_a_malformed_ceiling_falls_back_to_the_default_never_above_it(tmp_path):
    state = tmp_path / "state"
    write(state, render_toml({"rig": "on", "rig_max_machines": "many", "rig_max_hourly": -1, "rig_max_hours": True}))
    got = rs.settings(state)
    assert (got.max_machines, got.max_hourly, got.max_hours) == (1, 0.60, 8.0)
    write(state, render_toml({"rig": "on", "rig_max_machines": 2, "rig_max_hourly": 1.5, "rig_max_hours": 12}))
    got = rs.settings(state)
    assert (got.max_machines, got.max_hourly, got.max_hours) == (2, 1.5, 12.0)


def test_a_torn_machine_file_reads_as_off(tmp_path):
    state = tmp_path / "state"
    write(state, "rig = \"on\"\n[[[")
    assert rs.settings(state).on is False


def test_the_key_path_is_named_by_the_service_and_follows_xdg_config_home(tmp_path):
    assert rs.key_path("vast", {"XDG_CONFIG_HOME": str(tmp_path)}) == tmp_path / "flotilla" / "rig" / "vast.key"
    assert rs.key_path("vast", {}).parts[-4:] == (".config", "flotilla", "rig", "vast.key")


def key_file(tmp_path, mode=0o600, text="account-key-0123456789"):
    path = tmp_path / "vast.key"
    path.write_text(text + "\n")
    path.chmod(mode)
    return path


def test_a_private_regular_key_file_is_read(tmp_path):
    assert rs.read_key(key_file(tmp_path)) == "account-key-0123456789"


@pytest.mark.parametrize("mode", [0o640, 0o604, 0o644])
def test_a_key_others_can_read_is_refused(tmp_path, mode):
    with pytest.raises(rs.KeyRefused, match="chmod 600"):
        rs.read_key(key_file(tmp_path, mode))


def test_a_symlinked_key_is_refused(tmp_path):
    target = key_file(tmp_path)
    link = tmp_path / "link"
    link.symlink_to(target)
    with pytest.raises(rs.KeyRefused, match="not a regular file"):
        rs.read_key(link)


def test_a_missing_or_empty_key_is_refused_naming_its_place(tmp_path):
    with pytest.raises(rs.KeyRefused, match="vast.key"):
        rs.read_key(tmp_path / "vast.key")
    with pytest.raises(rs.KeyRefused, match="empty"):
        rs.read_key(key_file(tmp_path, text=""))


def test_the_machine_key_is_made_once_private_with_a_backup(tmp_path):
    state = tmp_path / "state"
    first = rs.machine_key(state)
    assert len(first) == 12 and all(ch in "0123456789abcdef" for ch in first)
    assert rs.machine_key(state) == first
    for name in ("machine-key", "machine-key.bak"):
        path = state / "rig" / name
        assert path.read_text().strip() == first and stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_a_damaged_machine_key_is_recovered_from_its_backup(tmp_path):
    state = tmp_path / "state"
    first = rs.machine_key(state)
    (state / "rig" / "machine-key").write_text("garbage; rm -rf\n")
    assert rs.machine_key(state) == first
    assert (state / "rig" / "machine-key").read_text().strip() == first


def test_a_damaged_machine_key_with_nothing_to_recover_from_is_refused_not_replaced(tmp_path):
    state = tmp_path / "state"
    (state / "rig").mkdir(parents=True)
    (state / "rig" / "machine-key").write_text("garbage\n")
    with pytest.raises(rs.KeyRefused, match="machine key"):
        rs.machine_key(state)
    assert (state / "rig" / "machine-key").read_text() == "garbage\n"


def test_labels_are_made_and_read_back():
    assert rs.label("0123456789ab", "m3") == "flotilla:0123456789ab:m3"
    assert rs.key_of_label("flotilla:0123456789ab:m3") == ("0123456789ab", "m3")
    for other in ("", "echoes render box", "flotilla:short:m1", "flotilla:0123456789ab:", "flotilla:0123456789ab:x1"):
        assert rs.key_of_label(other) is None


def test_a_state_directory_copied_from_another_host_refuses_its_key(tmp_path, monkeypatch):
    """Two machines with one key would reap each other's instances as orphans (final review of 0.8.0, I-2)."""
    state = tmp_path / "state"
    monkeypatch.setattr(rs, "HOST", lambda: "host-a")
    first = rs.machine_key(state)
    assert rs.machine_key(state) == first
    monkeypatch.setattr(rs, "HOST", lambda: "host-b")
    with pytest.raises(rs.KeyRefused, match="another machine"):
        rs.machine_key(state)


def test_a_key_made_before_hosts_were_recorded_adopts_this_host(tmp_path, monkeypatch):
    state = tmp_path / "state"
    monkeypatch.setattr(rs, "HOST", lambda: "host-a")
    first = rs.machine_key(state)
    (state / "rig" / "machine-key.host").unlink()
    assert rs.machine_key(state) == first and (state / "rig" / "machine-key.host").read_text().strip() == "host-a"


def test_the_allowed_images_default_to_the_shipped_one_and_are_the_persons(tmp_path):
    state = tmp_path / "state"
    assert rs.settings(state).images == (rs.DEFAULT_IMAGE,)
    write(state, render_toml({"rig": "on", "rig_images": ["ghcr.io/me/shoot:1.2", "bad; rm", 7]}))
    assert rs.settings(state).images == ("ghcr.io/me/shoot:1.2",)
    write(state, render_toml({"rig": "on", "rig_images": []}))
    assert rs.settings(state).images == ()
    assert "rig_images" in PERSON_KEYS


def test_the_suggested_open_line_is_two_hours_and_one_dollar_on_a_fresh_machine(tmp_path):
    got = rs.settings(tmp_path / "state")
    assert (got.default_hours, got.default_budget) == (2.0, 1.0)
    assert rs.open_line(got, "r1") == "! flotilla rig open --hours 2 --budget 1 --for r1"


def test_the_person_sets_the_suggested_hours_and_budget_and_measuring_keeps_them(tmp_path):
    state = tmp_path / "state"
    write_machine(state, {"schema": 1})
    set_person_keys(state, rig_default_hours=4, rig_default_budget=3.5)
    write_machine(state, {"schema": 1, "cpu_count": 8})
    got = rs.settings(state)
    assert (got.default_hours, got.default_budget) == (4.0, 3.5)
    assert rs.open_line(got, "r5") == "! flotilla rig open --hours 4 --budget 3.5 --for r5"
    assert {"rig_default_hours", "rig_default_budget"} <= set(PERSON_KEYS)


@pytest.mark.parametrize("value", ['"four"', "-1", "0", "true", "inf", "nan", "[2]"])
def test_a_malformed_suggestion_falls_back_to_its_default(tmp_path, value):
    state = tmp_path / "state"
    write(state, f"rig = \"on\"\nrig_default_hours = {value}\nrig_default_budget = {value}\n")
    got = rs.settings(state)
    assert (got.default_hours, got.default_budget) == (2.0, 1.0)


def test_the_suggested_hours_are_never_above_the_hours_ceiling(tmp_path):
    state = tmp_path / "state"
    write(state, render_toml({"rig": "on", "rig_default_hours": 10, "rig_max_hours": 8}))
    assert rs.open_line(rs.settings(state), "r2") == "! flotilla rig open --hours 8 --budget 1 --for r2"
    write(state, render_toml({"rig": "on", "rig_max_hours": 1.5}))
    assert rs.open_line(rs.settings(state), "r2") == "! flotilla rig open --hours 1.5 --budget 1 --for r2"


def test_the_pasted_numbers_read_back_as_exactly_the_numbers_meant(tmp_path):
    """A rounded print could land above the ceiling, and `rig open` would refuse the very line it was given."""
    state = tmp_path / "state"
    write(state, render_toml({"rig": "on", "rig_max_hours": 1.9999996, "rig_default_budget": 0.1234567}))
    line = rs.open_line(rs.settings(state), "r3").split()
    assert float(line[line.index("--hours") + 1]) == 1.9999996
    assert float(line[line.index("--budget") + 1]) == 0.1234567


def test_an_infinite_ceiling_is_malformed_not_unlimited(tmp_path):
    """TOML spells infinity `inf`; read as a ceiling it would be no ceiling at all, so doubt lowers it."""
    state = tmp_path / "state"
    write(state, "rig = \"on\"\nrig_max_machines = inf\nrig_max_hourly = inf\nrig_max_hours = inf\n")
    got = rs.settings(state)
    assert (got.max_machines, got.max_hourly, got.max_hours) == (1, 0.60, 8.0)
