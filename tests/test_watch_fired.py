from flotilla.watch import fired


def test_a_hook_run_is_recorded_per_session_and_event(tmp_path):
    fired.record(tmp_path, "sid-1", "session-start", root=tmp_path, at="2026-09-29T10:00:00+00:00")
    fired.record(tmp_path, "sid-1", "guard", root=tmp_path, at="2026-09-29T10:05:00+00:00")
    assert fired.read(tmp_path, "sid-1") == {"session-start": "2026-09-29T10:00:00+00:00",
                                             "guard": "2026-09-29T10:05:00+00:00"}
    assert fired.read(tmp_path, "sid-2") == {}


def test_a_hook_that_cannot_record_still_answers(tmp_path):
    blocked = tmp_path / "file"
    blocked.write_text("x", encoding="utf-8")   # a file where the state directory should be
    fired.record(blocked, "sid-1", "stop", root=tmp_path, at="2026-09-29T10:00:00+00:00")   # no exception
    assert fired.read(blocked, "sid-1") == {}


def test_a_session_id_cannot_climb_out_of_the_directory(tmp_path):
    fired.record(tmp_path / "state", "../../escape", "stop", root=tmp_path, at="2026-09-29T10:00:00+00:00")
    assert not (tmp_path / "escape.json").exists() and not (tmp_path.parent / "escape.json").exists()


def test_two_events_recorded_at_once_both_survive(tmp_path):
    import multiprocessing
    events = ["guard", "permission", "prompt", "stop"]
    procs = [multiprocessing.Process(target=_hammer, args=(str(tmp_path), event)) for event in events]
    for proc in procs:
        proc.start()
    for proc in procs:
        proc.join()
    assert set(fired.read(tmp_path, "sid-1")) == set(events)


def _hammer(state, event):
    from pathlib import Path
    for n in range(200):
        fired.record(Path(state), "sid-1", event, root=Path(state), at=f"2026-09-29T10:00:{n % 60:02d}+00:00")
