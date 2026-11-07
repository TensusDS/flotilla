"""A field record replayed through the lane daemon's `tick` (lane daemon design, section 8).

Time steps of TICK_S. Each run asks at its second; once started, its memory grows to its size over its first
FILL_S (the load fills its block) and holds it until its work is done. Under a ceiling its resident memory is
`min(demand, ceiling)` and the rest is in swap; a hot run held below its demand reads pressed and does almost no
work (the bench of 2026-11-07: 0.2 % of its rate). A freeze order is answered one step later: the run stops, and
what the order asks moves to swap. Runs, the machine and the timers are built as the scheduler builds them, with
its `History`. Nothing sleeps.
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass, field
from pathlib import Path

from flotilla.lane import policy, scheduler, tick
from flotilla.lane.policy import FROZEN, ORDERED, RUNNING, WAITING

FIXTURE = Path(__file__).parent / "fixtures" / "lane" / "daemon-replay-2026-11-07.json"
TICK_S = 2.0
FILL_S = 20.0
HORIZON_S = 1500.0
HELD_RATE = 0.002    # a hot run's work rate while held below its demand
TYPES = {"review": "review", "handover": "handover", "push": "push"}


def load(path: Path = FIXTURE) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@dataclass
class Sim:
    id: str
    ask_s: float
    kind: str
    estimate_mb: int
    mb: int
    seconds: float
    hot: bool
    order: int
    state: str = "future"          # future, WAITING, RUNNING, ORDERED, FROZEN, done
    started_at: float | None = None
    since_start: float = 0.0
    ran_s: float = 0.0
    done_work: float = 0.0
    ceiling: int | None = None
    squeezed: bool = False
    heavy: bool = False
    events: int = 0
    resident: int = 0
    swapped: int = 0
    frozen_at: float = 0.0
    cause: str = ""
    reclaim: int = 0
    held_freezes: int = 0
    finished_at: float | None = None

    def demand(self) -> int:
        if self.state in ("future", WAITING, "done"):
            return 0
        filled = min(1.0, (self.ran_s + 1e-9) / FILL_S) if self.ran_s < FILL_S else 1.0
        return max(1, int(self.mb * filled))


@dataclass
class Record:
    machine: dict
    lowest_available: int = 10 ** 9
    longest_under_floor_s: float = 0.0
    highest_swap_share: float = 0.0
    most_daemon_moves_in_a_step: int = 0
    immortal_touched: list = field(default_factory=list)
    pushes_over_budget: list = field(default_factory=list)   # a squeeze or a reclaim while the swap is at 80 %
    unfinished: list = field(default_factory=list)
    moves: list = field(default_factory=list)


def without_the_daemon(runs: list[policy.Run], machine: policy.Machine) -> tick.Actions:
    """The lane of 0.30 in the same minutes: starts by `decide` on free memory, no ceilings, no overload rule."""
    starts = []
    for r in sorted((r for r in runs if r.state == WAITING), key=lambda r: (-policy.priority(r, machine.immortal_s),
                                                                              r.order)):
        if policy.decide(runs, machine, r.id, may_freeze=False).start:
            starts.append((r.id, r.heavy, policy.priority(r, machine.immortal_s)))
    return tick.Actions(starts=tuple(starts))


def _swap_used(sims: list[Sim], m: dict) -> int:
    return m["swap_used_outside_mb"] + sum(s.swapped for s in sims)


def run(record_in: dict, decide=tick.tick) -> Record:
    m = record_in["machine"]
    sims = [Sim(r["id"], r["ask_s"], TYPES.get(r["kind"], "run"), r["estimate_mb"], r["mb"], r["seconds"], r["hot"],
                n) for n, r in enumerate(record_in["runs"], start=1)]
    history = scheduler.History()
    rec = Record(machine=m)
    under_since = None
    now = 0.0
    while now <= HORIZON_S:
        for s in sims:
            if s.state == "future" and s.ask_s <= now:
                s.state = WAITING
        # memory as the kernel holds it this step
        for s in sims:
            if s.state in (RUNNING, ORDERED):
                want = s.demand()
                s.resident = min(want, s.ceiling) if s.ceiling is not None else want
                s.swapped = want - s.resident
                if want > (s.ceiling if s.ceiling is not None else want):
                    s.events += 1
        available = m["total_mb"] - m["outside_mb"] - sum(s.resident for s in sims if s.state in
                                                          (RUNNING, ORDERED, FROZEN))
        swap_used = _swap_used(sims, m)
        rec.lowest_available = min(rec.lowest_available, available)
        rec.highest_swap_share = max(rec.highest_swap_share, swap_used / m["swap_total_mb"])
        if available < m["floor_mb"]:
            under_since = now if under_since is None else under_since
            rec.longest_under_floor_s = max(rec.longest_under_floor_s, now - under_since)
        else:
            under_since = None
        # the picture the scheduler would build
        give = available - m["floor_mb"] + sum(s.resident for s in sims if s.state in (RUNNING, ORDERED))
        reserved = sum(max(0, s.estimate_mb - s.resident) for s in sims
                       if s.state in (RUNNING, ORDERED) and s.since_start < 30)
        runs = []
        for s in sims:
            if s.state not in (WAITING, RUNNING, ORDERED, FROZEN):
                continue
            from flotilla.lane import groups
            reading = groups.Reading(s.resident, s.ceiling, s.events,
                                     90.0 if s.hot and s.swapped > 0 and s.state != FROZEN else 2.0)
            seen = history.run(now, s.id, reading if s.state != WAITING else None, s.ceiling)
            mem = s.estimate_mb if s.state == WAITING else s.resident
            s.heavy = policy.is_heavy(mem, give, was=s.heavy) if s.state != WAITING else s.heavy
            runs.append(policy.Run(
                s.id, s.state, max(1, mem), s.heavy, 1.0, kind=s.kind, waited_s=max(0.0, now - s.ask_s),
                ran_s=s.ran_s, since_start_s=s.since_start, order=s.order, ceiling_mb=s.ceiling,
                scoped=s.state != WAITING, at_ceiling=seen.get("at_ceiling", False),
                pressure=seen.get("pressure"), pressed_s=seen.get("pressed_s", 0.0), grew_mb=seen.get("grew_mb", 0),
                peak_mb=seen.get("peak_mb", 0), squeezed=s.squeezed, cause=s.cause,
                frozen_s=now - s.frozen_at if s.state == FROZEN else 0.0, held_freezes=s.held_freezes,
                reclaimed_mb=s.reclaim if s.state == FROZEN else 0))
        machine = policy.Machine(
            cpus=m["cpus"], give_mb=give, free_mb=available - m["floor_mb"] - reserved,
            swap_free_mb=m["swap_total_mb"] - swap_used, swap_total_mb=m["swap_total_mb"],
            swap_pending_mb=sum(s.reclaim for s in sims if s.state == ORDERED), immortal_s=m["immortal_s"],
            available_mb=available, floor_mb=m["floor_mb"], over_s=history.machine(now, available, m["floor_mb"]))
        actions = decide(runs, machine)
        # what the wrapper does with an order given a step ago
        for s in sims:
            if s.state == ORDERED:
                s.reclaim = min(s.reclaim, s.resident)
                s.resident -= s.reclaim
                s.swapped += s.reclaim
                s.state, s.frozen_at = FROZEN, now
                if s.cause in (policy.OVER, policy.PRESSED_CAUSE):
                    s.held_freezes += 1
        by_id = {s.id: s for s in sims}
        immortal = {r.id for r in runs if policy.immortal(r, m["immortal_s"])}
        if swap_used >= policy.SWAP_USED_CAP * m["swap_total_mb"]:
            rec.pushes_over_budget += [(now, "squeeze", i) for i in actions.squeezed] + \
                [(now, "reclaim", o[0]) for o in actions.orders if o[1] > 0]
        daemon_moves = [i for i in actions.squeezed] + [o[0] for o in actions.orders if o[2] != policy.WAITER]
        rec.most_daemon_moves_in_a_step = max(rec.most_daemon_moves_in_a_step, len(daemon_moves))
        rec.immortal_touched += [(now, i) for i in [*actions.squeezed, *(o[0] for o in actions.orders)]
                                 if i in immortal]
        for i, _heavy, _p in actions.starts:
            s = by_id[i]
            s.state, s.started_at, s.since_start = RUNNING, now, 0.0
        for i, mb in actions.ceilings:
            by_id[i].ceiling = mb
        for i in actions.squeezed:
            by_id[i].squeezed = True
        for i in actions.restored:
            by_id[i].squeezed = False
        for i, reclaim, cause, _waiter in actions.orders:
            s = by_id[i]
            if s.state == RUNNING:
                s.state, s.reclaim, s.cause = ORDERED, reclaim, cause
        for i in actions.thaws:
            s = by_id[i]
            if s.state == FROZEN:
                s.resident += s.reclaim
                s.swapped = max(0, s.swapped - s.reclaim)
                s.state, s.reclaim, s.since_start = RUNNING, 0, 0.0
        rec.moves += [(now, "squeeze", i) for i in actions.squeezed] + \
            [(now, f"freeze ({o[2]})", o[0]) for o in actions.orders] + [(now, "thaw", i) for i in actions.thaws]
        # work done this step
        for s in sims:
            if s.state in (RUNNING, ORDERED):
                s.ran_s += TICK_S
                s.since_start += TICK_S
                rate = HELD_RATE if (s.hot and s.swapped > 0) else 1.0
                s.done_work += TICK_S * rate
                if s.done_work >= s.seconds:
                    s.state, s.finished_at, s.resident, s.swapped = "done", now, 0, 0
        now += TICK_S
    rec.unfinished = [s.id for s in sims if s.state != "done"]
    return rec
