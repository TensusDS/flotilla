import datetime as dt

import pytest

from flotilla.core.storage import LocalLogStore
from flotilla.rig import journal as j
from flotilla.rig import provider as pv
from flotilla.rig.providers import vast as vast_adapter
from flotilla.rig.reaper import emergency, needs_reaper, reap
from rigkit import FakeVast, fake_key

T0 = dt.datetime(2026, 10, 6, 20, 0, tzinfo=dt.timezone.utc)
KEY = "0123456789ab"


class Clock:
    def __init__(self):
        self.at = T0

    def __call__(self):
        return self.at

    def forward(self, **delta):
        self.at += dt.timedelta(**delta)


def label_of(machine_id):
    return f"flotilla:{KEY}:{machine_id}"


@pytest.fixture
def world(tmp_path, monkeypatch):
    fake = FakeVast()
    monkeypatch.setattr(vast_adapter, "SEND", fake)
    clock = Clock()
    rig = j.Rig(LocalLogStore(tmp_path / "rig"), clock=clock)
    vast = pv.Rented("vast", key=fake_key(tmp_path))
    return rig, clock, fake, (lambda name: vast)


def session(rig, hours=3, budget=2.0):
    return rig.open_session("max", "night frames", hours=hours, budget=budget)


def running(rig, fake, instance="101", hourly=0.30):
    machine = rig.add_machine("s1", "vast", label_of)
    fake.instances[instance] = {"label": machine.label, "dph_total": hourly}
    rig.move(machine.id, j.PROVISIONING, instance=instance, hourly=hourly, created=rig.now().isoformat())
    rig.move(machine.id, j.READY)
    return machine.id


def passes(rig, providers, n=1, **kwargs):
    kwargs.setdefault("machine_key", KEY)
    return [reap(rig, providers, services=("vast",), **kwargs) for _ in range(n)][-1]


def test_a_renewed_busy_machine_is_left_alone(world):
    rig, clock, fake, providers = world
    session(rig)
    mid = running(rig, fake)
    rig.move(mid, j.BUSY, run_pid=10, run_mark="a")
    for _ in range(4):
        clock.forward(minutes=20)
        rig.renew([mid])
        out = passes(rig, providers)
    assert rig.machines()[mid].state == j.BUSY and "101" in fake.instances and not out.failed


def test_an_expired_lease_drains_destroys_and_is_gone_one_pass_later(world):
    rig, clock, fake, providers = world
    session(rig)
    mid = running(rig, fake)
    rig.move(mid, j.BUSY, run_pid=10, run_mark="a")
    clock.forward(minutes=31)
    passes(rig, providers)
    item = rig.machines()[mid]
    assert item.state == j.DRAINING and item.reason == "lease expired" and fake.instances == {}
    clock.forward(minutes=5)
    out = passes(rig, providers)
    assert rig.machines()[mid].state == j.GONE and any("gone" in line for line in out.lines)


def test_a_busy_machine_whose_run_died_goes_back_to_ready_and_then_idles_out(world):
    rig, clock, fake, providers = world
    session(rig)
    mid = running(rig, fake)
    rig.move(mid, j.BUSY, run_pid=10, run_mark="a")
    passes(rig, providers, alive=lambda pid, mark: False)
    assert rig.machines()[mid].state == j.READY and rig.machines()[mid].reason == "its run is gone"
    clock.forward(minutes=15)
    rig.renew([mid])
    passes(rig, providers)
    assert rig.machines()[mid].state == j.DRAINING and rig.machines()[mid].reason == "idle 15 min"


def test_fifteen_idle_minutes_drain_a_ready_machine(world):
    rig, clock, fake, providers = world
    session(rig)
    mid = running(rig, fake)
    clock.forward(minutes=14)
    passes(rig, providers)
    assert rig.machines()[mid].state == j.READY
    clock.forward(minutes=1)
    passes(rig, providers)
    assert rig.machines()[mid].reason == "idle 15 min"


def test_a_session_past_its_end_closes_and_its_machines_drain(world):
    rig, clock, fake, providers = world
    session(rig, hours=1)
    mid = running(rig, fake)
    rig.move(mid, j.BUSY, run_pid=10, run_mark="a")
    clock.forward(minutes=61)
    rig.renew([mid])
    passes(rig, providers, n=2)
    assert rig.machines()[mid].state == j.GONE and rig.machines()[mid].reason == "session ended"
    assert rig.sessions()["s1"].state == j.CLOSED and rig.sessions()["s1"].reason == "session ended"


