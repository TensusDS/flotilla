import errno
import signal
import subprocess
import threading
from pathlib import Path

from flotilla.lane import freeze


def done(code=0, out=""):
    return subprocess.CompletedProcess([], code, out, "")


def user_cgroup(tmp_path, *, controllers="cpu memory pids"):
    """A user manager's cgroup as /proc/self/cgroup names it, with its files under a fake /sys/fs/cgroup."""
    proc_self = tmp_path / "self-cgroup"
    proc_self.write_text("0::/user.slice/user-1000.slice/user@1000.service/app.slice/x.scope\n")
    root = tmp_path / "cg"
    manager = root / "user.slice/user-1000.slice/user@1000.service"
    manager.mkdir(parents=True)
    (manager / "cgroup.controllers").write_text(controllers + "\n")
    (manager / "cgroup.freeze").write_text("0\n")
    return proc_self, root


def fake_cgroup(tmp_path, *, reclaim_raises=None):
    root = tmp_path / "cg"
    scope = root / "user.slice/flotilla-lane-b1-9-1.scope"
    scope.mkdir(parents=True)
    (scope / "cgroup.freeze").write_text("0\n")
    (scope / "cgroup.events").write_text("populated 1\nfrozen 0\n")
    reclaim = scope / "memory.reclaim"
    if reclaim_raises is None:
        reclaim.write_text("")
    else:
        reclaim.mkdir()   # writing a directory raises; the test's patched writer raises the errno instead
    return root


def fake_systemctl(argv, **kw):
    if argv[:2] == ["systemctl", "--user"] and "ControlGroup" in argv:
        return done(0, "/user.slice/flotilla-lane-b1-9-1.scope\n")
    return done(0)


def test_no_user_systemd_means_signal():
    kind, why = freeze.backend(which=lambda name: None, platform="linux")
    assert kind == freeze.SIGNAL and "systemd-run" in why


def test_macos_is_signal_and_the_setting_turns_it_off():
    assert freeze.backend(platform="darwin")[0] == freeze.SIGNAL
    kind, why = freeze.backend(setting=False, platform="darwin")
    assert kind == freeze.OFF and "lane_freeze = false" in why


def test_a_user_manager_with_memory_and_a_working_scope_is_scope(tmp_path):
    proc_self, root = user_cgroup(tmp_path)
    kind, _why = freeze.backend(which=lambda n: "/usr/bin/" + n, run=lambda argv, **kw: done(0),
                                proc_self=proc_self, root=root, platform="linux")
    assert kind == freeze.SCOPE


def test_a_scope_probe_that_fails_falls_back_to_signal(tmp_path):   # review focus 1
    proc_self, root = user_cgroup(tmp_path)
    kind, why = freeze.backend(which=lambda n: "/usr/bin/" + n, run=lambda argv, **kw: done(1),
                               proc_self=proc_self, root=root, platform="linux")
    assert kind == freeze.SIGNAL and "scope" in why


def test_a_session_beside_the_user_manager_finds_it(tmp_path):   # an ssh login: session-N.scope
    proc_self, root = user_cgroup(tmp_path)
    proc_self.write_text("0::/user.slice/user-1000.slice/session-6000.scope\n")
    kind, _why = freeze.backend(which=lambda n: "/usr/bin/" + n, run=lambda argv, **kw: done(0),
                                proc_self=proc_self, root=root, platform="linux", uid=1000)
    assert kind == freeze.SCOPE


def test_no_memory_controller_in_the_user_manager_means_signal(tmp_path):
    proc_self, root = user_cgroup(tmp_path, controllers="cpu pids")
    kind, why = freeze.backend(which=lambda n: "/usr/bin/" + n, run=lambda argv, **kw: done(0),
                               proc_self=proc_self, root=root, platform="linux")
    assert kind == freeze.SIGNAL and "memory" in why


def test_eagain_from_reclaim_is_not_an_error(tmp_path, monkeypatch):
    root = fake_cgroup(tmp_path, reclaim_raises=errno.EAGAIN)
    scope = root / "user.slice/flotilla-lane-b1-9-1.scope"
    real_write = Path.write_text

    def write(self, text, *a, **kw):
        if self.name == "memory.reclaim":
            raise OSError(errno.EAGAIN, "Resource temporarily unavailable")
        if self.name == "cgroup.freeze":
            (scope / "cgroup.events").write_bytes(f"populated 1\nfrozen {text.strip()}\n".encode())
        return real_write(self, text, *a, **kw)
    monkeypatch.setattr(Path, "write_text", write)
    s = freeze.Slot(freeze.SCOPE, root=root, run=fake_systemctl, tree_mb=lambda pid: 900)
    s.contain(["pytest"], booking="b1")
    s.attach(4242)
    found = s.freeze(512)
    assert found["freeze_kind"] == "scope" and found["held_mb"] == 900 and s.frozen()
    assert (scope / "cgroup.freeze").read_text().strip() == "1"
    s.thaw()
    assert (scope / "cgroup.freeze").read_text().strip() == "0" and not s.frozen()


def test_an_idle_slot_is_frozen_at_once_and_blocks_the_next_command():
    s = freeze.Slot(freeze.SIGNAL)
    s.freeze(0)
    assert s.frozen()
    threading.Timer(0.2, s.thaw).start()
    s.wait_thawed(sleep=lambda seconds: None if not s.frozen() else threading.Event().wait(0.05))
    assert not s.frozen()


def test_a_command_inside_a_lane_scope_is_not_wrapped_again(tmp_path):
    cg = tmp_path / "cgroup"
    cg.write_text("0::/user.slice/user-1000.slice/user@1000.service/app.slice/flotilla-lane-b3-77-1.scope\n")
    s = freeze.Slot(freeze.SCOPE, proc_self=cg)
    assert s.contain(["pytest"], booking="b9") == ["pytest"]


