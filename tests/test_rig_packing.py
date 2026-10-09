import datetime as dt

from flotilla.core.storage import LocalLogStore
from flotilla.rig import journal as j, packing

NOW = dt.datetime(2026, 10, 9, 12, 0, tzinfo=dt.timezone.utc)


def done(i, ladder, *, verdict="green", exit=0, seconds=60.0, cores=2.0, peak_mb=900, gpu_mb=500,
         gpu_shared_mb=None, days=0):
    return j.Run(id=f"j{i}", state=j.DONE, verdict=verdict, exit=exit, ladder=tuple(ladder), seconds=seconds,
                 cores=cores, peak_mb=peak_mb, gpu_mb=gpu_mb, gpu_shared_mb=gpu_shared_mb,
                 ended=(NOW - dt.timedelta(days=days)).isoformat())


def est(ladder, *runs):
    return packing.estimate(ladder, packing.history({r.id: r for r in runs}, now=NOW))


def test_a_never_measured_command_takes_the_prior():
    assert packing.estimate(["exact:a"], {}) == packing.PRIOR


def test_the_most_exact_step_with_a_sample_answers():
    need = est(["exact:a", "prog:x"], done(1, ["exact:a", "prog:x"], seconds=100, cores=6.0),
               done(2, ["exact:b", "prog:x"], seconds=10, cores=1.0))
    assert (need.seconds, need.cores) == (100.0, 6.0) and "exact" in need.source


def test_the_project_step_never_answers():
    assert est(["exact:new", "project:P"], done(1, ["exact:true", "project:P"], cores=0.1)) == packing.PRIOR


def test_duration_is_the_median_of_green_runs_and_resources_the_maxima():
    need = est(["exact:a"], done(1, ["exact:a"], seconds=10, peak_mb=100), done(2, ["exact:a"], seconds=30),
               done(3, ["exact:a"], seconds=20, peak_mb=700), done(4, ["exact:a"], verdict="red", exit=1, seconds=1))
    assert need.seconds == 20.0 and need.ram_mb == 900


def test_a_step_with_only_red_runs_takes_its_duration_from_a_coarser_step():
    need = est(["exact:a", "prog:x"], done(1, ["exact:a", "prog:x"], verdict="red", exit=1, seconds=5),
               done(2, ["exact:b", "prog:x"], seconds=40))
    assert need.seconds == 40.0


def test_a_zero_is_no_sample():
    need = est(["exact:a"], done(1, ["exact:a"], cores=0.0, peak_mb=0, gpu_mb=0))
    assert (need.cores, need.ram_mb, need.gpu_mb) == (packing.PRIOR.cores, packing.PRIOR.ram_mb, packing.PRIOR.gpu_mb)


def test_a_killed_run_is_no_memory_sample_but_teaches_cores():
    need = est(["exact:a"], done(1, ["exact:a"], verdict="red", exit=137, cores=3.0, peak_mb=5000))
    assert need.ram_mb == packing.PRIOR.ram_mb and need.cores == 3.0


def test_a_shared_gpu_figure_raises_but_never_lowers():
    assert est(["exact:a"], done(1, ["exact:a"], gpu_mb=1000),
               done(2, ["exact:a"], gpu_mb=None, gpu_shared_mb=9000)).gpu_mb == 9000
    assert est(["exact:a"], done(1, ["exact:a"], gpu_mb=1000),
               done(2, ["exact:a"], gpu_mb=None, gpu_shared_mb=100)).gpu_mb == 1000
    assert est(["exact:a"], done(1, ["exact:a"], gpu_mb=None, gpu_shared_mb=100)).gpu_mb == packing.PRIOR.gpu_mb


def test_a_gpu_never_measured_alone_is_the_prior_and_says_so():
    need = est(["exact:a"], done(1, ["exact:a"], gpu_mb=None))
    assert need.gpu_mb == packing.PRIOR.gpu_mb and need.gpu_prior
    assert not est(["exact:a"], done(1, ["exact:a"], gpu_mb=700)).gpu_prior


def test_old_and_unmeasured_runs_teach_nothing():
    assert est(["exact:a"], done(1, ["exact:a"], days=31), done(2, ["exact:a"], verdict="stopped"),
               done(3, ["exact:a"], verdict="refused")) == packing.PRIOR


def test_hand_written_measurements_fold_as_missing(tmp_path):
    store = LocalLogStore(tmp_path / "rig")
    r = j.Rig(store, clock=lambda: NOW)
    at = NOW.isoformat()
    with store.transaction(j.KEY) as tx:
        tx.append({"kind": "run", "id": "j1", "state": "waiting", "at": at, "ladder": ["exact:a"]})
        tx.append({"kind": "run", "id": "j1", "state": "done", "at": at, "verdict": "green", "seconds": -5,
                   "gpu_mb": "x", "peak_mb": 900, "cores": 2.0, "slug": "../../etc", "ended": "2000-01-01T00:00:00+00:00"})
    run = r.runs()["j1"]
    assert run.seconds is None and run.gpu_mb is None and run.slug == "" and run.ended == at
    assert packing.history(r.runs(), now=NOW) == {}   # no seconds: no sample


def test_a_runs_end_is_its_first_done_event(tmp_path):
    clock = {"at": NOW}
    r = j.Rig(LocalLogStore(tmp_path / "rig"), clock=lambda: clock["at"])
    run = r.queue_run("s1", who="a", project="p", revision="r", program="x", ladder=["exact:a"], pid=1, mark="m")
    first = r.finish_run(run.id, "gone").ended
    clock["at"] = NOW + dt.timedelta(hours=1)
    assert first == NOW.isoformat() and r.mark_swept(run.id).ended == first
