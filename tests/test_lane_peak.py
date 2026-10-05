import os
import subprocess

from flotilla.lane import peak


def fake_proc(tmp_path, procs):
    """procs: {pid: (pgrp, rss_pages)} or {pid: (pgrp, rss_pages, ppid)}"""
    for pid, spec in procs.items():
        pgrp, pages, ppid = (*spec, 1) if len(spec) == 2 else spec
        folder = tmp_path / str(pid)
        folder.mkdir()
        fields = ["S", str(ppid), str(pgrp)] + ["0"] * 50
        (folder / "stat").write_text(f"{pid} (cmd x) " + " ".join(fields), encoding="utf-8")
        (folder / "statm").write_text(f"1000 {pages} 0 0 0 0 0", encoding="utf-8")
    return tmp_path


def test_a_groups_memory_is_the_sum_of_its_processes(tmp_path):
    root = fake_proc(tmp_path, {11: (7, 100), 12: (7, 50), 13: (8, 999)})
    assert peak.group_rss_kb(7, proc_root=root, page_kb=4) == 600


def test_a_group_with_no_processes_is_unknown(tmp_path):
    assert peak.group_rss_kb(7, proc_root=fake_proc(tmp_path, {}), page_kb=4) is None


def test_the_ps_path_sums_the_group_and_its_descendants(tmp_path):
    """No /proc (macOS): one portable `ps -A` listing, filtered here - `-g` means a session on procps and is not
    portable (review of 0.7.14)."""
    listing = " 7 1 7 1000\n 8 7 7 2000\n 9 8 9 500\n 20 1 20 99999\n"
    asked = []

    def run(cmd, **kwargs):
        asked.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, listing, "")
    assert peak.group_rss_kb(7, proc_root=tmp_path / "none", run=run) == 3500
    assert asked[0][:2] == ["ps", "-A"]


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


def test_a_descendant_that_left_the_group_is_still_counted(tmp_path):
    """Playwright starts Chromium detached, in its own process group (review of 0.7.14, C1): the browser is the
    heaviest part of an e2e run and must be in its peak. Descendants are found by parent, whatever their group."""
    root = fake_proc(tmp_path, {7: (7, 100, 1), 8: (7, 50, 7), 9: (9, 1000, 8), 10: (9, 500, 9), 11: (11, 9999, 1)})
    assert peak.group_rss_kb(7, proc_root=root, page_kb=4) == (100 + 50 + 1000 + 500) * 4


def test_the_childrens_max_rss_backs_the_samples():
    """A tier shorter than a sample, or a spike between two, is caught by the largest child the kernel reaped."""
    marks = iter([200 * 1024, 900 * 1024])
    sampler = peak.GroupPeak(7, sample=0.01, read=lambda pgid: 100 * 1024, rusage=lambda: next(marks))
    with sampler:
        pass
    assert sampler.peak_mb == 900


def test_an_earlier_larger_child_is_not_this_tiers_peak():
    marks = iter([900 * 1024, 900 * 1024])
    sampler = peak.GroupPeak(7, sample=0.01, read=lambda pgid: 100 * 1024, rusage=lambda: next(marks))
    with sampler:
        import time
        time.sleep(0.05)
    assert sampler.peak_mb == 100


def test_a_sampler_that_cannot_start_measures_nothing_and_raises_nothing(monkeypatch):
    """Review of 0.7.14, I: `can't start new thread` under memory pressure must not kill a tier that passed."""
    import threading

    def refuse(self):
        raise RuntimeError("can't start new thread")
    monkeypatch.setattr(threading.Thread, "start", refuse)
    sampler = peak.GroupPeak(7, sample=0.01, read=lambda pgid: 100 * 1024, rusage=lambda: None)
    with sampler:
        pass
    assert sampler.peak_mb is None


def test_a_costly_read_slows_the_sampling_down():
    """Review of 0.7.14: on a machine with thousands of processes one /proc scan costs tens of milliseconds; the
    sampler keeps its own cost near 5% of a core by waiting twenty times as long as a read took."""
    import time
    reads = []

    def slow(pgid):
        reads.append(time.monotonic())
        time.sleep(0.02)
        return 100 * 1024
    sampler = peak.GroupPeak(7, sample=0.01, read=slow, rusage=lambda: None)
    with sampler:
        time.sleep(0.5)
    assert 1 <= len(reads) <= 2, len(reads)   # 0.02 s a read -> 0.4 s between reads, not 0.01 s
