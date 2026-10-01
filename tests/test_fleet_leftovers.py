import os
import signal

from flotilla.fleet import leftovers


SCOPE = "0::/user.slice/user-1000.slice/session-7.scope\n"


def fake_proc(root, pid, *, ppid, cwd, command, tty=0, uid=None, cgroup=SCOPE, start=1000):
    """One entry of a fake /proc: stat (starttime is field 22), status, cgroup, cmdline, and cwd as a link."""
    entry = root / str(pid)
    entry.mkdir(parents=True)
    (entry / "stat").write_text(f"{pid} ({command.split()[0]}) S {ppid} {pid} {pid} {tty} -1 0 0 0 0 0 0 0 0 0 20 0 1 0 "
                                f"{start} 0 0\n", encoding="utf-8")
    (entry / "status").write_text(f"Name:\tx\nUid:\t{os.getuid() if uid is None else uid}\t0\t0\t0\n",
                                  encoding="utf-8")
    (entry / "cgroup").write_text(cgroup, encoding="utf-8")
    (entry / "cmdline").write_text(command.replace(" ", "\0") + "\0", encoding="utf-8")
    os.symlink(cwd, entry / "cwd")


def world(tmp_path):
    proc = tmp_path / "proc"
    tree = tmp_path / "seat-1"
    (tree / "web").mkdir(parents=True)
    other = tmp_path / "seat-10"
    other.mkdir()
    return proc, tree, other


def test_an_orphan_working_in_the_tree_is_found_with_its_children(tmp_path):
    proc, tree, other = world(tmp_path)
    fake_proc(proc, 500, ppid=1, cwd=tree / "web", command="node vite --port 5173")
    fake_proc(proc, 501, ppid=500, cwd=tmp_path, command="esbuild --service")
    found = leftovers.in_tree(tree, keep=set(), proc_root=proc, me=99999)
    assert [(item.pid, item.command) for item in found] == [(500, "node vite --port 5173"), (501, "esbuild --service")]


def test_what_is_outside_the_tree_or_has_a_live_parent_stays(tmp_path):
    proc, tree, other = world(tmp_path)
    fake_proc(proc, 600, ppid=1, cwd=other, command="node vite")                 # a neighbour seat: prefix trap
    fake_proc(proc, 601, ppid=1, cwd=tmp_path, command="node vite")              # outside every tree
    fake_proc(proc, 700, ppid=1, cwd="/", command="sshd")
    fake_proc(proc, 701, ppid=700, cwd=tree, command="bash", tty=34817)          # the person's shell in the tree
    fake_proc(proc, 702, ppid=701, cwd=tree, command="npm run dev")              # started from that shell
    assert leftovers.in_tree(tree, keep=set(), proc_root=proc, me=99999) == []


def test_an_orphan_serving_a_terminal_or_a_live_session_stays(tmp_path):
    proc, tree, other = world(tmp_path)
    fake_proc(proc, 800, ppid=1, cwd=tree, command="tmux new-session")           # the person's tmux server
    fake_proc(proc, 801, ppid=800, cwd=tree, command="bash", tty=34818)
    fake_proc(proc, 900, ppid=1, cwd=tree, command="claude daemon")
    fake_proc(proc, 901, ppid=900, cwd=tree, command="claude --bg")              # a live census session under it
    fake_proc(proc, 950, ppid=1, cwd=tree, command="flotilla retire")            # this very command
    assert leftovers.in_tree(tree, keep={901}, proc_root=proc, me=950) == []


def test_a_machine_without_proc_is_not_asked(tmp_path):
    assert leftovers.in_tree(tmp_path, keep=set(), proc_root=tmp_path / "no-proc", me=1) is None


def test_a_systemd_service_or_another_users_process_is_never_a_leftover(tmp_path):
    proc, tree, other = world(tmp_path)
    fake_proc(proc, 500, ppid=1, cwd=tree, command="python serving", cgroup="0::/system.slice/curve-serving.service\n")
    fake_proc(proc, 501, ppid=1, cwd=tree, command="runsvc.sh", uid=os.getuid() + 1)
    fake_proc(proc, 502, ppid=1, cwd=tree, command="node vite")
    assert [item.pid for item in leftovers.in_tree(tree, keep=set(), proc_root=proc, me=99999)] == [502]


def test_stop_signals_only_the_same_process_it_found(tmp_path):
    proc = tmp_path / "proc"
    fake_proc(proc, 1, ppid=0, cwd=tmp_path, command="a", start=100)
    fake_proc(proc, 3, ppid=0, cwd=tmp_path, command="c", start=999)     # pid 3 was reused since the scan
    sent = []

    def kill(pid, sig):
        sent.append((pid, sig))

    found = [leftovers.Leftover(1, "a", "100"), leftovers.Leftover(2, "b", "100"), leftovers.Leftover(3, "c", "300")]
    assert leftovers.stop(found, kill=kill, proc_root=proc) == 1
    assert sent == [(1, signal.SIGTERM)]