def test_a_command_is_wrapped_in_a_scope_of_its_own(tmp_path):
    cg = tmp_path / "cgroup"
    cg.write_text("0::/user.slice/user-1000.slice/user@1000.service/app.slice/x.scope\n")
    s = freeze.Slot(freeze.SCOPE, proc_self=cg)
    argv = s.contain(["pytest", "-q"], booking="b9")
    assert argv[:5] == ["systemd-run", "--user", "--scope", "--quiet", "--collect"]
    assert argv[5].startswith("--unit=flotilla-lane-b9-") and argv[-3:] == ["--", "pytest", "-q"]


def test_a_signal_stop_reaches_a_child_in_a_group_of_its_own(monkeypatch):
    table = {10: (1, 10, 100), 11: (10, 11, 200), 12: (1, 12, 300)}   # 11 left its parent's group, 12 is a stranger
    sent = []
    monkeypatch.setattr(freeze.os, "kill", lambda pid, number: sent.append((pid, number)))
    monkeypatch.setattr(freeze.os, "killpg", lambda pid, number: sent.append(("group", pid, number)))
    s = freeze.Slot(freeze.SIGNAL, table=lambda: table, tree_mb=lambda pid: 300)
    s.attach(10)
    assert s.freeze(0)["freeze_kind"] == "signal"
    stopped = {pid for pid, number in [x for x in sent if len(x) == 2] if number == signal.SIGSTOP}
    assert stopped == {10, 11}
    sent.clear()
    s.thaw()
    assert {pid for pid, number in [x for x in sent if len(x) == 2] if number == signal.SIGCONT} == {10, 11}


def test_thaw_tree_continues_every_process_of_the_tree(monkeypatch):   # for others' grants and sweep
    sent = []
    monkeypatch.setattr(freeze.os, "kill", lambda pid, number: sent.append((pid, number)))
    monkeypatch.setattr(freeze.os, "killpg", lambda pid, number: None)
    assert freeze.thaw_tree(10, table={10: (1, 10, 1), 11: (10, 11, 1), 12: (1, 12, 1)})
    assert sorted(pid for pid, number in sent if number == signal.SIGCONT) == [10, 11]


def test_thaw_unit_writes_the_scopes_freeze_file(tmp_path):
    root = fake_cgroup(tmp_path)
    scope = root / "user.slice/flotilla-lane-b1-9-1.scope"
    (scope / "cgroup.freeze").write_text("1\n")
    assert freeze.thaw_unit("flotilla-lane-b1-9-1.scope", run=fake_systemctl, root=root)
    assert (scope / "cgroup.freeze").read_text().strip() == "0"


def test_thaw_unit_of_a_unit_that_is_gone_counts_as_thawed(tmp_path):   # final review I5b
    assert freeze.thaw_unit("gone.scope", run=lambda argv, **kw: done(0, ""), root=tmp_path)


def test_thaw_unit_when_systemd_cannot_be_asked_says_so(tmp_path):
    assert not freeze.thaw_unit("x.scope", run=lambda argv, **kw: done(1, ""), root=tmp_path)


def test_thaw_unit_ends_the_options_before_the_name(tmp_path):   # final review I7
    seen = []
    freeze.thaw_unit("flotilla-lane-b1-1-1.scope", run=lambda argv, **kw: seen.append(argv) or done(0, ""),
                     root=tmp_path)
    assert seen[0][-2:] == ["--", "flotilla-lane-b1-1-1.scope"]


def test_frozen_seconds_add_up_across_freezes():
    t = iter([0, 10, 20, 50])
    s = freeze.Slot(freeze.SIGNAL, clock=lambda: next(t))
    s.freeze(0)
    s.thaw()
    s.freeze(0)
    s.thaw()
    assert s.frozen_seconds() == 40


def test_the_guard_thaws_when_the_watch_thread_is_gone():
    s = freeze.Slot(freeze.SIGNAL)
    s.freeze(0)
    gone = threading.Thread(target=lambda: None)
    gone.start()
    gone.join()
    s.watcher = gone
    assert s.guard() and not s.frozen()
    assert not s.guard()


def test_the_guard_leaves_a_live_watch_alone():
    s = freeze.Slot(freeze.SIGNAL)
    s.freeze(0)
    stop = threading.Event()
    alive = threading.Thread(target=stop.wait)
    alive.start()
    s.watcher = alive
    try:
        assert not s.guard() and s.frozen()
    finally:
        stop.set()


def test_swap_is_read_from_meminfo(tmp_path):
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal: 16000000 kB\nSwapTotal:  12582908 kB\nSwapFree:   9115648 kB\n")
    assert freeze.swap_mb(meminfo) == (8902, 12287)


def test_swap_on_macos_is_read_from_sysctl(tmp_path):
    out = "total = 2048.00M  used = 512.00M  free = 1536.00M  (encrypted)\n"
    assert freeze.swap_mb(tmp_path / "none", run=lambda argv, **kw: done(0, out)) == (1536, 2048)


def test_swap_unread_is_unknown(tmp_path):
    assert freeze.swap_mb(tmp_path / "none", run=lambda argv, **kw: done(1, "")) is None



def test_scope_names_never_repeat_within_a_process(tmp_path):
    cg = tmp_path / "cgroup"
    cg.write_text("0::/user.slice/user-1000.slice/session-1.scope\n")
    first = freeze.Slot(freeze.SCOPE, proc_self=cg).contain(["true"], booking="b1")[5]
    second = freeze.Slot(freeze.SCOPE, proc_self=cg).contain(["true"], booking="b1")[5]
    assert first != second   # a scope systemd has not collected yet would refuse a second start under its name
