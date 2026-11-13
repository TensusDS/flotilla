import pytest

from flotilla.lane.policy import (FROZEN, ORDERED, RECLAIM_MARGIN_MB, RUNNING, WAITING, Decision, Machine, Run,
                                  decide, immortal, is_heavy, priority)

SERVER = Machine(cpus=8, give_mb=9000, free_mb=9000, swap_free_mb=8000, swap_total_mb=8000)


def run(id, state, mem, heavy=False, par=1, **kw):
    return Run(id, state, mem, heavy, par, **kw)


def machine(free, swap=None, swap_total=None, **kw):
    return Machine(cpus=8, give_mb=9000, free_mb=free, swap_free_mb=8000 if swap is None else swap,
                   swap_total_mb=8000 if swap_total is None else swap_total, **kw)


def test_priority_is_the_formula():
    r = Run("b1", WAITING, 100, False, 1, kind="push", waited_s=300, ran_s=450)
    assert priority(r, 900) == pytest.approx(0.2 * 1.0 + 0.3 * 0.5 + 0.5 * 0.5)

def test_the_first_run_on_an_empty_machine_starts_whatever_its_size():
    assert decide([run("b1", WAITING, 50_000)], machine(free=1000), "b1").start

def test_a_run_that_does_not_fit_free_memory_waits_and_says_so():
    d = decide([run("h", RUNNING, 6000), run("b2", WAITING, 2000)], machine(free=1200), "b2")
    assert not d.start and "memory: 2000 MB needed, 1200 MB of room" in d.why

def test_the_second_heavy_waits_past_the_cpu_bound():   # 8 + 8 > 1.5 * 8
    d = decide([run("h", RUNNING, 2000, True, 8), run("b2", WAITING, 2000, True, 8)], machine(free=6000), "b2")
    assert not d.start and "heavy runs' cores: 8 of 12" in d.why

def test_heavies_leave_twenty_percent_to_light_runs():   # 4000 + 3500 > 0.8 * 9000
    d = decide([run("h", RUNNING, 4000, True, 2), run("b2", WAITING, 3500, True, 2)], machine(free=5000), "b2")
    assert not d.start and "reserve 1800 MB for light runs" in d.why
    assert "3200 MB of room for heavy runs" in d.why and "available" not in d.why   # the share, not the room, holds it

def test_a_light_run_starts_in_the_reserve_beside_heavies():
    runs = [run("h", RUNNING, 7000, True, 4), run("b2", WAITING, 500)]
    assert decide(runs, machine(free=1500), "b2").start

def test_light_passes_a_heavy_that_does_not_fit():
    runs = [run("h", RUNNING, 4000, True, 8), run("b2", WAITING, 4000, True, 8, order=2),
            run("b3", WAITING, 300, order=3)]
    assert decide(runs, machine(free=3000), "b3").start

def test_no_heavy_passes_a_higher_heavy_that_does_not_fit():
    runs = [run("h", RUNNING, 3000, True, 6), run("b2", WAITING, 5000, True, 4, order=2, waited_s=600),
            run("b3", WAITING, 1900, True, 2, order=3)]
    d = decide(runs, machine(free=4000), "b3")
    assert not d.start and "behind heavy b2" in d.why

def test_a_light_waiter_freezes_the_lowest_younger_run():
    runs = [run("old", RUNNING, 1500, ran_s=600, since_start_s=600), run("young", RUNNING, 1500, ran_s=90,
            since_start_s=90), run("b3", WAITING, 1000, kind="push", waited_s=300)]
    d = decide(runs, machine(free=200), "b3")
    assert d.freeze == (("young", 800 + RECLAIM_MARGIN_MB),) and not d.start

def test_an_immortal_run_is_never_frozen():
    runs = [run("old", RUNNING, 3000, ran_s=900, since_start_s=900), run("b2", WAITING, 1000, kind="push")]
    d = decide(runs, machine(free=0), "b2")
    assert d.freeze == () and "no candidate to freeze" in d.why

