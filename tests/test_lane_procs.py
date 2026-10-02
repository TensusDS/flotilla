import os
import subprocess

from flotilla.lane import procs


def write_proc(root, pid, ppid, command, *, utime=0, stime=0, start=100, name="python"):
    folder = root / str(pid)
    folder.mkdir(parents=True)
    after = ["S", str(ppid)] + ["0"] * 9 + [str(utime), str(stime)] + ["0"] * 6 + [str(start), "0", "0"]
    (folder / "stat").write_text(f"{pid} ({name}) " + " ".join(after) + "\n", encoding="utf-8")
    (folder / "cmdline").write_bytes(command.replace(" ", "\0").encode() + b"\0")


def fake_procfs(tmp_path):
    root = tmp_path / "proc"
    root.mkdir()
    (root / "uptime").write_text("1000.00 500.00\n", encoding="utf-8")
    write_proc(root, 10, 1, "bash -l")
    write_proc(root, 20, 10, "uv run pytest -q", utime=150, stime=50, start=90000, name="py (test) x")
    write_proc(root, 30, 20, "python -m pytest -q", start=95000)
    return procs.ProcessTable("procfs", proc_root=root)


def test_procfs_lists_pid_parent_and_command(tmp_path):
    table = fake_procfs(tmp_path)
    found = {proc.pid: proc for proc in table.list()}
    assert (found[20].ppid, found[20].command) == (10, "uv run pytest -q")


def test_procfs_reads_cpu_age_and_ancestors(tmp_path):
    table = fake_procfs(tmp_path)
    assert table.cpu_seconds(20) == 200 / procs.CLK_TCK
    assert table.age_seconds(20) == 1000.0 - 90000 / procs.CLK_TCK
    assert table.ancestors(30) == [30, 20, 10]


def test_a_start_mark_tells_a_reused_pid_from_the_same_process(tmp_path):
    table = fake_procfs(tmp_path)
    mark = table.start_mark(20)
    assert table.alive(20, mark) is True
    assert table.alive(20, "12345") is False
    assert table.alive(99, mark) is False
    assert table.alive(None, "") is True and table.alive(20, "") is True


def test_ps_times_are_parsed_in_every_form():
    assert procs.parse_clock("01:02:03") == 3723
    assert procs.parse_clock("1-00:00:01") == 86401
    assert procs.parse_clock("0:01.50") == 1.5
    assert procs.parse_clock("") is None and procs.parse_clock("x:y") is None


def test_ps_lists_the_table_from_its_output():
    def run(cmd, **kwargs):
        assert cmd[:2] == ["ps", "-A"]
        return subprocess.CompletedProcess(cmd, 0, "   10     1 bash -l\n   20    10 uv run pytest -q\n", "")
    table = procs.ProcessTable("ps", run=run)
    assert [(p.pid, p.ppid, p.command) for p in table.list()] == [(10, 1, "bash -l"), (20, 10, "uv run pytest -q")]


def test_an_unreadable_table_is_none_not_empty(tmp_path):
    missing = procs.ProcessTable("procfs", proc_root=tmp_path / "nope")
    assert missing.list() is None
    failing = procs.ProcessTable("ps", run=lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", "denied"))
    assert failing.list() is None


def test_the_real_ps_answers_about_this_process():
    table = procs.ProcessTable("ps")
    assert table.parent(os.getpid()) == os.getppid()
    assert table.start_mark(os.getpid())
    assert table.cpu_seconds(os.getpid()) is not None


def test_ps_is_asked_in_a_fixed_locale_and_time_zone():
    seen = {}

    def run(cmd, **kwargs):
        seen["env"] = kwargs.get("env") or {}
        return subprocess.CompletedProcess(cmd, 0, "Sat Sep 27 13:00:00 2026\n", "")
    procs.ProcessTable("ps", run=run).start_mark(123)
    assert seen["env"].get("LC_ALL") == "C" and seen["env"].get("TZ") == "UTC"


def test_a_process_that_has_ended_but_is_not_reaped_is_not_alive(tmp_path):
    """A run that ended stays in /proc, with its start mark, until its parent reaps it; its booking must not read as
    held by a live run (found writing `lane stop`)."""
    table = fake_procfs(tmp_path)
    mark = table.start_mark(30)
    assert table.alive(30, mark)
    stat = tmp_path / "proc" / "30" / "stat"
    stat.write_text(stat.read_text(encoding="utf-8").replace(") S ", ") Z "), encoding="utf-8")
    assert not table.alive(30, mark)
