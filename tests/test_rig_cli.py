import datetime as dt
import io
import subprocess
import time
from contextlib import redirect_stdout

import pytest

from flotilla import cli
from flotilla.core import paths
from flotilla.core.storage import LocalLogStore
from flotilla.onboard.machine import read_machine
from flotilla.onboard.tomlw import render_toml
from flotilla.rig import commands, cron, health
from flotilla.rig import journal as j
from flotilla.rig import settings as rs
from flotilla.rig.providers import vast as vast_adapter
from rigkit import FakeVast, fake_key

T0 = dt.datetime(2026, 10, 6, 20, 0, tzinfo=dt.timezone.utc)


class Table:
    def __init__(self):
        self.text = None

    def __call__(self, argv, *, input=None, **kwargs):
        if argv == ["crontab", "-l"]:
            if self.text is None:
                return subprocess.CompletedProcess(argv, 1, "", "no crontab for max")
            return subprocess.CompletedProcess(argv, 0, self.text, "")
        self.text = input
        return subprocess.CompletedProcess(argv, 0, "", "")


@pytest.fixture
def world(tmp_path, monkeypatch, request):
    monkeypatch.setenv("TZ", "UTC")            # status prints local times; the runner's zone must not matter
    time.tzset()
    request.addfinalizer(time.tzset)           # runs after monkeypatch restores TZ
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    clock = {"at": T0}
    table, fake = Table(), FakeVast()
    fake_key(tmp_path)
    monkeypatch.setattr(vast_adapter, "SEND", fake)
    monkeypatch.setattr(commands, "CRON_RUN", table)
    monkeypatch.setattr(commands, "CLOCK", lambda: clock["at"])
    monkeypatch.setattr(commands, "ALIVE", lambda pid, mark: True)
    monkeypatch.setattr(cron, "interpreter", lambda state, **kw: "/usr/bin/python3")
    return {"clock": clock, "table": table, "fake": fake, "state": paths.state_dir(), "tmp": tmp_path}


def turn(state, word):
    state.mkdir(parents=True, exist_ok=True)
    (state / "machine.toml").write_text(render_toml({"rig": word, "rig_provider": "vast"}), encoding="utf-8")


def run_cli(*args):
    out = io.StringIO()
    with redirect_stdout(out):
        code = cli.main(list(args))
    return code, out.getvalue()


def journal(world):
    return j.Rig(LocalLogStore(world["state"] / "rig"), clock=lambda: world["clock"]["at"])


def machine(world, instance="101", hourly=0.30, hours=3, budget=2.0):
    rig = journal(world)
    rig.open_session("max", "night frames", hours=hours, budget=budget)
    key = rs.machine_key(world["state"])
    item = rig.add_machine("s1", "vast", lambda mid: rs.label(key, mid))
    world["fake"].instances[instance] = {"label": item.label, "dph_total": hourly}
    rig.move(item.id, j.PROVISIONING, instance=instance, gpu="RTX A5000", hourly=hourly, created=T0.isoformat())
    rig.move(item.id, j.READY)
    return rig, item.id


def test_off_says_so_in_one_line_and_asks_nothing(world):
    code, out = run_cli("rig")
    assert code == 0 and out.strip() == rs.OFF_LINE
    assert world["fake"].requests == [] and world["table"].text is None


def test_enable_turns_rig_on_installs_the_launcher_and_disable_turns_it_off(world):
    code, out = run_cli("rig", "enable", "--provider", "vast")
    assert code == 0 and "rig is on" in out
    data = read_machine(world["state"])
    assert (data["rig"], data["rig_provider"]) == ("on", "vast")
    assert (world["state"] / "rig" / "reaper.py").exists()
    code, out = run_cli("rig", "disable")
    assert code == 0 and read_machine(world["state"])["rig"] == "off"


def test_enable_names_a_key_others_can_read(world, tmp_path):
    (tmp_path / "config" / "flotilla" / "rig" / "vast.key").chmod(0o644)
    code, out = run_cli("rig", "enable", "--provider", "vast")
    assert code == 0 and "chmod 600" in out


def test_enable_refuses_an_unknown_provider(world):
    code, out = run_cli("rig", "enable", "--provider", "hetzner")
    assert code == 2 and read_machine(world["state"]) is None


def test_enable_is_refused_to_a_background_session(world, monkeypatch):
    from flotilla.core import caller
    from flotilla.core.census import Session
    monkeypatch.setattr(caller, "calling_sessions", lambda: [Session(
        name="main 3", session_id="bg", kind="background", pid=None, short_id=None, status=None, state=None, cwd="",
        started_at_ms=None)])
    code, out = run_cli("rig", "enable", "--provider", "vast")
    assert code == 2 and "background" in out and read_machine(world["state"]) is None