def test_a_heavy_waiter_never_freezes_a_heavy_run():
    runs = [run("h", RUNNING, 4000, True, 4, ran_s=120, since_start_s=120),
            run("b2", WAITING, 4000, True, 4, kind="push", waited_s=600)]
    assert decide(runs, machine(free=500), "b2").freeze == ()

def test_a_run_in_its_grace_minute_is_not_a_candidate():
    runs = [run("y", RUNNING, 2000, ran_s=30, since_start_s=30), run("b2", WAITING, 1000, kind="push")]
    assert decide(runs, machine(free=0), "b2").freeze == ()

def test_equal_priority_is_no_reason_to_freeze():   # margin 0: strictly lower only
    runs = [run("a", RUNNING, 2000, ran_s=120, since_start_s=120), run("b2", WAITING, 1000, ran_s=120)]
    assert decide(runs, machine(free=0), "b2").freeze == ()

def test_nothing_is_frozen_when_all_candidates_together_do_not_make_room():
    runs = [run("a", RUNNING, 500, ran_s=90, since_start_s=90), run("b2", WAITING, 3000, kind="push")]
    assert decide(runs, machine(free=1000), "b2").freeze == ()

def test_no_swap_freezes_nothing():
    runs = [run("y", RUNNING, 2000, ran_s=90, since_start_s=90), run("b2", WAITING, 1000, kind="push")]
    d = decide(runs, machine(free=0, swap=0), "b2")
    assert d.freeze == () and "swap cannot take 1128 MB" in d.why

def test_the_machines_swap_stays_at_most_four_fifths_used():   # systemd-oomd kills at 90 %
    runs = [run("y", RUNNING, 2000, ran_s=90, since_start_s=90), run("b2", WAITING, 1000, kind="push")]
    # 10000 of 12000 used; 90 % of the free 2000 would allow 1800, but 10000 + 1128 is 93 % used
    d = decide(runs, machine(free=0, swap=2000, swap_total=12000), "b2")
    assert d.freeze == () and "swap cannot take 1128 MB" in d.why
    # the edge itself: 9600 - 1128 = 8472 used of 12000 is 70.6 % before, 80.0 % after -> allowed; one MB less, not
    assert decide(runs, machine(free=0, swap=3528, swap_total=12000), "b2").freeze != ()
    assert decide(runs, machine(free=0, swap=3527, swap_total=12000), "b2").freeze == ()

def test_a_frozen_run_thaws_ahead_of_a_later_run_of_its_purpose():
    runs = [run("f", FROZEN, 1500, ran_s=300, waited_s=900, order=1), run("n", WAITING, 1500, order=5)]
    m = machine(free=1600)
    assert decide(runs, m, "f").start and not decide(runs, m, "n").start

def test_two_runs_do_not_freeze_each_other_in_turn():
    # b froze a; a, now frozen, outranks nothing that froze it: a runs again only when it fits, never by an order
    runs = [run("a", FROZEN, 2000, ran_s=200, waited_s=400), run("b", RUNNING, 2000, kind="push", ran_s=10,
            since_start_s=10, waited_s=300)]
    assert decide(runs, machine(free=100), "a").freeze == ()

def test_an_ordered_victim_is_not_ordered_again():
    runs = [run("v", ORDERED, 2000, ran_s=90, since_start_s=90), run("b3", WAITING, 1000, kind="push")]
    assert decide(runs, machine(free=0), "b3").freeze == ()

def test_ten_runs_at_once_at_most():
    runs = [run(f"r{n}", RUNNING, 10) for n in range(10)] + [run("b", WAITING, 10)]
    assert "runs at once: 10 of 10" in decide(runs, machine(free=8000), "b").why

def test_memory_unknown_admits_by_cap_and_cpu_only_and_freezes_nothing():
    runs = [run("a", RUNNING, 2000, ran_s=90, since_start_s=90), run("b", WAITING, 1000, kind="push")]
    d = decide(runs, Machine(cpus=8, give_mb=None, free_mb=None, swap_free_mb=8000), "b")
    assert d.start and d.freeze == ()

