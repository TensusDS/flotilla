import subprocess

from flotilla.core import resources

VM_STAT = """Mach Virtual Memory Statistics: (page size of 16384 bytes)
Pages free:                               12000.
Pages active:                            300000.
Pages inactive:                          200000.
Pages speculative:                         4000.
Pages throttled:                              0.
Pages wired down:                        150000.
"""


def test_vm_stat_uses_its_own_page_size():
    """Apple silicon pages are 16 KB, Intel 4 KB: the page size is read from vm_stat's own first line, never assumed."""
    assert resources.parse_vm_stat(VM_STAT) == (12000 + 200000 + 4000) * 16384 // 2**20


def test_vm_stat_with_a_4k_page():
    text = VM_STAT.replace("16384", "4096")
    assert resources.parse_vm_stat(text) == (12000 + 200000 + 4000) * 4096 // 2**20


def test_garbage_vm_stat_is_unknown():
    assert resources.parse_vm_stat("nothing here") is None
    assert resources.parse_vm_stat("Mach Virtual Memory Statistics: (page size of 16384 bytes)\n") is None


def test_available_mb_on_linux_reads_memavailable(tmp_path):
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal:       16000000 kB\nMemFree:         1000000 kB\nMemAvailable:    6144000 kB\n")
    assert resources.available_mb(meminfo=meminfo, os_name="linux") == 6000


def test_available_mb_on_linux_without_meminfo_is_unknown(tmp_path):
    assert resources.available_mb(meminfo=tmp_path / "absent", os_name="linux") is None


def test_available_mb_on_macos_asks_vm_stat(tmp_path):
    def run(cmd, **kw):
        assert cmd == ["vm_stat"]
        return subprocess.CompletedProcess(cmd, 0, VM_STAT, "")
    assert resources.available_mb(run=run, os_name="darwin") == (12000 + 200000 + 4000) * 16384 // 2**20


def test_available_mb_on_macos_with_vm_stat_failing_is_unknown():
    def run(cmd, **kw):
        raise FileNotFoundError(cmd[0])
    assert resources.available_mb(run=run, os_name="darwin") is None


def test_free_disk_is_positive(tmp_path):
    assert resources.free_disk_mb(tmp_path) > 0


def test_free_disk_of_a_missing_path_is_unknown(tmp_path):
    assert resources.free_disk_mb(tmp_path / "absent") is None


def test_tree_mb_reads_du():
    def run(cmd, **kw):
        assert cmd[:2] == ["du", "-sk"]
        return subprocess.CompletedProcess(cmd, 0, "1024\t/x\n", "")
    assert resources.tree_mb("/x", run=run) == 1


def test_tree_mb_rounds_up():
    def run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 0, "1025\t/x\n", "")
    assert resources.tree_mb("/x", run=run) == 2


def test_tree_mb_with_du_failing_is_unknown():
    def failing(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 1, "", "du: cannot access")

    def timing_out(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 20)
    assert resources.tree_mb("/x", run=failing) is None
    assert resources.tree_mb("/x", run=timing_out) is None


def test_vm_stat_without_its_page_size_line_is_unknown():
    """A page count with no page size is no number of bytes: unknown, never a guessed 4 KB."""
    assert resources.parse_vm_stat(VM_STAT.split("\n", 1)[1]) is None


def test_tree_mb_from_a_du_that_failed_partway_is_unknown():
    """du exits 1 when a subdirectory is unreadable but still prints a total - an undercount, so not a size."""
    def partial(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 1, "1024\t/x\n", "du: cannot read directory '/x/secret'")
    assert resources.tree_mb("/x", run=partial) is None


MB = 2**20


def cgroup_v2(tmp_path, chain, *, leaf="/user.slice/app.scope"):
    """chain: {relative dir: (memory.max or None, memory.current, inactive_file)} under a fake /sys/fs/cgroup."""
    root = tmp_path / "cgroup"
    for rel, (limit, current, inactive) in chain.items():
        d = root / rel.lstrip("/") if rel != "/" else root
        d.mkdir(parents=True, exist_ok=True)
        if rel != "/":
            (d / "memory.max").write_text("max\n" if limit is None else f"{limit}\n")
            (d / "memory.current").write_text(f"{current}\n")
            (d / "memory.stat").write_text(f"anon 1\nfile 9\ninactive_file {inactive}\nactive_file 3\n")
    selfcg = tmp_path / "self-cgroup"
    selfcg.write_text(f"0::{leaf}\n")
    return selfcg, root


def test_a_container_limit_is_the_room_it_leaves(tmp_path):
    """Review of 0.7.14: in a container, or a systemd slice with MemoryMax, MemAvailable is the host's; the room is
    the limit less the group's working set (its usage less the file cache it can drop)."""
    selfcg, root = cgroup_v2(tmp_path, {"/": (None, 0, 0),
                                        "/user.slice": (None, 0, 0),
                                        "/user.slice/app.scope": (4096 * MB, 3000 * MB, 1000 * MB)})
    room, note = resources.cgroup_room_mb(proc_self=selfcg, root=root)
    assert room == 4096 - (3000 - 1000) and "4096" in note


def test_the_tightest_ancestor_binds(tmp_path):
    selfcg, root = cgroup_v2(tmp_path, {"/": (None, 0, 0),
                                        "/user.slice": (2048 * MB, 1500 * MB, 0),
                                        "/user.slice/app.scope": (8192 * MB, 100 * MB, 0)})   # its own, looser
    room, note = resources.cgroup_room_mb(proc_self=selfcg, root=root)
    assert room == 2048 - 1500 and "/user.slice" in note


