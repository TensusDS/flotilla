import hashlib
import io
import os
import subprocess
import sys
import tarfile
import time

import pytest

from flotilla.rig import remote
from rigssh import machine, tagged

linux = pytest.mark.skipif(sys.platform != "linux", reason="the machine's scripts use GNU tools and /proc")
T1, T2 = "j1-aaaaaaaa", "j10-bbbbbbbb"


def ssh(tmp_path, script, *args, stdin=None, timeout=20):
    argv = remote.ssh_argv("h:1", tmp_path / "k", tmp_path / "kh", remote.line(script, *args), stdin=stdin is not None)
    if stdin is None:
        return subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout)
    return subprocess.run(argv, input=stdin, capture_output=True, timeout=timeout)


def run_line(box, tag, *argv, status=None, pairs=()):
    return (remote.RUN, box / "work/P/runs" / tag, status or box / "st", tag, box / "work/cache", "600",
            str(len(pairs)), *pairs, *argv)


def tar_bytes(files):
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w") as t:
        for name, text in files.items():
            data = text.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
    return out.getvalue()


def digest(data):
    return hashlib.sha256(data).hexdigest(), str(len(data))


@pytest.mark.parametrize("bad", ["-oProxyCommand=x:22", "host:0", "host:99999", "ho st:22", ":22", "host", "",
                                 "h;id:22"])
def test_an_address_that_is_not_one_is_refused(bad):
    with pytest.raises(ValueError):
        remote.address(bad)


def test_ssh_reads_no_config_and_forwards_nothing(tmp_path):
    argv = remote.ssh_argv("ssh4.vast.ai:30001", tmp_path / "k", tmp_path / "m1", "true")
    assert argv[argv.index("-F") + 1] == "/dev/null" and "-a" in argv and "ClearAllForwardings=yes" in argv
    assert "BatchMode=yes" in argv and f"UserKnownHostsFile={tmp_path / 'm1'}" in argv and "-n" in argv
    assert argv[-2:] == ["root@ssh4.vast.ai", "true"] and argv[argv.index("-p") + 1] == "30001"
    assert "-n" not in remote.ssh_argv("ssh4.vast.ai:30001", tmp_path / "k", tmp_path / "m1", "true", stdin=True)


@linux
def test_a_hostile_argv_arrives_as_written(tmp_path, monkeypatch):
    box = machine(tmp_path, monkeypatch)
    (box / "work/P/runs" / T1).mkdir(parents=True)
    argv = ["printf", "%s|", "a b", "$HOME", "$(id)", "x;y", "-n", "'q'", '"d"', "\\", "{a,b}", "X=1"]
    out = ssh(tmp_path, *run_line(box, T1, *argv))
    assert out.stdout.decode() == "".join(f"{a}|" for a in argv[2:])


@linux
def test_only_the_named_env_reaches_the_command(tmp_path, monkeypatch):
    box = machine(tmp_path, monkeypatch)
    (box / "work/P/runs" / T1).mkdir(parents=True)
    monkeypatch.setenv("FLOTILLA_LEAK", "secret")
    out = ssh(tmp_path, *run_line(box, T1, "sh", "-c", 'echo "$SCENE ${FLOTILLA_LEAK:-none} $FLOTILLA_RUN"',
                                  pairs=("SCENE=night",)))
    assert out.stdout.decode().strip() == f"night none {T1}"


@linux
def test_the_status_file_carries_the_code_and_the_measurements(tmp_path, monkeypatch):
    box = machine(tmp_path, monkeypatch)
    (box / "work/P/runs" / T1).mkdir(parents=True)
    out = ssh(tmp_path, *run_line(box, T1, "sh", "-c", "exit 7"))
    status = ssh(tmp_path, remote.STATUS, box / "st").stdout.decode()
    assert out.returncode == 7 and status.startswith("exit=7 ") and "peak_kb=" in status and "cpu_s=" in status


@linux
def test_a_command_past_the_remote_limit_reads_timeout(tmp_path, monkeypatch):
    box = machine(tmp_path, monkeypatch)
    (box / "work/P/runs" / T1).mkdir(parents=True)
    line = list(run_line(box, T1, "sleep", "30"))
    line[5] = "1"
    ssh(tmp_path, *line)
    assert ssh(tmp_path, remote.STATUS, box / "st").stdout.decode().startswith("exit=timeout ")


@linux
def test_receive_checks_the_stream_before_it_marks_complete(tmp_path, monkeypatch):
    box = machine(tmp_path, monkeypatch)
    rev = box / "work/P/rev/abc"
    data = tar_bytes({"a.txt": "1"})
    assert ssh(tmp_path, remote.CHECK, rev).stdout.decode().startswith("absent")
    cut = ssh(tmp_path, remote.RECEIVE, rev, *digest(data), stdin=data[:512])
    assert cut.returncode == 65 and ssh(tmp_path, remote.CHECK, rev).stdout.decode().startswith("absent")
    assert ssh(tmp_path, remote.RECEIVE, rev, *digest(data), stdin=data).returncode == 0
    assert ssh(tmp_path, remote.CHECK, rev).stdout.decode().startswith("present")
    run = box / "work/P/runs" / T1
    assert ssh(tmp_path, remote.STAGE, rev, run).returncode == 0 and (run / "a.txt").read_text() == "1"
    assert not (run / ".flotilla-complete").exists()