def test_freezing_off_orders_nothing():
    runs = [run("y", RUNNING, 2000, ran_s=90, since_start_s=90), run("b2", WAITING, 1000, kind="push")]
    d = decide(runs, machine(free=0, freeze=False), "b2")
    assert d.freeze == () and "freezing is off" in d.why

def test_heavy_is_sticky():
    assert is_heavy(100, 9000, was=True) and is_heavy(1800, 9000) and not is_heavy(1799, 9000)


def test_the_why_names_why_each_run_cannot_be_frozen():
    runs = [run("old", RUNNING, 3000, ran_s=900, since_start_s=900), run("y", RUNNING, 500, ran_s=30, since_start_s=30),
            run("b2", WAITING, 1000, kind="push")]
    d = decide(runs, machine(free=0), "b2")
    assert "old is immortal" in d.why and "y is in its first minute" in d.why


def test_a_higher_waiter_that_does_not_fit_freezes_first():
    runs = [run("y", RUNNING, 2000, ran_s=90, since_start_s=90), run("a", WAITING, 1000, kind="push", waited_s=600,
            order=2), run("b", WAITING, 1000, kind="push", waited_s=300, order=3)]
    assert decide(runs, machine(free=0), "b").freeze == ()
    assert decide(runs, machine(free=0), "a").freeze == (("y", 1000 + RECLAIM_MARGIN_MB),)


def test_a_frozen_run_never_orders_a_freeze():
    runs = [run("f", FROZEN, 1000, ran_s=600, waited_s=900, kind="push"), run("y", RUNNING, 2000, ran_s=90,
            since_start_s=90)]
    d = decide(runs, machine(free=0), "f")
    assert not d.start and d.freeze == ()


def test_a_heavy_behind_a_heavy_orders_nothing():
    runs = [run("h", RUNNING, 3000, True, 6), run("b2", WAITING, 5000, True, 4, order=2, waited_s=600),
            run("b3", WAITING, 1900, True, 2, order=3), run("y", RUNNING, 1000, ran_s=90, since_start_s=90)]
    d = decide(runs, machine(free=1000), "b3")
    assert d.freeze == () and "behind heavy b2" in d.why


def test_memory_unknown_freezes_nothing_even_at_the_cap():
    runs = [run(f"r{n}", RUNNING, 10, ran_s=90, since_start_s=90) for n in range(10)] + \
        [run("b", WAITING, 10, kind="push")]
    d = decide(runs, Machine(cpus=8, give_mb=None, free_mb=None, swap_free_mb=8000), "b")
    assert not d.start and d.freeze == () and "runs at once: 10 of 10" in d.why


def test_the_heavy_memory_bound_is_not_cured_by_freezing():
    # free memory is there; the heavies' 80 % is what is full - freezing the light run y would change nothing
    runs = [run("h", RUNNING, 4000, True, 2, ran_s=120, since_start_s=120),
            run("y", RUNNING, 1000, ran_s=90, since_start_s=90), run("b2", WAITING, 3500, True, 2, kind="push")]
    d = decide(runs, machine(free=4000), "b2")
    assert d.freeze == () and "reserve 1800 MB for light runs" in d.why and "freeze" not in d.why


def test_a_waiter_whose_orders_are_out_holds_its_memory_against_the_runs_behind_it():
    # h froze y for itself: what is free is h's until h starts - a light run behind h does not take it, and y does
    # not thaw into it; a run ahead of h is not held back
    runs = [run("a", WAITING, 1000, kind="push", waited_s=600, order=0),
            run("y", FROZEN, 1128, ran_s=120, waited_s=200, order=1),
            run("h", WAITING, 5000, True, 4, kind="push", waited_s=600, order=2, holds=True),
            run("l", WAITING, 1000, order=3), run("o", RUNNING, 3000, ran_s=300, since_start_s=300)]
    m = machine(free=2500)
    assert decide(runs, m, "a").start
    assert not decide(runs, m, "l").start and not decide(runs, m, "y").start
    assert decide(runs, machine(free=6100), "h").start
    without = [r if r.id != "h" else run("h", WAITING, 5000, True, 4, kind="push", waited_s=600, order=2)
               for r in runs]
    assert decide(without, m, "y").start   # no order out: the frozen run thaws into the room, passing the heavy