def test_no_limit_anywhere_is_no_room_figure(tmp_path):
    selfcg, root = cgroup_v2(tmp_path, {"/": (None, 0, 0), "/user.slice": (None, 0, 0),
                                        "/user.slice/app.scope": (None, 5 * MB, 0)})
    assert resources.cgroup_room_mb(proc_self=selfcg, root=root) == (None, "")
    assert resources.cgroup_room_mb(proc_self=tmp_path / "absent", root=root) == (None, "")


def test_a_cgroup_v1_memory_limit(tmp_path):
    root = tmp_path / "cgroup"
    (root / "memory").mkdir(parents=True)
    (root / "memory" / "memory.limit_in_bytes").write_text(f"{1024 * MB}\n")
    (root / "memory" / "memory.usage_in_bytes").write_text(f"{600 * MB}\n")
    (root / "memory" / "memory.stat").write_text(f"cache 1\ntotal_inactive_file {100 * MB}\n")
    selfcg = tmp_path / "self-cgroup"
    selfcg.write_text("12:memory:/\n3:cpu:/\n")
    room, _ = resources.cgroup_room_mb(proc_self=selfcg, root=root)
    assert room == 1024 - 500
    (root / "memory" / "memory.limit_in_bytes").write_text("9223372036854771712\n")   # v1's "no limit"
    assert resources.cgroup_room_mb(proc_self=selfcg, root=root) == (None, "")


def test_available_is_the_smaller_of_host_and_container(tmp_path):
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemAvailable:   8192000 kB\n")
    selfcg, root = cgroup_v2(tmp_path, {"/": (None, 0, 0), "/user.slice": (None, 0, 0),
                                        "/user.slice/app.scope": (2048 * MB, 1048 * MB, 0)})
    mb, note = resources.memory_mb(meminfo=meminfo, os_name="linux", proc_self=selfcg, cgroup_root=root)
    assert mb == 1000 and "limit" in note
    assert resources.available_mb(meminfo=meminfo, os_name="linux", proc_self=selfcg, cgroup_root=root) == 1000
    mb, note = resources.memory_mb(meminfo=meminfo, os_name="linux", proc_self=tmp_path / "absent",
                                   cgroup_root=root)
    assert mb == 8000 and note == ""


def test_spawn_and_the_lane_see_the_container_limit_too(monkeypatch):
    """Three readers of free memory - the sizing, spawn's seat check and the lane's floor - must agree: a spawn that
    asks the host while the sizing asks the container would raise the seats the sizing refused."""
    import sys

    import pytest

    from flotilla.fleet import spawn
    from flotilla.lane import machine
    if not sys.platform.startswith("linux"):
        pytest.skip("MemAvailable is Linux's")
    import importlib.util

    def pristine(module):   # conftest stubs both readers in every test; load each module's own code afresh
        spec = importlib.util.spec_from_file_location(f"{module.__name__}_pristine", module.__file__)
        copy = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, spec.name, copy)   # its dataclasses look themselves up there
        spec.loader.exec_module(copy)
        return copy
    monkeypatch.setattr(resources, "cgroup_room_mb", lambda **kw: (500, "the memory limit of 600 MB on cgroup /x"))
    assert pristine(spawn).available_mb() == 500
    assert pristine(machine)._meminfo() == 500 * 1024
    monkeypatch.setattr(resources, "cgroup_room_mb", lambda **kw: (None, ""))
    assert pristine(spawn).available_mb() == pristine(spawn).read_available_mb()


def _container(tmp_path, leaf):
    """A container on cgroup v2: its own namespace root carries the limit, and /proc/self/cgroup says `0::/`."""
    root = tmp_path / "cgroup"
    root.mkdir()
    (root / "memory.max").write_text(f"{512 * MB}\n")
    (root / "memory.current").write_text(f"{200 * MB}\n")
    (root / "memory.stat").write_text(f"inactive_file {50 * MB}\n")
    if leaf != "/":
        (root / leaf.strip("/")).mkdir()
    selfcg = tmp_path / "self-cgroup"
    selfcg.write_text(f"0::{leaf}\n")
    return selfcg, root


def test_a_docker_containers_limit_sits_on_its_root(tmp_path):
    """Review of 0.7.14, measured in `docker run -m 512m`: `0::/`, the limit on /sys/fs/cgroup/memory.max. The walk
    skipped the root and read nothing, so in a container - the case the reader exists for - nothing changed."""
    for name, leaf in (("plain", "/"), ("systemd", "/init.scope")):
        here = tmp_path / name
        here.mkdir()
        selfcg, root = _container(here, leaf)
        room, note = resources.cgroup_room_mb(proc_self=selfcg, root=root)
        assert room == 512 - (200 - 50) and "512" in note, (leaf, room)
        assert resources.cgroup_limit_mb(proc_self=selfcg, root=root) == 512


def test_the_limit_is_the_tightest_one(tmp_path):
    selfcg, root = cgroup_v2(tmp_path, {"/": (None, 0, 0), "/user.slice": (2048 * MB, 1500 * MB, 0),
                                        "/user.slice/app.scope": (8192 * MB, 100 * MB, 0)})
    assert resources.cgroup_limit_mb(proc_self=selfcg, root=root) == 2048
    assert resources.cgroup_limit_mb(proc_self=tmp_path / "absent", root=root) is None


def test_a_cgroup_name_that_is_not_utf8_reads_as_no_limit(tmp_path):
    selfcg = tmp_path / "self-cgroup"
    selfcg.write_bytes(b"0::/\xff\xfe\n")
    assert resources.cgroup_room_mb(proc_self=selfcg, root=tmp_path) == (None, "")
