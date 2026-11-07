"""tick: one pure decision over the whole lane - starts, ceilings, squeezes, freezes, thaws (lane daemon design, 4.3)."""

from flotilla.lane import policy
from flotilla.lane.policy import FROZEN, ORDERED, RUNNING, WAITING, Machine, Run
from flotilla.lane.tick import OVER_S, PRESSED_S, THAW_AFTER_S, tick

IMM = 1000.0


def run(id, state, mem, heavy=False, par=1, **kw):
    kw.setdefault("since_start_s", 600.0)
    return Run(id, state, mem, heavy, par, **kw)


def aged(id, p, mem=1000, **kw):
    """A running run of priority `p` (type part 0.06, run part 0.0005 per second with immortality at 1000 s)."""
    return run(id, RUNNING, mem, ran_s=(p - 0.06) / 0.0005, **kw)


def machine(available, floor=2000, swap_free=8000, swap_total=10000, **kw):
    kw.setdefault("free_mb", None if available is None else available - floor)
    return Machine(cpus=8, give_mb=None if available is None else 12000, swap_free_mb=swap_free,
                   swap_total_mb=swap_total, available_mb=available, floor_mb=floor, immortal_s=IMM, **kw)


def ceilings(actions):
    return dict(actions.ceilings)


def test_a_started_run_gets_its_start_ceiling():
    a = tick([run("w", WAITING, 100)], machine(10000))
    assert [s[0] for s in a.starts] == ["w"] and ceilings(a)["w"] == 512


def test_starts_follow_decide():
    a = tick([aged("h", 0.5, 7000), run("hi", WAITING, 1500, kind="push"), run("lo", WAITING, 1500)],
             machine(2000 + 2000))   # room for one of them
    assert [s[0] for s in a.starts] == ["hi"]
    assert "memory" in dict(a.why)["lo"]


def test_a_waiters_freeze_comes_from_decide():
    runs = [aged("low", 0.10, 3000), run("w", WAITING, 2500, kind="push", waited_s=600)]
    a = tick(runs, machine(2000 + 1000))
    assert a.orders and a.orders[0][0] == "low" and a.orders[0][2:] == ("waiter", "w")


def test_growth_doubles_while_there_is_room():
    def grown(room, at=True):
        r = aged("r", 0.3, 512, ceiling_mb=512, at_ceiling=at, scoped=True)
        return ceilings(tick([r], machine(2000 + room))).get("r")
    assert grown(10000) == 1024
    assert grown(300) == 812
    assert grown(0) is None and grown(10000, at=False) is None


def test_growth_goes_by_priority():
    hi = aged("hi", 0.45, 1000, ceiling_mb=1000, at_ceiling=True, scoped=True)
    lo = aged("lo", 0.2, 1000, ceiling_mb=1000, at_ceiling=True, scoped=True)
    a = tick([lo, hi], machine(2000 + 1000))
    assert ceilings(a) == {"hi": 2000}


def test_immortal_and_pinned_runs_lose_their_ceiling():
    old = run("old", RUNNING, 900, ran_s=IMM, ceiling_mb=1000)
    pin = run("pin", RUNNING, 900, pinned=True, ceiling_mb=1000)
    free = run("free", RUNNING, 900, ran_s=IMM)
    c = ceilings(tick([old, pin, free], machine(10000)))
    assert c["old"] is None and c["pin"] is None and "free" not in c


def test_pressed_without_room_freezes_the_lowest():
    pressed = aged("p", 0.4, 2000, ceiling_mb=2000, at_ceiling=True, pressure=80.0, pressed_s=PRESSED_S + 2,
                   scoped=True)
    lower = aged("l", 0.2, 1500, scoped=True)
    a = tick([pressed, lower], machine(2000 + 0 + 0))
    assert [(o[0], o[2], o[3]) for o in a.orders] == [("l", "pressed", "p")]   # it names the run it is for
    a = tick([pressed], machine(2000))
    assert a.orders == (("p", 0, "pressed", "p"),)
    calm = aged("p", 0.4, 2000, ceiling_mb=2000, at_ceiling=True, pressure=80.0, pressed_s=5, scoped=True)
    assert tick([calm, lower], machine(2000)).orders == ()


