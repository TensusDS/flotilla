import datetime as dt
import io
import json
import os
import signal
import subprocess
import sys
import threading
import time
from contextlib import chdir, redirect_stdout
from pathlib import Path

import pytest

from flotilla import cli
from flotilla.rig import commands, remote
from flotilla.rig import journal as j
from flotilla.rig import run as run_module
from flotilla.rig import settings as rs
from rigssh import drop_after, machine, tagged, unreachable_after_drop
from rigtree import git_tree
from test_rig_up import journal, opened, world  # noqa: F401 - the rig up world: fake vast, fake clock, state

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="the machine's scripts use GNU tools and /proc")
PROFILE = 'schema = 1\n[tests]\nsetup_command = "echo set >> $HOME/setup-count && mkdir -p node_modules"\n'
HERE = Path(__file__).resolve().parent


@pytest.fixture
def box(tmp_path, monkeypatch):
    found = machine(tmp_path, monkeypatch)
    monkeypatch.setattr(run_module, "KEY_PATH", str(tmp_path / "key"))
    monkeypatch.setattr(run_module, "POLL", 0.2)
    monkeypatch.setattr(run_module, "ASK_EVERY", 0.2)
    return found


@pytest.fixture
def tree(tmp_path):
    return git_tree(tmp_path / "tree", {"package.json": "{}", "package-lock.json": "{}", "shoot.sh": "echo",
                                        ".flotilla/project.toml": PROFILE})


def rig_run(world, tree, *argv):
    out = io.StringIO()
    with redirect_stdout(out), chdir(tree):
        code = cli.main(["rig", "run", "--root", str(tree), "--as", "minor 8", *argv])
    return code, out.getvalue()


def raised(world, tree):
    assert commands.raise_machine(world["state"], rs.settings(world["state"]), who="x", why="x", root=str(tree),
                                  wait=60.0, watchdog=45) == 0


def background_rig_run(world, tree, box, tmp_path, *argv, no_room=False):
    env = {**os.environ, "RIG_TEST_SSH": remote.PROGRAM, "RIG_TEST_BASE": remote.BASE, "RIG_TEST_BEAT": remote.BEAT,
           "RIG_TEST_KEY": str(tmp_path / "key"), "PYTHONPATH": f"{HERE.parent}{os.pathsep}{HERE}",
           "RIG_TEST_CLOCK": world["clock"]["at"].isoformat()}
    if no_room:
        env["RIG_TEST_NO_ROOM"] = "1"
    return subprocess.Popen([sys.executable, str(HERE / "rigrun_child.py"), "rig", "run", "--root", str(tree),
                             "--as", "minor 9", *argv], cwd=tree, env=env,
                            stdout=open(tmp_path / "child.log", "ab"), stderr=subprocess.STDOUT)


def rows(world):
    return [json.loads(line) for line in (world["state"] / "rig" / "rig.jsonl").read_text().splitlines()]


def order(world):
    return [(r["kind"], r["id"], r["state"]) for r in rows(world)]


def running(world):
    return sum(1 for r in journal(world).runs().values() if r.state == j.RUNNING)


def wait_for(predicate, seconds=20.0):
    end = time.time() + seconds
    while time.time() < end:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("condition not met in time")


def renewals_after_running(world, run_id):
    found = rows(world)
    start = next(i for i, r in enumerate(found) if r["kind"] == "run" and r["id"] == run_id and r["state"] == "running")
    return sum(1 for r in found[start:] if r["kind"] == "machine" and "lease_until" in r)


def after_drop(box, delay, action):
    """Run `action` `delay` seconds after the stand-in dropped the connection, in a thread."""
    def wait():
        end = time.time() + 60
        while not (box / ".dropped").exists() and time.time() < end:
            time.sleep(0.05)
        time.sleep(delay)
        action()
    thread = threading.Thread(target=wait, daemon=True)
    thread.start()
    return thread


def tag_of(world, run_id):
    return journal(world).runs()[run_id].tag


