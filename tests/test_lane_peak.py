import os
import subprocess

from flotilla.lane import peak


def fake_proc(tmp_path, procs):
    """procs: {pid: (pgrp, rss_pages)}"""
    for pid, (pgrp, pages) in procs.items():
        folder = tmp_path / str(pid)
        folder.mkdir()
        fields = ["S", "1", str(pgrp)] + ["0"] * 50
        (folder / "stat").write_text(f"{pid} (cmd x) " + " ".join(fields), encoding="utf-8")
        (folder / "statm").write_text(f"1000 {pages} 0 0 0 0 0", encoding="utf-8")
    return tmp_path


def test_a_groups_memory_is_the_sum_of_its_processes(tmp_path):
    root = fake_proc(tmp_path, {11: (7, 100), 12: (7, 50), 13: (8, 999)})
    assert peak.group_rss_kb(7, proc_root=root, page_kb=4) == 600


def test_a_group_with_no_processes_is_unknown(tmp_path):
    assert peak.group_rss_kb(7, proc_root=fake_proc(tmp_path, {}), page_kb=4) is None


def test_the_ps_path_sums_the_group(tmp_path):
    def run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, " 1000\n 2000\n", "")
    assert peak.group_rss_kb(7, proc_root=tmp_path / "none", run=run) == 3000


def test_the_sampler_keeps_the_largest_reading():
    readings = iter([100 * 1024, 300 * 1024, 200 * 1024])
    sampler = peak.GroupPeak(7, sample=0.01, read=lambda pgid: next(readings, None))
    with sampler:
        import time
        time.sleep(0.2)
    assert sampler.peak_mb == 300


def test_a_sampler_that_read_nothing_is_unknown():
    sampler = peak.GroupPeak(7, sample=0.01, read=lambda pgid: None)
    with sampler:
        pass
    assert sampler.peak_mb is None
