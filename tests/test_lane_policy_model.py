"""The real policy on the brainstorm model's thirty-run queues (lane shared machine design, section 10): the same
twenty queues land in the model's band, and the anti-goals hold on every one of them."""

import functools
import statistics

from lane_replay import replay

CASES = [(seed, spread) for seed in range(10) for spread in (0, 60)]


@functools.lru_cache(maxsize=None)
def _replayed(seed, spread):   # each queue once per session: both tests read the same replay
    return replay(seed, spread)


def test_the_policy_lands_in_the_models_band():
    rows = [_replayed(seed, spread) for seed, spread in CASES]
    # the model on these 20 queues (2026-11-06): makespan mean 182.8 min, light wait p95 mean 5.48 min,
    # 0.45 freezes a queue, never twice the same run
    assert all(r["unfinished"] == 0 for r in rows)
    assert 165 <= statistics.mean(r["makespan"] for r in rows) <= 200
    assert statistics.mean(r["light_wait_p95"] for r in rows) <= 6.6


def test_the_anti_goals_hold_on_every_queue():
    for seed, spread in CASES:
        r = _replayed(seed, spread)
        assert r["heavy_by_heavy"] == 0 and r["immortal_frozen"] == 0 and r["max_freezes_one"] <= 2, (seed, spread)