def test_rig_run_runs_the_argv_as_written(world, box, tree):
    opened(world)
    code, out = rig_run(world, tree, "--", "printf", "%s|", "a b", "$HOME", "$(id)", "{a,b}")
    assert code == 0 and "a b|$HOME|$(id)|{a,b}|" in out


def test_the_remote_exit_code_is_the_runs_and_the_last_line_names_it(world, box, tree):
    opened(world)
    code, out = rig_run(world, tree, "--", "sh", "-c", "exit 75")
    assert code == 75 and out.strip().splitlines()[-1].startswith("flotilla: rig run j1: red (exit 75)")


def test_setup_runs_in_each_run(world, box, tree):
    opened(world)
    assert rig_run(world, tree, "--", "test", "-d", "node_modules")[0] == 0
    assert rig_run(world, tree, "--", "test", "-d", "node_modules")[0] == 0
    assert (box / "root" / "setup-count").read_text().count("set") == 2


def test_artifacts_come_back_even_from_a_red_run(world, box, tree):
    opened(world)
    code, out = rig_run(world, tree, "--get", "shots", "--", "sh", "-c", "mkdir shots && echo f > shots/a.txt && exit 1")
    assert code == 1 and (tree / "shots" / "a.txt").read_text() == "f\n"


def test_a_put_file_reaches_this_run_only(world, box, tree):
    opened(world)
    (tree / "data.bin").write_text("d")
    assert rig_run(world, tree, "--put", "data.bin", "--", "test", "-f", "data.bin")[0] == 0
    assert rig_run(world, tree, "--", "test", "-f", "data.bin")[0] == 1


def test_a_changed_tracked_file_not_put_is_refused_before_any_money(world, box, tree):
    opened(world)
    (tree / "shoot.sh").write_text("changed")
    code, out = rig_run(world, tree, "--", "true")
    assert code == 2 and "shoot.sh" in out and world["fake"].created == 0


@pytest.mark.parametrize("pair", ["API_KEY=x", "GH_TOKEN=x", "db_password=x", "1BAD=x", "=x", "NOEQUALS"])
def test_a_secret_or_malformed_env_is_refused(world, box, tree, pair):
    opened(world)
    code, out = rig_run(world, tree, "--env", pair, "--", "true")
    assert code == 2 and world["fake"].created == 0


def test_two_runs_share_a_machine(world, box, tree, tmp_path):
    opened(world)
    raised(world, tree)
    first = background_rig_run(world, tree, box, tmp_path, "--", "sh", "-c", "sleep 3")
    wait_for(lambda: running(world) == 1)
    code, _ = rig_run(world, tree, "--", "true")
    first.wait(60)
    assert code == 0 and world["fake"].created == 1
    assert order(world).index(("run", "j2", "running")) < order(world).index(("run", "j1", "done"))


def test_a_third_run_waits_for_room(world, box, tree, tmp_path, monkeypatch):
    opened(world)
    monkeypatch.setattr(j, "RUN_SETTLE", dt.timedelta(0))   # room, not settling, must hold the third run
    raised(world, tree)
    monkeypatch.setattr(run_module, "READ",
                        lambda b: {"mem_kb": 0, "mem_total_kb": 0, "cpus": 8, "gpu_free_mb": -1, "gpu_total_mb": -1})
    a = background_rig_run(world, tree, box, tmp_path, "--", "sh", "-c", "sleep 4", no_room=True)
    wait_for(lambda: running(world) == 1)
    b = background_rig_run(world, tree, box, tmp_path, "--", "sh", "-c", "sleep 4", no_room=True)
    wait_for(lambda: running(world) == 2)
    code, _ = rig_run(world, tree, "--", "true")
    a.wait(60)
    b.wait(60)
    first_done = min(order(world).index(("run", rid, "done")) for rid in ("j1", "j2"))
    assert code == 0 and order(world).index(("run", "j3", "running")) > first_done