def test_the_budget_drains_at_ninety_percent_projected_one_pass_ahead(world):
    rig, clock, fake, providers = world
    session(rig, budget=0.10)
    mid = running(rig, fake, hourly=0.60)              # 0.05 $ a pass
    rig.move(mid, j.BUSY, run_pid=10, run_mark="a")
    clock.forward(minutes=3)
    passes(rig, providers)
    assert rig.machines()[mid].state == j.BUSY          # 0.03 + 0.05 = 0.08 < 0.09
    clock.forward(minutes=1)
    passes(rig, providers)
    assert rig.machines()[mid].reason == "budget" and rig.sessions()["s1"].reason == "budget"


def test_the_price_is_refreshed_from_the_listing(world):
    rig, clock, fake, providers = world
    session(rig)
    mid = running(rig, fake, hourly=0.30)
    fake.instances["101"]["dph_total"] = 0.45
    passes(rig, providers)
    assert rig.machines()[mid].hourly == 0.45


def test_turning_rig_off_drains_what_is_not_running_and_lets_a_run_end(world):
    rig, clock, fake, providers = world
    session(rig)
    idle = running(rig, fake, instance="101")
    working = running(rig, fake, instance="102")
    rig.move(working, j.BUSY, run_pid=10, run_mark="a")
    passes(rig, providers, on=False)
    assert rig.machines()[idle].state == j.DRAINING and rig.machines()[idle].reason == "rig turned off"
    assert rig.machines()[working].state == j.BUSY
    rig.move(working, j.READY)
    passes(rig, providers, on=False)
    assert rig.machines()[working].state == j.DRAINING


def test_a_destroy_that_reports_success_but_leaves_the_instance_ends_in_stuck(world):
    rig, clock, fake, providers = world
    session(rig)
    mid = running(rig, fake)
    fake.sticky.add("101")
    clock.forward(minutes=31)
    for _ in range(3):
        passes(rig, providers)
        assert rig.machines()[mid].state == j.DRAINING
    out = passes(rig, providers)
    assert rig.machines()[mid].state == j.STUCK and out.stuck == [mid]
    fake.sticky.clear()
    passes(rig, providers, n=2)
    assert rig.machines()[mid].state == j.GONE


def test_a_failed_listing_never_reads_as_gone_and_destroys_nothing(world):
    rig, clock, fake, providers = world
    session(rig)
    mid = running(rig, fake)
    clock.forward(minutes=31)
    fake.listing_status = 503
    for _ in range(4):
        out = passes(rig, providers)
        assert out.failed
    assert rig.machines()[mid].state == j.STUCK
    assert not any(method == "DELETE" for method, _, _ in fake.requests)   # a label not seen is never destroyed


def test_a_destroy_needs_the_label_the_journal_holds(world):
    rig, clock, fake, providers = world
    session(rig)
    mid = running(rig, fake, instance="101")
    fake.instances["777"] = {"label": "echoes render box"}
    rig.note(mid, instance="777")                      # a forged or damaged line names the person's own instance
    clock.forward(minutes=31)
    out = passes(rig, providers)
    assert "777" in fake.instances
    assert rig.machines()[mid].state == j.STUCK and rig.machines()[mid].reason == "label mismatch"
    assert out.stuck == [mid]
    assert "101" not in fake.instances                 # the real one, no longer named by its machine, is an orphan


@pytest.mark.parametrize("forged_label", ["", "flotilla:ffffffffffff:m1"])
def test_a_forged_machine_line_never_destroys_what_is_not_this_machines(world, forged_label):
    rig, clock, fake, providers = world
    session(rig)
    fake.instances["777"] = {"label": forged_label or None}   # the person's own, or another machine's
    path = rig.store.root / "rig.jsonl"
    with open(path, "a", encoding="utf-8") as handle:
        handle.write('{"at": "2026-10-06T20:00:00+00:00", "kind": "machine", "id": "m1", "state": "draining", '
                     f'"session": "s1", "provider": "vast", "instance": "777", "label": "{forged_label}"}}\n')
    passes(rig, providers)
    assert "777" in fake.instances and rig.machines()["m1"].reason == "label mismatch"


def test_without_a_machine_key_the_pass_records_but_destroys_nothing(world):
    rig, clock, fake, providers = world
    session(rig)
    mid = running(rig, fake)
    clock.forward(minutes=31)
    out = passes(rig, providers, machine_key=None)
    assert rig.machines()[mid].state == j.DRAINING and "101" in fake.instances and out.failed
    assert not any(method == "DELETE" for method, _, _ in fake.requests)


