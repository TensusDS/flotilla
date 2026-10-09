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
                   "gpu_mb": "x", "peak_mb": 900, "cores": 2.0, "slug": "../../etc",
                   "ended": "2000-01-01T00:00:00+00:00"})
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


# which waiting run starts

MACHINE = j.Machine(id="m1", cpus=24, ram_mb=64000, gpu_total_mb=16000)   # room: 24 cores, 61952 MB, 14976 MB


def waiting(i, minutes=0):
    return j.Run(id=f"j{i}", state=j.WAITING, since=(NOW - dt.timedelta(minutes=minutes)).isoformat())


def on(i):
    return j.Run(id=f"j{i}", state=j.RUNNING)


def need(seconds, cores=2.0, ram=1000, gpu=0):
    return packing.Need(seconds, cores, ram, gpu, "test", False)


def pick(waits, running, needs, machine=MACHINE):
    return packing.choose(waits, running, machine, needs, NOW).run_id


def test_the_longest_run_that_fits_goes_first():
    needs = {"j1": need(60), "j2": need(480, cores=6, gpu=3000), "j3": need(60)}
    assert pick([waiting(1), waiting(2), waiting(3)], [], needs) == "j2"


def test_short_runs_fill_what_a_long_one_leaves():
    needs = {"j8": need(480, cores=20), "j9": need(480, cores=1), "j1": need(900, cores=6), "j2": need(60)}
    assert pick([waiting(1), waiting(2)], [on(8), on(9)], needs) == "j2"


def test_an_unmeasured_run_counts_as_the_longest():
    assert pick([waiting(1), waiting(2)], [], {"j1": need(900), "j2": packing.PRIOR}) == "j2"


def test_within_the_floor_cores_do_not_bind_but_memory_does():
    small = j.Machine(id="m1", cpus=4, ram_mb=8000, gpu_total_mb=None)        # room: 4 cores, 5952 MB
    assert pick([waiting(1)], [on(9)], {"j9": packing.PRIOR, "j1": packing.PRIOR}, small) == "j1"
    assert pick([waiting(1)], [on(9)], {"j9": need(60, ram=4000), "j1": need(60, ram=2000)}, small) is None


def test_past_the_floor_cores_bind():
    needs = {"j8": need(60, cores=10), "j9": need(60, cores=10), "j1": need(60, cores=6)}
    assert pick([waiting(1)], [on(8), on(9)], needs) is None


def test_measured_cores_are_clamped_to_the_machine():
    assert pick([waiting(1)], [on(8), on(9)], {"j8": need(60, cores=0.5), "j9": need(60, cores=0.5),
                                               "j1": need(60, cores=40)}) is None    # 24 + 1 > 24: past the floor
    assert pick([waiting(1)], [], {"j1": need(60, cores=40)}) == "j1"
    got = packing.choose([waiting(1), waiting(2)], [on(9)], MACHINE,
                         {"j9": need(60, cores=1), "j1": need(60, cores=40), "j2": need(10)}, NOW)
    assert got.run_id == "j1"                               # cores alone never make a run bigger than the machine


def test_a_senior_that_fits_goes_before_a_longer_later_run():
    assert pick([waiting(1, minutes=11), waiting(2)], [], {"j1": need(60), "j2": need(900)}) == "j1"


def test_a_senior_holds_the_room_it_needs():
    needs = {"j8": need(600, cores=10), "j9": need(600, cores=10), "j1": need(900, cores=8), "j2": need(30)}
    got = packing.choose([waiting(1, minutes=11), waiting(2)], [on(8), on(9)], MACHINE, needs, NOW)
    assert got.run_id is None and "senior j1" in got.why["j2"]


def test_a_later_run_may_take_room_the_senior_does_not_need():
    needs = {"j9": need(600, cores=4, gpu=14000), "j1": need(900, cores=4, gpu=4000), "j2": need(30, gpu=0)}
    assert pick([waiting(1, minutes=11), waiting(2)], [on(9)], needs) == "j2"


def test_a_run_bigger_than_the_machine_starts_alone_on_an_empty_machine():
    assert pick([waiting(1)], [], {"j1": need(60, ram=90000)}) == "j1"
    assert pick([waiting(1)], [on(9)], {"j9": need(60, cores=1), "j1": need(60, ram=90000)}) is None


def test_a_senior_too_big_for_the_machine_reserves_all_of_it():
    needs = {"j9": need(60, cores=1), "j1": need(60, gpu=20000), "j2": need(10, cores=1, ram=100, gpu=0)}
    got = packing.choose([waiting(1, minutes=11), waiting(2)], [on(9)], MACHINE, needs, NOW)
    assert got.run_id is None and "whole machine" in got.why["j2"]


def test_ties_go_by_run_number():
    assert pick([waiting(2), waiting(1)], [], {"j1": need(60), "j2": need(60)}) == "j1"


def test_a_machine_without_gpu_does_not_count_gpu_memory():
    cpu_only = j.Machine(id="m1", cpus=8, ram_mb=32000, gpu_total_mb=None)
    assert pick([waiting(1)], [on(9)], {"j9": need(60, gpu=20000), "j1": need(60, gpu=2048)}, cpu_only) == "j1"


def test_every_waiting_run_that_does_not_start_says_why():
    got = packing.choose([waiting(1), waiting(2)], [], MACHINE, {"j1": need(900), "j2": need(60)}, NOW)
    assert got.run_id == "j1" and set(got.why) == {"j2"} and got.why["j2"]


def test_the_shape_is_known_only_with_cpus_and_memory():
    assert packing.shape_known(MACHINE) and not packing.shape_known(j.Machine(id="m1", cpus=8, ram_mb=0))


def test_the_source_names_only_the_kinds_the_lane_issues():
    assert "match" in est(["ignore all:x"], done(1, ["ignore all:x"])).source
    assert "ignore" not in est(["ignore all:x"], done(1, ["ignore all:x"])).source


def test_a_run_measured_at_no_gpu_memory_needs_none():
    # final review of 0.11.0: a CPU-only command on a GPU machine measures 0, and 0 is its answer
    need = est(["exact:a"], done(1, ["exact:a"], gpu_mb=0, peak_mb=900))
    assert need.gpu_mb == 0 and not need.gpu_prior


def test_a_run_too_short_to_sample_teaches_no_gpu_figure():
    assert est(["exact:a"], done(1, ["exact:a"], gpu_mb=0, peak_mb=0)).gpu_prior