def test_overload_squeezes_the_victim_first():
    v = aged("v", 0.2, 3500, ceiling_mb=3756, scoped=True)
    a = tick([v], machine(1500, over_s=OVER_S + 2))
    assert ceilings(a) == {"v": 3000} and a.squeezed == ("v",) and a.orders == ()
    assert tick([v], machine(1500, over_s=5)).ceilings == ()


def test_the_squeeze_never_goes_under_256():
    v = aged("v", 0.2, 400, ceiling_mb=656, scoped=True)
    assert ceilings(tick([v], machine(0, over_s=OVER_S + 2)))["v"] == 256


def test_the_squeeze_is_clamped_to_the_swap_budget():
    v = aged("v", 0.2, 3500, ceiling_mb=3756, scoped=True)
    a = tick([v], machine(1000, swap_free=2500, over_s=OVER_S + 2))
    assert ceilings(a) == {"v": 3000}
    a = tick([v], machine(1000, swap_free=2000, over_s=OVER_S + 2))   # swap at 80 %: ruling 7
    assert a.ceilings == () and a.orders == (("v", 0, "over", ""),)


def test_a_squeezed_run_that_is_pressed_is_frozen():
    v = aged("v", 0.2, 3000, ceiling_mb=3000, squeezed=True, pressed_s=PRESSED_S + 2, scoped=True)
    a = tick([v], machine(1500, over_s=OVER_S + 2))
    assert len(a.orders) == 1 and a.orders[0][0] == "v" and a.orders[0][2] == "over"
    assert 0 < a.orders[0][1] <= 3000


def test_an_unscoped_victim_is_frozen_at_once():
    v = aged("v", 0.2, 3500)
    assert tick([v], machine(1500, over_s=OVER_S + 2)).orders == (("v", 0, "over", ""),)


def test_overload_ignores_the_grace():   # the person's ruling 5
    v = run("v", RUNNING, 3500, ran_s=5, since_start_s=5, ceiling_mb=3756, scoped=True)
    assert ceilings(tick([v], machine(1500, over_s=OVER_S + 2))) == {"v": 3000}


def test_overload_with_nothing_to_take():   # the person's ruling 8
    pin = run("pin", RUNNING, 4000, pinned=True)
    old = run("old", RUNNING, 4000, ran_s=IMM)
    w = run("w", WAITING, 100)
    a = tick([pin, old, w], machine(1300, over_s=OVER_S + 2))
    assert a.over_mb == 700 and a.orders == () and a.starts == () and a.squeezed == ()
    assert "nothing can be frozen" in dict(a.why)["w"]


def test_one_victim_at_a_time():
    v = aged("v", 0.2, 3500, ceiling_mb=3756, scoped=True)
    out = run("o", ORDERED, 1000, ran_s=300)
    assert tick([v, out], machine(1500, over_s=OVER_S + 2)).ceilings == ()
    showing = aged("s", 0.3, 3000, ceiling_mb=2000, squeezed=True, scoped=True)   # 3000 > 2000 + 256: not shown
    a = tick([v, showing], machine(1500, over_s=OVER_S + 2))
    assert a.ceilings == () and a.orders == ()


def test_freezing_off_ends_at_a_squeeze():
    v = aged("v", 0.2, 3000, ceiling_mb=3000, squeezed=True, pressed_s=PRESSED_S + 2, scoped=True)
    assert tick([v], machine(1500, over_s=OVER_S + 2, freeze=False)).orders == ()


def test_squeezed_runs_are_restored_before_any_start():
    s = aged("s", 0.3, 2000, ceiling_mb=2000, squeezed=True, scoped=True)
    a = tick([s, run("w", WAITING, 100)], machine(2000 + 5000))
    assert ceilings(a)["s"] == 2256 and a.restored == ("s",) and a.starts == ()


def frozen(cause, **kw):
    kw.setdefault("ceiling_mb", 2000)
    kw.setdefault("frozen_s", THAW_AFTER_S + 1)
    return run("f", FROZEN, kw.pop("mem", 1000), cause=cause, ran_s=300, **kw)


