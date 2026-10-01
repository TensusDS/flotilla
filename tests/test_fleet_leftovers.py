import os
import signal

from flotilla.fleet import leftovers


def fake_proc(root, pid, *, ppid, cwd, command, tty=0):
    """One entry of a fake /proc: stat with the fields leftovers reads, cmdline, and cwd as a link."""
    entry = root / str(pid)
    entry.mkdir(parents=True)
    (entry / "stat").write_text(f"{pid} ({command.split()[0]}) S {ppid} {pid} {pid} {tty} -1 0\n", encoding="utf-8")
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


def test_stop_terminates_each_and_counts_what_it_reached(tmp_path):
    sent = []

    def kill(pid, sig):
        sent.append((pid, sig))
        if pid == 2:
            raise ProcessLookupError(pid)

    found = [leftovers.Leftover(1, "a"), leftovers.Leftover(2, "b"), leftovers.Leftover(3, "c")]
    assert leftovers.stop(found, kill=kill) == 2
    assert sent == [(1, signal.SIGTERM), (2, signal.SIGTERM), (3, signal.SIGTERM)]
