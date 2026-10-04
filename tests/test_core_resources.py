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