def test_a_dropped_connection_asks_the_machine(world, box, tree):
    opened(world)
    drop_after(box, "# flotilla-run", 1)
    code, out = rig_run(world, tree, "--get", "shots", "--", "sh", "-c", "sleep 30; mkdir shots")
    run = journal(world).runs()["j1"]
    assert code == 75 and run.verdict == "cut" and journal(world).machines()[run.machine].state == j.READY
    assert tagged(box, run.tag) == []


def test_a_run_that_ended_while_the_connection_was_down_keeps_its_verdict(world, box, tree):
    opened(world)
    drop_after(box, "# flotilla-run", 1)
    unreachable_after_drop(box)
    after_drop(box, 3.0, lambda: (box / ".unreachable").unlink())
    code, out = rig_run(world, tree, "--", "sh", "-c", "sleep 2")
    assert code == 0 and journal(world).runs()["j1"].verdict == "green"


def test_a_machine_that_cannot_be_asked_is_drained_not_handed_on(world, box, tree, monkeypatch):
    opened(world)
    drop_after(box, "# flotilla-run", 1)
    unreachable_after_drop(box)
    monkeypatch.setattr(run_module, "ASK_FOR", 1.0)
    code, out = rig_run(world, tree, "--", "sleep", "5")
    assert code == 75 and journal(world).runs()["j1"].verdict == "lost"
    assert journal(world).machines()["m1"].state == j.DRAINING


def test_a_run_stopped_from_outside_while_cut_is_cut(world, box, tree):
    opened(world)
    drop_after(box, "# flotilla-run", 1)
    unreachable_after_drop(box)

    def stop_from_outside():
        tag = tag_of(world, "j1")
        st = next((box / "work").rglob(f"{tag}.run.running")).with_suffix("")
        # on the machine itself: the stand-in is unreachable after the drop, as the field side is
        subprocess.run(["bash", "-c", remote.line(remote.STOP, st, tag)], capture_output=True, timeout=30)
        wait_for(lambda: st.exists() and st.read_text().startswith("exit=stopped"))
        (box / ".unreachable").unlink()

    after_drop(box, 0.5, stop_from_outside)
    code, out = rig_run(world, tree, "--", "sleep", "30")
    assert code == 75 and journal(world).runs()["j1"].verdict == "cut"


def test_a_run_past_its_ceiling_stops_everything_it_started(world, box, tree, monkeypatch):
    opened(world)
    monkeypatch.setattr(run_module, "CEILING", 3)
    code, out = rig_run(world, tree, "--", "sh", "-c", "setsid sleep 30 & sleep 30")
    run = journal(world).runs()["j1"]
    assert code == 124 and run.verdict == "ceiling" and tagged(box, run.tag) == []


@pytest.mark.parametrize("delay", [0.0, 1.0])   # in the tree's phases, or in the command's
def test_a_killed_rig_run_stops_its_run_on_the_machine(world, box, tree, tmp_path, delay):
    opened(world)
    raised(world, tree)
    child = background_rig_run(world, tree, box, tmp_path, "--", "sleep", "30")
    wait_for(lambda: running(world) == 1)
    time.sleep(delay)
    child.send_signal(signal.SIGTERM)
    time.sleep(0.2)
    child.send_signal(signal.SIGTERM)
    child.wait(60)
    run = journal(world).runs()["j1"]
    assert run.verdict == "stopped" and tagged(box, run.tag) == []
    assert journal(world).machines()["m1"].state == j.READY


def test_the_run_renews_the_lease_and_the_heartbeat(world, box, tree, monkeypatch):
    opened(world)
    raised(world, tree)
    monkeypatch.setattr(run_module, "RENEW", 0.5)
    rig_run(world, tree, "--", "sleep", "3")
    assert (box / "root" / "flotilla-heartbeat").exists() and renewals_after_running(world, "j1") >= 3