def test_status_shows_the_session_its_money_the_machine_and_the_reaper(world):
    turn(world["state"], "on")
    rig, mid = machine(world)
    rig.move(mid, j.BUSY, run_pid=10, run_mark="a")
    world["clock"]["at"] = T0 + dt.timedelta(minutes=10)
    run_cli("rig", "reap")
    code, out = run_cli("rig")
    assert code == 0
    assert "rig: on, provider vast; ceilings: 1 machine(s), 0.60 $/h, 8 h" in out
    assert "session s1 open (max: night frames) until 23:00, spent at least 0.05 of 2.00 $" in out
    assert "machine m1 busy: instance 101, RTX A5000, 0.30 $/h, lease until 20:30" in out
    assert "reaper: last pass 20:10" in out


def test_status_says_the_reaper_is_silent(world):
    turn(world["state"], "on")
    machine(world)
    run_cli("rig", "reap")
    world["clock"]["at"] = T0 + dt.timedelta(minutes=13)
    _, out = run_cli("rig")
    assert "REAPER SILENT" in out


def test_reap_installs_the_line_drains_and_removes_the_line_when_nothing_is_left(world):
    turn(world["state"], "on")
    machine(world, hours=1)
    run_cli("rig", "reap")
    assert world["table"].text.count(cron.MARK) == 1
    assert str(world["state"] / "rig" / "reaper.py") in world["table"].text
    world["clock"]["at"] = T0 + dt.timedelta(minutes=61)
    _, first = run_cli("rig", "reap")
    code, second = run_cli("rig", "reap")
    out = first + second
    assert code == 0 and "machine m1 gone" in out and "session s1 closed" in out
    assert cron.MARK not in (world["table"].text or "")


def test_reap_drains_a_live_machine_even_when_rig_is_off(world):
    turn(world["state"], "on")
    machine(world)
    turn(world["state"], "off")
    code, out = run_cli("rig", "reap")
    assert "rig turned off" in out and world["fake"].instances == {}


def test_a_second_reap_while_one_runs_steps_aside(world):
    import fcntl
    turn(world["state"], "on")
    (world["state"] / "rig").mkdir(parents=True, exist_ok=True)
    with open(world["state"] / "rig" / "reap.lock", "a") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        code, out = run_cli("rig", "reap")
    assert code == 0 and "another reaper pass is running" in out


def test_a_damaged_journal_turns_the_pass_into_the_emergency(world):
    turn(world["state"], "on")
    machine(world)
    path = world["state"] / "rig" / "rig.jsonl"
    path.write_text("{broken\n" + path.read_text())
    code, out = run_cli("rig", "reap")
    assert code == 1 and "DAMAGED" in out and world["fake"].instances == {}


def test_reap_exits_one_when_a_machine_is_stuck(world):
    turn(world["state"], "on")
    machine(world)
    world["fake"].sticky.add("101")
    world["clock"]["at"] = T0 + dt.timedelta(minutes=31)
    codes = [run_cli("rig", "reap")[0] for _ in range(4)]
    assert codes[-1] == 1
    _, out = run_cli("rig")
    assert "STUCK" in out and "m1" in out


def test_a_pass_that_could_not_ask_the_service_is_shown_as_failed(world):
    turn(world["state"], "on")
    machine(world)
    world["fake"].listing_status = 503
    code, _ = run_cli("rig", "reap")
    _, out = run_cli("rig")
    assert code == 1 and "FAILED" in out and "503" in out


def test_a_damaged_machine_key_still_drains_but_destroys_nothing(world):
    turn(world["state"], "on")
    machine(world)
    for name in ("machine-key", "machine-key.bak"):
        (world["state"] / "rig" / name).write_text("garbage\n")
    world["clock"]["at"] = T0 + dt.timedelta(minutes=31)
    code, out = run_cli("rig", "reap")
    assert code == 1 and "damaged" in out and "101" in world["fake"].instances
    assert journal(world).machines()["m1"].state == j.DRAINING


def test_enable_refuses_a_torn_machine_file(world):
    world["state"].mkdir(parents=True, exist_ok=True)
    (world["state"] / "machine.toml").write_text("cpu_count = 8\n[[[")
    code, out = run_cli("rig", "enable", "--provider", "vast")
    assert code == 2 and (world["state"] / "machine.toml").read_text() == "cpu_count = 8\n[[["


def test_doctor_warns_when_flotilla_is_installed_per_project(world):
    import json as _json
    turn(world["state"], "on")
    plugins = world["tmp"] / "claude" / "plugins" / "installed_plugins.json"
    plugins.parent.mkdir(parents=True)
    plugins.write_text(_json.dumps({"version": 2, "plugins": {"flotilla@flotilla": [
        {"scope": "project", "installPath": "/x", "version": "0.8.0"}]}}))
    env = {"CLAUDE_CONFIG_DIR": str(world["tmp"] / "claude")}
    found = health.findings(world["state"], now=T0, run=world["table"], env=env)
    assert any(status == "warn" and "per project" in detail for status, detail, _ in found)
    plugins.write_text(_json.dumps({"version": 2, "plugins": {"flotilla@flotilla": [
        {"scope": "user", "installPath": "/x", "version": "0.8.0"}]}}))
    found = health.findings(world["state"], now=T0, run=world["table"], env=env)
    assert not any("per project" in detail for _, detail, _ in found)