def test_a_heavy_held_by_its_cpu_bound_orders_no_freeze():
    # memory is short AND the heavies' cores are full: freezing the light y would free memory the heavy still could
    # not use (the replay of the model's queues froze one run four times this way)
    runs = [run("h", RUNNING, 3000, True, 8, ran_s=900, since_start_s=900),
            run("y", RUNNING, 3000, ran_s=90, since_start_s=90), run("b2", WAITING, 4000, True, 8, kind="push")]
    d = decide(runs, machine(free=2000), "b2")
    assert d.freeze == () and "heavy runs' cores: 8 of 12" in d.why


def test_a_waiter_with_orders_out_orders_nothing_more():   # final review C1
    # b3 ordered y frozen a second ago; y has not answered, so the memory is not free yet - b3 waits for it and
    # does not freeze z too
    runs = [run("y", ORDERED, 1500, ran_s=90, since_start_s=90), run("z", RUNNING, 1500, ran_s=90, since_start_s=90),
            run("b3", WAITING, 1000, kind="push", waited_s=300, holds=True)]
    d = decide(runs, machine(free=200), "b3")
    assert d.freeze == () and not d.start and "waits for its freeze" in d.why


def test_orders_not_yet_answered_count_against_the_swap():
    runs = [run("y", RUNNING, 2000, ran_s=90, since_start_s=90), run("b2", WAITING, 1000, kind="push")]
    # 8000 swap, all free, but 6000 MB already ordered elsewhere and not yet moved: 6000 + 1128 > 6400
    d = decide(runs, Machine(cpus=8, give_mb=9000, free_mb=0, swap_free_mb=8000, swap_total_mb=8000,
                             swap_pending_mb=6000), "b2")
    assert d.freeze == () and "swap cannot take 1128 MB" in d.why


def test_memory_unknown_still_bounds_the_cores():   # final review I1
    runs = [run(f"r{n}", RUNNING, 10, par=4) for n in range(3)] + [run("b", WAITING, 10, par=4)]
    d = decide(runs, Machine(cpus=8, give_mb=None, free_mb=None, swap_free_mb=8000), "b")
    assert not d.start and "cores: 12 of 12" in d.why


def test_a_lone_heavy_keeps_the_light_reserve_beside_light_runs():   # final review I2, spec 2.4
    runs = [run("l", RUNNING, 500), run("h", WAITING, 8000, True, 4)]
    d = decide(runs, machine(free=8500), "h")
    assert not d.start and "reserve 1800 MB for light runs" in d.why
    assert decide([run("h", WAITING, 8000, True, 4)], machine(free=8500), "h").start   # an empty machine: it goes



def test_a_run_that_just_refused_this_waiter_is_no_candidate():   # review of ed148e3, 1: back-off
    runs = [run("y", RUNNING, 2000, ran_s=90, since_start_s=90, refused_for="b2"),
            run("b2", WAITING, 1000, kind="push")]
    d = decide(runs, machine(free=0), "b2")
    assert d.freeze == () and "y refused an order a moment ago" in d.why



def test_a_run_that_just_refused_any_waiter_is_no_candidate():   # review of the follow-up, I2
    runs = [run("y", RUNNING, 2000, ran_s=90, since_start_s=90, refused_for="b9"),
            run("b2", WAITING, 1000, kind="push")]
    d = decide(runs, machine(free=0), "b2")
    assert d.freeze == () and "y refused an order a moment ago" in d.why


def test_the_holders_check_leaves_heaviness_to_the_waiter():   # review of the follow-up, I1
    from flotilla.lane.policy import victim_reason
    me, them = run("h", RUNNING, 4000, True, 4, ran_s=120, since_start_s=120), run("w", WAITING, 4000, True, 4,
                                                                                    kind="push", waited_s=600)
    assert "heavy" in victim_reason(me, them, machine(free=0))
    assert victim_reason(me, them, machine(free=0), by_rules_only=True) == ""