def test_no_session_records_a_request_and_spends_nothing(world, box, tree):
    code, out = rig_run(world, tree, "--", "true")
    assert code == 2 and "r1" in out and world["fake"].created == 0


def test_the_run_is_recorded_with_its_measurements(world, box, tree):
    opened(world)
    rig_run(world, tree, "--", "sh", "-c", "head -c 50000000 /dev/zero | tail -c 1 > /dev/null")
    run = journal(world).runs()["j1"]
    assert len(run.revision) == 40 and run.state == j.DONE and run.program == "sh" and run.ladder
    assert None not in (run.seconds, run.cost, run.peak_mb, run.cores)


def test_root_naming_another_tree_is_refused(world, box, tree, tmp_path):
    opened(world)
    other = git_tree(tmp_path / "other", {"x": "1"})
    out = io.StringIO()
    with redirect_stdout(out), chdir(tree):
        code = cli.main(["rig", "run", "--root", str(other), "--as", "minor 8", "--", "true"])
    assert code == 2 and world["fake"].created == 0


def test_a_stop_cut_short_by_a_second_signal_is_sent_again(world, box, tree, monkeypatch):
    opened(world)
    monkeypatch.setattr(run_module, "CEILING", 3)
    real, calls = run_module.Box.call, []

    def call(self, script, *args, **kwargs):
        if script == remote.STOP:
            calls.append(args)
            if len(calls) == 1:
                raise SystemExit(143)          # the second signal, arriving while the first STOP is on its way
        return real(self, script, *args, **kwargs)
    monkeypatch.setattr(run_module.Box, "call", call)
    with pytest.raises(SystemExit):
        rig_run(world, tree, "--", "sleep", "30")
    run = journal(world).runs()["j1"]
    assert len(calls) >= 2 and run.verdict == "stopped" and tagged(box, run.tag) == []


def test_a_client_killed_outright_is_a_cut_not_the_commands_exit(world, box, tree):
    from rigssh import kill_on_drop
    opened(world)
    drop_after(box, "# flotilla-run", 1)
    kill_on_drop(box)
    code, out = rig_run(world, tree, "--", "sleep", "30")
    run = journal(world).runs()["j1"]
    assert code == 75 and run.verdict == "cut" and tagged(box, run.tag) == []


@pytest.mark.parametrize("shared, gpu", [("0", 300), ("1", None)])
def test_a_shared_machines_gpu_figure_is_not_kept_as_the_runs(shared, gpu):
    task = run_module.Run.__new__(run_module.Run)
    task.measured = {}
    task.read_status(f"exit=0 seconds=10 cpu_s=5 peak_kb=2048 gpu_mb=300 shared={shared}", stopped_here=False)
    assert task.measured["gpu_mb"] == gpu and task.measured["cores"] == 0.5 and task.measured["peak_mb"] == 2


def test_a_gone_run_is_stopped_on_its_machine_once(world, box, tree, monkeypatch):
    opened(world)
    raised(world, tree)
    r = journal(world)
    s = next(iter(r.sessions().values()))
    gone = r.queue_run(s.id, who="x", project="P", revision="a" * 40, program="sleep", ladder=(), pid=404, mark="m")
    r.start_run(gone.id, "m1", alive=lambda pid, mark: True, roomy=False)
    r.finish_run(gone.id, "gone", reason="its process is gone")
    real, stops = run_module.Box.call, []

    def call(self, script, *args, **kwargs):
        if script == remote.STOP and gone.tag in args:
            stops.append(args)
        return real(self, script, *args, **kwargs)
    monkeypatch.setattr(run_module.Box, "call", call)
    rig_run(world, tree, "--", "true")
    rig_run(world, tree, "--", "true")
    assert len(stops) == 1 and journal(world).runs()[gone.id].swept


def test_max_lowers_the_ceiling_for_one_run(world, box, tree):
    opened(world)
    code, out = rig_run(world, tree, "--max", "2", "--", "sleep", "30")
    assert code == 124 and journal(world).runs()["j1"].verdict == "ceiling"


