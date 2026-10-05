from pathlib import Path

from flotilla.lane import measure


def write_stat(path: Path, user, nice, system, idle, iowait):
    path.write_text(f"cpu  {user} {nice} {system} {idle} {iowait} 0 0 0 0 0\ncpu0 1 1 1 1 1 0 0 0 0 0\n")
    return path


def test_busy_share_is_busy_jiffies_over_all(tmp_path):
    before = measure.cpu_times(write_stat(tmp_path / "a", 100, 0, 100, 700, 100))
    after = measure.cpu_times(write_stat(tmp_path / "b", 400, 0, 200, 700, 200))   # idle +0, iowait +100: not busy
    assert measure.busy_share(before, after) == 0.8          # 400 busy of 500 jiffies


def test_busy_share_is_unknown_without_proc_stat(tmp_path):
    assert measure.cpu_times(tmp_path / "absent") is None
    assert measure.busy_share(None, None) is None


class _Peak:
    def __init__(self, mb):
        self.peak_mb = mb

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None


def test_a_measurer_reports_wall_time_cpu_cores_and_peak():
    clock = iter([100.0, 110.0])
    cpu = iter([5.0, 45.0])
    stat = iter([measure.CpuTimes(0, 1000), measure.CpuTimes(500, 2000)])
    m = measure.Measurer(7, clock=lambda: next(clock), cpu=lambda: next(cpu), stat=lambda: next(stat),
                         peak=_Peak(900))
    with m:
        pass
    assert m.usage == measure.Usage(seconds=10.0, peak_mb=900, cores=4.0, busy=0.5)


def test_a_measurer_with_nothing_readable_still_has_its_time():
    clock = iter([1.0, 4.0])
    m = measure.Measurer(7, clock=lambda: next(clock), cpu=lambda: None, stat=lambda: None, peak=_Peak(None))
    with m:
        pass
    assert m.usage == measure.Usage(seconds=3.0, peak_mb=None, cores=None, busy=None)
