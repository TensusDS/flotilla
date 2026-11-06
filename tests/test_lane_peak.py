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


def _waits(monkeypatch, *, sample, cost):
    """The waits the sampler asks for, read without waiting: each read costs `cost` seconds of this thread's CPU,
    by a clock that advances only when told to - real timing on a shared CI runner is not what is tested."""
    clock = {"now": 0.0, "calls": 0}

    def thread_time():
        clock["calls"] += 1
        if clock["calls"] % 2 == 0:   # every second call ends a read
            clock["now"] += cost
        return clock["now"]
    monkeypatch.setattr(peak.time, "thread_time", thread_time)
    sampler = peak.GroupPeak(7, sample=sample, read=lambda pgid: 100 * 1024, rusage=lambda: None)
    waits, stop = [], sampler._stop.set

    def record(timeout=None):
        waits.append(round(timeout, 6))
        if len(waits) >= 3:
            stop()
        return False
    sampler._stop.wait = record
    with sampler:
        sampler._thread.join(timeout=5)
    return waits


def test_a_costly_read_slows_the_sampling_down(monkeypatch):
    """Review of 0.7.14: on a machine with thousands of processes one /proc scan costs tens of milliseconds; the
    sampler keeps its own cost near 5% of a core by waiting twenty times the CPU a read took."""
    assert _waits(monkeypatch, sample=0.1, cost=0.02) == [0.4, 0.4, 0.4]


def test_the_stretched_wait_has_a_ceiling(monkeypatch):
    """Never past five samples, so a gap never swallows a whole short tier."""
    assert _waits(monkeypatch, sample=0.02, cost=0.05) == [0.1, 0.1, 0.1]


def test_waiting_for_a_cpu_does_not_stretch_the_gap(monkeypatch):
    """Wall time a read spends off the CPU on a loaded machine is not its cost (review of 0.7.14): only CPU counts.
    (A wall-clock version of this test failed on a slow macOS runner - CI of 0.7.14.)"""
    assert _waits(monkeypatch, sample=0.02, cost=0.0) == [0.02, 0.02, 0.02]

def test_peak_cores_is_the_busiest_stretch_not_the_average():
    readings = iter([(1000, 0.0), (1000, 8.0), (1000, 9.0), (1000, 10.0)])   # (kB, group CPU s) a second apart
    times = iter([0.0, 1.0, 2.0, 3.0])
    meter = peak.GroupPeak(1, sample=1.0, sampler=lambda group: next(readings), clock=lambda: next(times),
                           rusage=lambda: None)
    for _ in range(4):
        meter._take()
    assert meter.peak_cores == 8.0 and meter.peak_mb == 1


def test_peak_cores_waits_for_a_whole_sample_between_readings():
    readings = iter([(1000, 0.0), (1000, 5.0), (1000, 6.0)])
    times = iter([0.0, 0.25, 1.0])   # the reading at 0.25 s is too close to rate; 6 CPU s over 1 s is
    meter = peak.GroupPeak(1, sample=1.0, sampler=lambda group: next(readings), clock=lambda: next(times),
                           rusage=lambda: None)
    for _ in range(3):
        meter._take()
    assert meter.peak_cores == 6.0


def test_a_memory_only_reader_has_no_peak_cores():
    meter = peak.GroupPeak(1, read=lambda group: 2048, rusage=lambda: None)
    meter._take()
    assert meter.peak_mb == 2 and meter.peak_cores is None


def test_group_usage_sums_cpu_of_the_group_and_its_tree(tmp_path):
    root = fake_proc(tmp_path, {7: (7, 100, 1), 8: (8, 50, 7), 9: (9, 999, 1)})
    for pid, ticks in ((7, (100, 50, 0, 0)), (8, (200, 0, 25, 25)), (9, (9999, 0, 0, 0))):
        fields = ["S", "1" if pid != 8 else "7", str(pid)] + ["0"] * 8 + [str(t) for t in ticks] + ["0"] * 40
        (root / str(pid) / "stat").write_text(f"{pid} (cmd x) " + " ".join(fields), encoding="utf-8")
    kb, cpu = peak.group_usage(7, proc_root=root, page_kb=4, tick=100)
    assert kb == 600 and cpu == 4.0   # (150 + 250) ticks at 100 a second; pid 9 is neither group nor tree


def test_tree_rss_counts_the_wrapper_and_its_command(tmp_path):
    root = fake_proc(tmp_path, {10: (10, 100, 1), 11: (11, 300, 10), 12: (10, 999, 1)})   # 12: same group, no child
    assert peak.tree_rss_mb(10, proc_root=root, page_kb=4) == 2   # 400 pages * 4 kB, rounded up


def test_tree_rss_of_a_pid_that_is_gone_is_unknown(tmp_path):
    assert peak.tree_rss_mb(10, proc_root=fake_proc(tmp_path, {11: (11, 5, 1)}), page_kb=4) is None