# --- the daemon's inputs (lane daemon design, 2026-11-07) ---

from flotilla.lane import policy   # noqa: E402

IMM = 1000.0


def aged(id, p, grew=0, **kw):
    """A running run whose priority is `p` with immortality at 1000 s: type part 0.06, run part 0.0005 per second."""
    return run(id, RUNNING, 1000, ran_s=(p - 0.06) / 0.0005, since_start_s=600, grew_mb=grew, **kw)


def test_start_ceiling():
    assert (policy.start_ceiling(100), policy.start_ceiling(0), policy.start_ceiling(4000)) == (512, 512, 4400)
    assert (policy.slack(100), policy.slack(4000)) == (256, 400)


def test_room_counts_unused_ceiling_as_promised():
    m = Machine(cpus=8, give_mb=9000, free_mb=7500, swap_free_mb=8000, available_mb=10000, floor_mb=2000)
    runs = [run("a", RUNNING, 1000, ceiling_mb=3000), run("b", RUNNING, 500), run("w", WAITING, 9000,
                                                                                    ceiling_mb=99999)]
    assert policy.room(runs, m) == 10000 - 2000 - 2000 - 500
    assert policy.room(runs, Machine(cpus=8, give_mb=None, free_mb=None, swap_free_mb=None)) is None


def test_victim_is_the_lowest_priority():
    assert policy.victim([aged("a", 0.30), aged("b", 0.20)], IMM).id == "b"
    assert policy.victim([], IMM) is None


def test_victim_tie_break_by_growth():   # the person's ruling, 2026-11-07
    assert policy.victim([aged("low", 0.20, 100), aged("near", 0.24, 3000), aged("far", 0.40, 9000)], IMM).id == "near"
    assert policy.victim([aged("low", 0.20, 100), aged("near", 0.26, 3000), aged("far", 0.40, 9000)], IMM).id == "low"


def test_daemon_reason_ignores_the_grace():   # ruling 5
    m = machine(free=0, immortal_s=IMM)
    young = run("y", RUNNING, 1000, ran_s=10, since_start_s=10)
    assert policy.daemon_reason(young, m) == ""
    assert "pinned" in policy.daemon_reason(run("y", RUNNING, 1000, pinned=True), m)
    assert "immortal" in policy.daemon_reason(run("y", RUNNING, 1000, ran_s=IMM), m)
    assert policy.daemon_reason(run("y", RUNNING, 1000, refused_for="x"), m)
    assert policy.daemon_reason(run("y", FROZEN, 1000), m)
    assert policy.daemon_reason(run("y", RUNNING, 1000, freezable=False), m)



def test_a_floor_is_believed_up_to_a_quarter_of_the_machine():   # final review C2: any session writes floor_mb
    assert policy.clamp_floor(9_000_000, 15603) == 3900
    assert policy.clamp_floor(1000, 15603) == 1000
    assert policy.clamp_floor(1500, 4000) == 1500        # never under the default floor
    assert policy.clamp_floor(9_000_000, None) == 4096



def test_a_negative_floor_is_no_floor():   # fix review R9
    assert policy.clamp_floor(-5000, 15603) == 0


def test_the_heavy_share_is_named_only_when_it_and_not_the_room_holds_a_heavy_run_back():   # review of B, finding 4
    d = decide([run("h", RUNNING, 4000, True, 2), run("b2", WAITING, 3500, True, 2)], machine(free=1000), "b2")
    assert "1000 MB of room" in d.why and "for heavy runs" not in d.why