@linux
def test_a_second_receive_of_one_revision_does_not_nest(tmp_path, monkeypatch):
    box = machine(tmp_path, monkeypatch)
    rev = box / "work/P/rev/abc"
    data = tar_bytes({"a.txt": "1"})
    ssh(tmp_path, remote.RECEIVE, rev, *digest(data), stdin=data)
    ssh(tmp_path, remote.RECEIVE, rev, *digest(data), stdin=data)
    assert sorted(p.name for p in rev.iterdir()) == [".flotilla-complete", "a.txt"]


def start(tmp_path, box, tag, *argv, status):
    line = remote.line(*run_line(box, tag, *argv, status=status))
    return subprocess.Popen(remote.ssh_argv("h:1", tmp_path / "k", tmp_path / "kh", line), stdin=subprocess.DEVNULL)


def wait_tagged(box, tag, count):
    end = time.time() + 10
    while len(tagged(box, tag, wait=0)) < count and time.time() < end:
        time.sleep(0.05)


@linux
def test_stop_ends_the_group_and_the_tagged(tmp_path, monkeypatch):
    box = machine(tmp_path, monkeypatch)
    (box / "work/P/runs" / T1).mkdir(parents=True)
    child = start(tmp_path, box, T1, "sh", "-c", "setsid sleep 60 & sleep 60", status=box / "st")
    wait_tagged(box, T1, 3)
    assert ssh(tmp_path, remote.STOP, box / "st", T1).returncode == 0
    child.wait(15)
    assert tagged(box, T1) == []
    assert ssh(tmp_path, remote.STATUS, box / "st").stdout.decode().startswith("exit=stopped ")


@linux
def test_stop_of_one_run_leaves_its_neighbour(tmp_path, monkeypatch):
    box = machine(tmp_path, monkeypatch)
    for tag in (T1, T2):
        (box / "work/P/runs" / tag).mkdir(parents=True)
    a = start(tmp_path, box, T1, "sleep", "60", status=box / "st1")
    b = start(tmp_path, box, T2, "sleep", "60", status=box / "st2")
    wait_tagged(box, T1, 1)
    wait_tagged(box, T2, 1)
    ssh(tmp_path, remote.STOP, box / "st1", T1)
    a.wait(15)
    assert tagged(box, T1) == [] and tagged(box, T2, wait=0) != []
    ssh(tmp_path, remote.STOP, box / "st2", T2)
    b.wait(15)


@linux
def test_a_stop_before_the_run_wrote_its_group_still_finds_it_by_its_tag(tmp_path, monkeypatch):
    box = machine(tmp_path, monkeypatch)
    (box / "work/P/runs" / T1).mkdir(parents=True)
    child = start(tmp_path, box, T1, "sleep", "60", status=box / "st")
    end, said = time.time() + 5, ""
    while time.time() < end:
        said = ssh(tmp_path, remote.STOP, box / "st", T1).stdout.decode()
        if said.strip() != "stopped 0":
            break
    child.wait(15)
    assert said.strip() != "stopped 0" and tagged(box, T1) == []


def test_readings_report_memory_cpus_and_disk(tmp_path, monkeypatch):
    if sys.platform != "linux":
        pytest.skip("reads /proc")
    box = machine(tmp_path, monkeypatch)
    line = ssh(tmp_path, remote.READINGS, box / "work").stdout.decode()
    fields = dict(item.split("=") for item in line.split())
    assert int(fields["mem_kb"]) > 0 and int(fields["cpus"]) > 0 and int(fields["disk_free_mb"]) >= 0
    assert fields["gpu_free_mb"] == "-1"          # no nvidia-smi on the test machine


@linux
def test_prune_keeps_what_is_recent_or_running(tmp_path, monkeypatch):
    box = machine(tmp_path, monkeypatch)
    project = box / "work/P"
    for sha in ("old", "new"):
        (project / "rev" / sha).mkdir(parents=True)
    old = time.time() - 3 * 86400
    os.utime(project / "rev" / "old", (old, old))
    (project / "runs" / "stale").mkdir(parents=True)
    os.utime(project / "runs" / "stale", (old, old))
    out = ssh(tmp_path, remote.PRUNE, project, "0", box / "work/cache", "0")
    assert out.returncode == 0 and out.stdout.decode().startswith("free_mb=")
    assert sorted(p.name for p in (project / "rev").iterdir()) == ["new"]
    assert not (project / "runs" / "stale").exists()