def test_max_above_the_profiles_ceiling_is_refused(world, box, tree):
    opened(world)
    code, out = rig_run(world, tree, "--max", "999999", "--", "true")
    assert code == 2 and "max_run_seconds" in out and world["fake"].created == 0


def rig_stop(world, run_id, who):
    out = io.StringIO()
    with redirect_stdout(out):
        code = cli.main(["rig", "stop", run_id, "--as", who])
    return code, out.getvalue()


def test_a_seat_stops_its_own_run(world, box, tree, tmp_path):
    opened(world)
    raised(world, tree)
    child = background_rig_run(world, tree, box, tmp_path, "--", "sleep", "60")
    wait_for(lambda: running(world) == 1)
    time.sleep(1.0)
    code, out = rig_stop(world, "j1", "minor 9")
    child.wait(60)
    run = journal(world).runs()["j1"]
    assert code == 0 and run.verdict == "stopped" and tagged(box, run.tag) == []


def test_a_seat_cannot_stop_another_seats_run(world, box, tree, tmp_path, monkeypatch):
    from flotilla.core import caller
    opened(world)
    monkeypatch.setattr(caller, "person_refusal", lambda what: "this caller is a session, not the person")
    raised(world, tree)
    child = background_rig_run(world, tree, box, tmp_path, "--", "sleep", "20")
    wait_for(lambda: running(world) == 1)
    code, out = rig_stop(world, "j1", "main 2")
    assert code == 2 and "minor 9" in out and journal(world).runs()["j1"].state == j.RUNNING
    child.wait(60)


def test_a_name_the_census_does_not_confirm_stops_nothing_but_the_persons(world, box, tree, tmp_path, monkeypatch):
    # security review of 0.10.4: outside any listed session `--as` is only a word, and the owner's name was enough
    from flotilla.core import caller
    opened(world)
    raised(world, tree)
    child = background_rig_run(world, tree, box, tmp_path, "--", "sleep", "20")
    wait_for(lambda: running(world) == 1)
    monkeypatch.setattr(caller, "person_refusal", lambda what: "this caller is a session, not the person")
    monkeypatch.setattr(caller, "has_terminal", lambda: False)   # a process a session detached: no census, no tty
    code, out = rig_stop(world, "j1", "minor 9")
    assert code == 2 and "census" in out and journal(world).runs()["j1"].state == j.RUNNING
    child.wait(60)


def test_stopping_a_run_whose_process_is_gone_ends_it_on_the_machine(world, box, tree, tmp_path):
    opened(world)
    raised(world, tree)
    child = background_rig_run(world, tree, box, tmp_path, "--", "sleep", "60")
    wait_for(lambda: running(world) == 1)
    time.sleep(1.0)
    child.kill()
    child.wait(10)
    code, out = rig_stop(world, "j1", "minor 9")
    run = journal(world).runs()["j1"]
    assert code == 0 and run.state == j.DONE and run.verdict == "stopped" and tagged(box, run.tag) == []


# review of 0.10.4: the risky branches of `rig stop`

def placed_run(world, pid, mark, slug=""):
    rig = journal(world)
    session = next(s for s in rig.sessions().values() if s.state == j.OPEN)
    machine = next(iter(rig.machines().values()))
    run = rig.queue_run(session.id, who="minor 9", project="p", revision="r", program="sleep", ladder=[], pid=pid,
                        mark=mark)
    assert rig.start_run(run.id, machine.id, alive=lambda p, m: True, roomy=True)
    if slug:
        rig.place_run(run.id, slug)
    return run


def test_a_run_with_no_start_mark_is_never_signalled(world, box, tree, monkeypatch):
    opened(world)
    raised(world, tree)
    bystander = subprocess.Popen(["sleep", "60"])
    try:
        placed_run(world, bystander.pid, "")
        monkeypatch.setattr(commands, "ALIVE", lambda pid, mark: True)
        code, out = rig_stop(world, "j1", "minor 9")
        assert bystander.poll() is None and code == 0 and journal(world).runs()["j1"].verdict == "stopped"
    finally:
        bystander.kill()
        bystander.wait()