def test_a_run_no_ceiling_holds_keeps_its_admitted_memory_and_the_slack_promised():   # third review of B, 1 and 2
    from flotilla.lane import policy
    unscoped = run("a", RUNNING, 300, ceiling_mb=512, admitted_mb=4000, scoped=False, since_start_s=600)
    assert policy.promised(unscoped, 512) == 4000 + policy.slack(4000) - 300
    unbound = run("a", RUNNING, 300, ceiling_mb=512, admitted_mb=4000, scoped=True, bound=False, since_start_s=600)
    assert policy.promised(unbound, 512) == 4000 + policy.slack(4000) - 300
    by_hand = run("h", RUNNING, 300, scoped=False, since_start_s=600)   # nothing admitted it by a figure
    assert policy.promised(by_hand, None) == 0


def test_a_ceilings_headroom_is_promised_beyond_the_views_own_reservation():   # third review of B, finding 4
    from flotilla.lane import policy
    # first half-minute: the view reserved 300 (admitted 3600 less a tree of 3300); the group reads 300
    r = run("a", RUNNING, 300, ceiling_mb=3000, admitted_mb=3600, reserved_mb=300, scoped=True, since_start_s=10)
    assert policy.promised(r, 3000) == 3000 - 300 - 300


def test_a_heavy_run_larger_than_the_heavy_share_says_it_starts_only_on_an_empty_lane():   # field test 2026-11-11
    # 0.8 * 9000 = 7200 is the most heavy runs may take: 7500 never fits beside any run
    runs = [run("other", RUNNING, 800), run("big", WAITING, 7500, True, 2)]
    d = decide(runs, machine(free=8000), "big")
    assert not d.start and "7500 MB needed, more than the 7200 MB heavy runs may take" in d.why
    assert "starts only when the lane is empty" in d.why and d.freeze == ()


def test_every_bound_that_holds_a_heavy_run_is_named_not_only_the_first():   # field test 2026-11-11
    runs = [run("h", RUNNING, 1000, True, 8), run("big", WAITING, 7500, True, 8)]
    d = decide(runs, machine(free=7000), "big")
    assert "heavy runs' cores: 8 of 12" in d.why and "starts only when the lane is empty" in d.why


def test_a_heavy_behind_one_that_waits_for_an_empty_lane_is_told_so():   # field test 2026-11-11
    runs = [run("other", RUNNING, 800), run("big", WAITING, 7500, True, 2, order=1, waited_s=600),
            run("b3", WAITING, 2000, True, 2, order=2)]
    d = decide(runs, machine(free=8000), "b3")
    assert not d.start and "behind heavy big, which starts only when the lane is empty" in d.why


def test_a_heavy_behind_one_that_only_lacks_room_is_told_just_that():
    runs = [run("h", RUNNING, 3000, True, 6), run("b2", WAITING, 5000, True, 4, order=2, waited_s=600),
            run("b3", WAITING, 1900, True, 2, order=3)]
    d = decide(runs, machine(free=4000), "b3")
    assert d.why == "behind heavy b2"


NINE, HALF = "2026-11-13T09:00:00+00:00", "2026-11-13T09:30:00+00:00"


def _equal(id, project, served, order, **kw):
    """A waiter of saturated wait: every one of them has the same priority."""
    return run(id, WAITING, 100, waited_s=900, order=order, project=project, fair=(0, served) if served is not None
               else (), **kw)


def test_the_fair_line_weighs_each_place_again():   # review of 8dfcc24: one project took every slot of a tick
    from flotilla.lane.policy import line
    runs = [_equal("a1", "alpha", NINE, 1), _equal("a2", "alpha", NINE, 2),
            _equal("b1", "beta", HALF, 3)]
    assert [r.id for r in line(runs, SERVER)] == ["a1", "b1", "a2"]
    two = machine(free=9000, max_runs=2)
    assert [decide(runs, two, r.id).start for r in runs] == [True, False, True]


def test_the_fifo_line_is_priority_then_place():
    from flotilla.lane.policy import line
    runs = [_equal("a1", "alpha", None, 1), _equal("a2", "alpha", None, 2), _equal("b1", "beta", None, 3, holds=True),
            run("p", WAITING, 100, kind="push", waited_s=900, order=4)]
    assert [r.id for r in line(runs, SERVER)] == ["p", "a1", "a2", "b1"]   # holding changes nothing a fifo machine did