def test_a_daemon_freeze_thaws_after_30_s_with_room_for_its_headroom():
    f = frozen("over", reclaimed_mb=1000)
    assert tick([f], machine(2000 + 1300)).thaws == ("f",)
    assert tick([f], machine(2000 + 1200)).thaws == ()
    assert tick([frozen("over", reclaimed_mb=1000, frozen_s=20)], machine(2000 + 5000)).thaws == ()
    assert tick([f], machine(1500, over_s=OVER_S + 2)).thaws == ()


def test_a_pressed_freeze_needs_room_for_a_doubling():
    f = frozen("pressed", reclaimed_mb=500)
    assert tick([f], machine(2000 + 2400)).thaws == ()
    assert tick([f], machine(2000 + 2500)).thaws == ("f",)


def test_twice_frozen_needs_room_for_its_peak():
    f = frozen("over", held_freezes=2, peak_mb=5000, mem=3000)
    assert tick([f], machine(2000 + 1900)).thaws == ()
    assert tick([f], machine(2000 + 2000)).thaws == ("f",)


def test_a_waiter_freeze_thaws_by_decide():
    f = frozen("waiter", mem=500)
    assert tick([f], machine(2000 + 3000)).thaws == ("f",)
    assert tick([aged("big", 0.9, 9000), f], machine(2000 + 100)).thaws == ()


def test_memory_unknown_decides_by_bounds_only():   # review focus 4
    r = aged("r", 0.2, 3000, ceiling_mb=3000, at_ceiling=True, pressed_s=PRESSED_S + 2, scoped=True)
    a = tick([r, run("w", WAITING, 100)], machine(None, over_s=OVER_S + 2))
    assert a.orders == () and a.squeezed == () and "r" not in ceilings(a)
    assert [s[0] for s in a.starts] == ["w"]


def test_older_bookings_are_never_started():
    a = tick([run("old", WAITING, 100, rule=4)], machine(10000))
    assert a.starts == () and "older flotilla" in dict(a.why)["old"]


def test_every_run_that_does_not_move_has_a_why():
    runs = [aged("h", 0.5, 7000), run("w1", WAITING, 1500, kind="push"), run("w2", WAITING, 1500),
            frozen("over", reclaimed_mb=9000)]
    a = tick(runs, machine(2000 + 1600))
    moved = {s[0] for s in a.starts} | set(a.thaws)
    assert {"w1", "w2", "f"} - moved <= set(dict(a.why))


def test_the_victim_is_chosen_by_priority_not_weight():   # the person's ruling 6
    heavy_old = aged("big", 0.5, 6000, heavy=True, ceiling_mb=6256, scoped=True)
    light_new = aged("new", 0.1, 1200, ceiling_mb=1456, scoped=True)
    a = tick([heavy_old, light_new], machine(1500, over_s=OVER_S + 2))
    assert a.squeezed == ("new",)
    assert policy.priority(light_new, IMM) < policy.priority(heavy_old, IMM)


def test_a_held_run_is_frozen_at_once_when_the_swap_is_at_its_budget():   # found replaying 2026-11-07
    held = aged("h", 0.3, 512, ceiling_mb=512, at_ceiling=True, pressure=90.0, pressed_s=2, scoped=True)
    a = tick([held], machine(2000, swap_free=3500))   # 1500 MB left of the budget: under the reserve
    assert a.orders == (("h", 0, "pressed", "h"),)
    assert tick([held], machine(2000, swap_free=5000)).orders == ()   # 3000 MB to spare: it waits PRESSED_S



def test_a_scoped_run_without_a_ceiling_gets_one_at_its_use():   # final review C1: a ceiling is the daemon's own
    bare = aged("b", 0.3, 1000, scoped=True)
    assert ceilings(tick([bare], machine(10000))) == {"b": 1000 + policy.slack(1000)}
    assert tick([aged("u", 0.3, 1000)], machine(10000)).ceilings == ()                      # no scope: nothing to hold
    assert tick([run("i", RUNNING, 1000, ran_s=IMM, scoped=True)], machine(10000)).ceilings == ()   # immortal
