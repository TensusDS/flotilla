"""The minutes of 2026-11-07 01:51-02:02, replayed through the daemon's tick (lane daemon design, section 8).

The field test's lane admitted five heavy runs on light estimates, froze nothing, and took the machine to 0.5 GB
free with 10.4 GB of swap used. Replayed through `tick`, the machine stays above its floor."""

import time

import lane_daemon_replay as replay
from flotilla.lane import tick


def test_the_daemon_holds_the_machine_of_2026_11_07():
    record = replay.run(replay.load())
    floor = record.machine["floor_mb"]
    assert not record.immortal_touched, record.immortal_touched
    assert record.most_daemon_moves_in_a_step <= 1
    assert record.longest_under_floor_s <= tick.OVER_S + 2 * replay.TICK_S, (record.longest_under_floor_s,
                                                                              record.lowest_available)
    assert record.pushes_over_budget == [], record.pushes_over_budget   # the swap budget, on every daemon move
    # what the held runs push themselves between a look and their freeze is theirs, not a move of the daemon's: it
    # stays well under systemd-oomd's line (81.5 % when this was written; 88 % before the held-swap reserve)
    assert record.highest_swap_share < 0.85, record.highest_swap_share
    assert record.unfinished == [], record.unfinished
    assert record.lowest_available >= floor - 256   # the field minutes reached 0.5 GB free


def test_the_rules_of_0_30_lose_the_same_minutes():
    """The replay's own guard, seen red: with no ceilings and no overload rule - the lane of 0.30 - the same minutes
    take the machine far below its floor."""
    record = replay.run(replay.load(), decide=replay.without_the_daemon)
    assert record.longest_under_floor_s > tick.OVER_S + 2 * replay.TICK_S


def test_the_replay_is_fast():
    began = time.monotonic()
    replay.run(replay.load())
    assert time.monotonic() - began < 10