def test_under_the_fair_share_a_waiter_whose_freeze_is_out_keeps_its_place():   # review of 8dfcc24, minor
    """Its project now holds a run, so its fair key fell behind an equal of another project: that one would take the
    room the freeze made, and the frozen run would have stopped for nothing."""
    from flotilla.lane.policy import line
    runs = [_equal("a1", "alpha", NINE, 1, holds=True),
            _equal("b1", "beta", "", 2)]
    runs[0] = Run(**{**runs[0].__dict__, "fair": (1, NINE)})
    assert [r.id for r in line(runs, SERVER)] == ["a1", "b1"]


def test_a_frozen_run_takes_no_turn_in_the_fair_line():
    from flotilla.lane.policy import line
    frozen = run("f", FROZEN, 100, waited_s=900, order=1, project="alpha", fair=(1, NINE))
    runs = [frozen, _equal("a1", "alpha", NINE, 2), _equal("b1", "beta", HALF, 3)]
    runs[1] = Run(**{**runs[1].__dict__, "fair": (1, NINE)})
    runs[2] = Run(**{**runs[2].__dict__, "fair": (1, HALF)})
    assert [r.id for r in line(runs, SERVER)][1:] == ["a1", "b1"]


def test_a_waiter_that_cannot_start_costs_its_project_no_turn():   # review of e098233
    """alpha is due; its first waiter does not fit, its second does. The second takes alpha's turn: the waiter that
    cannot start takes none, as the waiters before 0.31 skipped a fair choice that could not go."""
    runs = [_equal("a1", "alpha", NINE, 1), _equal("b1", "beta", HALF, 2), _equal("a2", "alpha", NINE, 3)]
    runs[0] = Run(**{**runs[0].__dict__, "mem_mb": 5000})
    runs.append(run("g", RUNNING, 100, order=0, project="gamma", fair=(1, HALF)))   # an empty lane starts anything
    one = machine(free=1000, max_runs=2)
    assert [decide(runs, one, r.id).start for r in runs[:3]] == [False, False, True]


def _with(r, **fields):
    return Run(**{**r.__dict__, **fields})


def test_a_turn_taken_weighs_both_the_runs_held_and_the_service():   # review of dcf78ca: two surviving mutants
    """beta holds a run, alpha none: alpha goes first. Its turn makes it hold one, served now - so beta, holding one
    and served earlier, goes next, though alpha was served before beta."""
    runs = [_equal("a1", "alpha", NINE, 1), _equal("a2", "alpha", NINE, 2),
            _with(_equal("b1", "beta", HALF, 3), fair=(1, HALF)),
            run("g", RUNNING, 100, order=0, project="beta", fair=(1, HALF))]
    two = machine(free=9000, max_runs=3)
    assert [decide(runs, two, r.id).start for r in runs[:3]] == [True, False, True]


def _behind_a_heavy():
    """A heavy push that does not fit blocks the heavy runs behind it; alpha is due, and its first waiter is heavy."""
    return [run("g", RUNNING, 100, order=0, project="gamma", fair=(1, HALF)),
            run("h", WAITING, 20_000, True, 8, kind="push", waited_s=900, order=1, project="gamma", fair=(1, HALF)),
            _with(_equal("a1", "alpha", NINE, 2), heavy=True), _equal("b1", "beta", HALF, 3),
            _equal("a2", "alpha", NINE, 4)]


def test_a_heavy_waiter_behind_a_heavy_costs_its_project_no_turn():   # review of dcf78ca: a surviving mutant
    runs = _behind_a_heavy()
    one = machine(free=1000, max_runs=2)
    assert [decide(runs, one, i).start for i in ("a1", "b1", "a2")] == [False, False, True]


def test_the_tick_starts_what_the_fair_replay_admits():   # review of dcf78ca: the daemon's path, not only decide
    from flotilla.lane import tick
    runs = _behind_a_heavy()
    one = machine(free=1000, max_runs=2, available_mb=10_000, floor_mb=1500)
    assert [start[0] for start in tick.tick(runs, one).starts] == ["a2"]