def test_doctor_names_a_reaper_line_whose_launcher_is_gone(world):
    turn(world["state"], "on")
    machine(world)
    run_cli("rig", "reap")
    (world["state"] / "rig" / "reaper.py").unlink()
    found = health.findings(world["state"], now=T0, run=world["table"])
    assert any(status == "fail" and "is gone" in detail for status, detail, _ in found)


def test_doctor_names_a_silent_reaper(world):
    turn(world["state"], "on")
    machine(world)
    found = health.findings(world["state"], now=T0 + dt.timedelta(minutes=30), run=world["table"])
    assert any(status == "fail" and "silent" in detail for status, detail, _ in found)


def test_the_provider_key_never_reaches_the_output_or_the_journal(world):
    turn(world["state"], "on")
    world["fake"].leak = "instance-key-abcdef0123"
    machine(world)
    world["clock"]["at"] = T0 + dt.timedelta(minutes=31)
    texts = [run_cli("rig", "reap")[1], run_cli("rig")[1]]
    world["fake"].listing_status = 401
    texts.append(run_cli("rig", "reap")[1])
    texts.append((world["state"] / "rig" / "rig.jsonl").read_text())
    for text in texts:
        assert "instance-key-abcdef0123" not in text and "account-key-0123456789" not in text


def test_a_crash_inside_a_pass_still_marks_that_the_pass_ran(world, monkeypatch):
    """A flotilla that ran and crashed is not a flotilla that is gone: the launcher must not count it as a miss and
    fall to its last resort (final review of 0.8.0, I-1)."""
    from flotilla.rig import reaper
    turn(world["state"], "on")
    machine(world)

    def broken(*args, **kwargs):
        raise RuntimeError("something unforeseen")
    monkeypatch.setattr(reaper, "reap", broken)
    code, out = run_cli("rig", "reap")
    ok, note = health.last_outcome(world["state"])
    assert code == 1 and health.last_reap(world["state"]) is not None
    assert not ok and "RuntimeError" in note and "something unforeseen" in out


# rig down: a seat gives an idle machine back at once (0.12.0)

def queued(rig, pid=5):
    return rig.queue_run("s1", who="minor 3", project="p", revision="r", program="node", ladder=(), pid=pid,
                         mark="m")


def test_rig_down_gives_an_idle_machine_back_and_sees_it_gone(world, monkeypatch):
    monkeypatch.setattr(commands, "SLEEP", lambda s: None)
    turn(world["state"], "on")
    machine(world)
    code, out = run_cli("rig", "down", "--as", "minor 3")
    item = journal(world).machines()["m1"]
    assert code == 0 and item.state == j.GONE and "given back" in out and "gone" in out
    assert world["fake"].instances == {}
    assert journal(world).sessions()["s1"].state == j.OPEN          # the session stays open for the next run


def test_rig_down_refuses_while_a_run_is_on_the_machine(world):
    turn(world["state"], "on")
    rig, mid = machine(world)
    run = queued(rig)
    assert rig.start_run(run.id, mid, alive=lambda p, m: True, roomy=True)
    code, out = run_cli("rig", "down", "--as", "minor 3")
    assert code == 2 and "j1" in out and journal(world).machines()[mid].state == j.BUSY


def test_rig_down_refuses_while_a_run_waits_in_the_session(world):
    turn(world["state"], "on")
    rig, mid = machine(world)
    queued(rig)
    code, out = run_cli("rig", "down", "--as", "minor 3")
    assert code == 2 and "waits" in out and journal(world).machines()[mid].state == j.READY


def test_a_machine_given_back_is_never_handed_a_run(world):
    turn(world["state"], "on")
    rig, mid = machine(world)
    assert rig.give_back(mid, "minor 3", alive=lambda p, m: True) == ""
    run = queued(rig)
    assert rig.start_run(run.id, mid, alive=lambda p, m: True, roomy=True) is None


def test_a_machine_with_a_run_started_meanwhile_is_not_given_back(world):
    turn(world["state"], "on")
    rig, mid = machine(world)
    run = queued(rig)
    rig.start_run(run.id, mid, alive=lambda p, m: True, roomy=True)
    assert "j1" in rig.give_back(mid, "minor 3", alive=lambda p, m: True)
    assert journal(world).machines()[mid].state == j.BUSY


def test_rig_down_with_no_machine_says_so(world):
    turn(world["state"], "on")
    code, out = run_cli("rig", "down", "--as", "minor 3")
    assert code == 0 and "no machine" in out


def test_the_seat_posts_say_when_to_give_the_machine_back():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent / "templates" / "posts"
    for post in ("main.md", "minor.md"):
        text = (root / post).read_text(encoding="utf-8")
        assert "flotilla rig down" in text and "5 minutes" in text