def test_a_rig_run_that_outlives_the_wait_is_not_recorded(world, box, tree, monkeypatch):
    from flotilla.lane.procs import ProcessTable
    opened(world)
    raised(world, tree)
    stubborn = subprocess.Popen([sys.executable, "-c", "import signal, time\n"
                                 "signal.signal(signal.SIGTERM, signal.SIG_IGN)\ntime.sleep(60)"])
    try:
        time.sleep(0.5)
        placed_run(world, stubborn.pid, ProcessTable.for_machine().start_mark(stubborn.pid) or "")
        monkeypatch.setattr(commands, "STOP_WAIT", 1.0)
        code, out = rig_stop(world, "j1", "minor 9")
        assert code == 1 and "not recorded" in out and journal(world).runs()["j1"].state == j.RUNNING
    finally:
        stubborn.kill()
        stubborn.wait()


def dead_pid():
    gone = subprocess.Popen(["true"])
    gone.wait()
    return gone.pid


def test_a_stop_the_machine_did_not_take_is_recorded_gone(world, box, tree, monkeypatch):
    from flotilla.rig import run as rig_run
    opened(world)
    raised(world, tree)
    placed_run(world, dead_pid(), "x")
    monkeypatch.setattr(rig_run.Box, "call", lambda self, *a, **k: subprocess.CompletedProcess(a, 255, b"", b""))
    code, out = rig_stop(world, "j1", "minor 9")
    run = journal(world).runs()["j1"]
    assert code == 1 and run.verdict == "gone" and "could not" in out


def test_a_machine_that_cannot_be_asked_leaves_the_run_gone(world, box, tree, monkeypatch):
    from flotilla.rig import run as rig_run

    def lost(self, *a, **k):
        raise rig_run.Lost("the machine's host key changed")
    opened(world)
    raised(world, tree)
    placed_run(world, dead_pid(), "x")
    monkeypatch.setattr(rig_run.Box, "call", lost)
    code, out = rig_stop(world, "j1", "minor 9")
    assert code == 1 and journal(world).runs()["j1"].verdict == "gone"


def test_the_fallback_stop_names_the_runs_own_status_files(world, box, tree, monkeypatch):
    from flotilla.rig import run as rig_run
    called = []
    opened(world)
    raised(world, tree)
    run = placed_run(world, dead_pid(), "x", slug="tree-0123abcd")
    monkeypatch.setattr(rig_run.Box, "call",
                        lambda self, *a, **k: called.append(a) or subprocess.CompletedProcess(a, 0, b"", b""))
    assert rig_stop(world, "j1", "minor 9")[0] == 0
    paths = remote.paths("tree-0123abcd", "r", run.tag)
    assert [c[1] for c in called] == [paths.run_status, paths.setup_status]


def test_a_slug_a_shell_wrote_folds_as_none(world, box, tree):
    opened(world)
    raised(world, tree)
    placed_run(world, dead_pid(), "x", slug="../../etc")
    assert journal(world).runs()["j1"].slug == ""


# 0.11.0: measurements fit for packing

def test_a_machine_without_nvidia_smi_measures_no_gpu(world, box, tree):
    opened(world)
    raised(world, tree)
    code, out = rig_run(world, tree, "--", "true")
    assert code == 0 and journal(world).runs()["j1"].gpu_mb is None


def test_a_shared_gpu_figure_is_kept_apart():
    task = run_module.Run.__new__(run_module.Run)
    task.measured = {}
    task.read_status("exit=0 seconds=10 cpu_s=20 peak_kb=4096 gpu_mb=3000 shared=1", stopped_here=False)
    assert task.measured["gpu_mb"] is None and task.measured["gpu_shared_mb"] == 3000