def test_an_unknown_provider_fails_loudly_and_marks_nothing_gone(world):
    rig, clock, fake, _ = world
    session(rig)
    mid = running(rig, fake)
    clock.forward(minutes=31)

    def providers(name):
        raise pv.ProviderError("no vast key")
    out = passes(rig, providers)
    assert out.failed and rig.machines()[mid].state == j.DRAINING
    assert any("no vast key" in line for line in out.lines)


def test_an_orphan_with_our_label_is_destroyed(world):
    rig, clock, fake, providers = world
    fake.instances["555"] = {"label": f"flotilla:{KEY}:m9"}
    out = passes(rig, providers)
    assert "555" not in fake.instances and any("orphan" in line for line in out.lines)


def test_a_machine_being_created_is_never_an_orphan(world):
    rig, clock, fake, providers = world
    session(rig)
    machine = rig.add_machine("s1", "vast", label_of)   # requested: the create answered, the id not yet written
    fake.instances["900"] = {"label": machine.label}
    passes(rig, providers)
    assert "900" in fake.instances


def test_an_instance_of_a_machine_the_journal_calls_gone_is_destroyed_again(world):
    rig, clock, fake, providers = world
    session(rig)
    mid = running(rig, fake, instance="101")
    clock.forward(minutes=31)
    passes(rig, providers, n=2)
    assert rig.machines()[mid].state == j.GONE
    fake.instances["101"] = {"label": label_of(mid)}
    passes(rig, providers)
    assert "101" not in fake.instances


def test_an_unlabelled_instance_is_never_touched(world):
    rig, clock, fake, providers = world
    fake.instances.update({"777": {"label": None}, "778": {"label": "echoes render box"},
                           "779": {"label": f"flotilla:{KEY}:not-a-machine"}})
    passes(rig, providers)
    assert set(fake.instances) == {"777", "778", "779"}
    assert not any(method == "DELETE" for method, _, _ in fake.requests)


def test_another_machines_label_is_not_an_orphan(world):
    rig, clock, fake, providers = world
    fake.instances["888"] = {"label": "flotilla:ffffffffffff:m1"}
    passes(rig, providers)
    assert "888" in fake.instances


def test_a_failed_machine_without_an_instance_is_gone_at_once(world):
    rig, clock, fake, providers = world
    session(rig)
    machine = rig.add_machine("s1", "vast", label_of)
    rig.move(machine.id, j.FAILED, reason="no offer")
    passes(rig, providers)
    assert rig.machines()[machine.id].state == j.GONE


def test_a_closing_session_with_nothing_live_closes_with_its_reason(world):
    rig, clock, fake, providers = world
    session(rig)
    mid = running(rig, fake, hourly=0.60)
    rig.set_session("s1", j.CLOSING, reason="closed by the person")
    clock.forward(minutes=10)
    passes(rig, providers, n=2)
    assert rig.machines()[mid].state == j.GONE
    assert rig.sessions()["s1"].state == j.CLOSED and rig.sessions()["s1"].reason == "closed by the person"


def test_a_renew_landing_after_the_decision_wins(world, monkeypatch):
    rig, clock, fake, providers = world
    session(rig)
    mid = running(rig, fake)
    rig.move(mid, j.BUSY, run_pid=10, run_mark="a")
    clock.forward(minutes=31)
    real_move = rig.move

    def renew_first(machine_id, state, *args, **kwargs):
        if state == j.DRAINING:
            rig.renew([machine_id])                    # the run renews between the reaper's decision and its move
        return real_move(machine_id, state, *args, **kwargs)
    monkeypatch.setattr(rig, "move", renew_first)
    passes(rig, providers)
    monkeypatch.setattr(rig, "move", real_move)
    assert rig.machines()[mid].state == j.BUSY


def test_the_emergency_pass_destroys_every_instance_with_this_machines_label(world):
    rig, clock, fake, providers = world
    fake.instances.update({"101": {"label": f"flotilla:{KEY}:m1"}, "102": {"label": "echoes render box"},
                           "103": {"label": "flotilla:ffffffffffff:m1"}})
    out = emergency(providers, machine_key=KEY, services=("vast",))
    assert set(fake.instances) == {"102", "103"} and out.failed


def test_the_reaper_is_needed_while_anything_lives_and_not_after(world):
    rig, clock, fake, providers = world
    session(rig, hours=1)
    assert needs_reaper(rig)
    running(rig, fake)
    clock.forward(minutes=61)
    passes(rig, providers, n=2)
    assert not needs_reaper(rig)
