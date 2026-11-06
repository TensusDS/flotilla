"""The brainstorm's model queues (2026-11-06), replayed through the real policy (`flotilla.lane.policy.decide`).

The model decided by its own simplified rules; this replay keeps its queues and its machine - the job generator and
the CPU water-filling are copied verbatim so the seeds reproduce the model's queues - and lets the policy decide
each step instead. Time is simulated in steps of 15 seconds; nothing runs and nothing sleeps.

A freeze acts as the real one does: the order is answered one step later (the victim runs on meanwhile, as its
wrapper wakes and reclaims), then its command stops, and the memory the order asks for moves to swap while the rest
stays resident; the victim comes back as a waiter needing that moved memory again.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from flotilla.lane import policy

DT = 0.25   # minutes per step
CORES, RAM_MB, SWAP_MB = 8, 9000, 8000
KINDS = ("push", "handover", "review", "run")   # the model's types, in its order
LIMIT = 2000.0


@dataclass
class Job:
    id: int
    heavy: bool
    work: float      # core-minutes left
    par: float       # cores it can use
    mem: float       # GB
    type: int
    arrival: float
    state: str = "future"   # future, waiting, running, frozen, done
    run_time: float = 0.0
    since_start: float = 0.0
    first_start: float | None = None
    done_at: float | None = None
    freezes: int = 0
    total: float = 0.0
    frozen_time: float = 0.0
    frozen_by: list = field(default_factory=list)
    swapped: int = 0   # MB of it in swap while frozen (the replay's own field)
    frozen_for: int | None = None   # the waiter whose order froze it, until that waiter starts (likewise)
    ordered_mb: int = 0   # what an order not yet answered asks it to move to swap


MIXES = {   # (heavy GB range, light GB range)
    "roomy": ((1.5, 4.0), (0.1, 0.8)),
    "tight": ((2.0, 6.0), (0.2, 1.5)),
}


def make_jobs(seed: int, spread: float, n_heavy: int = 5, n_light: int = 25, scale: float = 1.0,
              mix: str = "roomy") -> list[Job]:
    rng = random.Random(seed)
    heavy_mem, light_mem = MIXES[mix]
    jobs = []
    for i in range(n_heavy + n_light):
        heavy = i < n_heavy
        if heavy:
            work, par, mem = rng.uniform(60, 360), rng.choice([4, 6, 8]), rng.uniform(*heavy_mem)
        else:
            work, par, mem = rng.uniform(0.2, 6.0), rng.choice([1, 1, 1, 2]), rng.uniform(*light_mem)
        par = min(par, max(1, round(par * scale)))
        arrival = rng.uniform(0, spread) if spread else 0.0
        jobs.append(Job(i, heavy, work, par, mem * min(1.0, scale ** 0.5), rng.randrange(4), arrival, total=work))
    rng.shuffle(jobs)
    return jobs


def shares(running: list[Job], cores: int) -> dict[int, float]:
    out, left = {}, float(cores)
    ordered = sorted(running, key=lambda j: j.par)
    for n, job in enumerate(ordered):
        give = min(job.par, left / (len(ordered) - n))
        out[job.id] = give
        left -= give
    return out


def _mb(job: Job) -> int:
    return round(job.mem * 1000)


STATES = {"waiting": policy.WAITING, "running": policy.RUNNING, "ordered": policy.ORDERED,
          "frozen": policy.FROZEN}


def _view(jobs: list[Job], now: float, order: dict[int, int]) -> tuple[list[policy.Run], policy.Machine]:
    runs = []
    holding = {j.frozen_for for j in jobs if j.state in ("ordered", "frozen") and j.frozen_for is not None}
    for j in jobs:
        if j.state not in STATES:
            continue
        runs.append(policy.Run(
            str(j.id), STATES[j.state], j.swapped if j.state == "frozen" else _mb(j), j.heavy, j.par,
            kind=KINDS[j.type], waited_s=(now - j.arrival) * 60, ran_s=j.run_time * 60,
            since_start_s=j.since_start * 60, order=order[j.id],
            holds=j.id in holding and j.state != "running"))
    resident = sum(_mb(j) for j in jobs if j.state in ("running", "ordered")) + \
        sum(_mb(j) - j.swapped for j in jobs if j.state == "frozen")
    swapped = sum(j.swapped for j in jobs if j.state == "frozen")
    pending = sum(j.ordered_mb for j in jobs if j.state == "ordered")
    machine = policy.Machine(cpus=CORES, give_mb=RAM_MB, free_mb=RAM_MB - resident, swap_free_mb=SWAP_MB - swapped,
                             swap_total_mb=SWAP_MB, swap_pending_mb=pending, cpu_factor=1.5, light_reserve=0.2,
                             immortal_s=900.0,
                             max_runs=10, freeze=True)
    return runs, machine


def replay(seed: int, spread: float, mix: str = "tight") -> dict:
    jobs = make_jobs(seed, spread, mix=mix)
    by_id = {j.id: j for j in jobs}
    order = {j.id: n for n, j in enumerate(sorted(jobs, key=lambda j: (j.arrival, j.id)))}
    heavy_by_heavy = immortal_frozen = ordered_again = 0
    now = 0.0
    while now < LIMIT:
        for j in jobs:
            if j.state == "future" and j.arrival <= now:
                j.state = "waiting"
        if all(j.state == "done" for j in jobs):
            break
        for j in jobs:   # last step's orders are answered now
            if j.state == "ordered":
                j.state, j.swapped, j.ordered_mb = "frozen", min(_mb(j), j.ordered_mb), 0
        runs, machine = _view(jobs, now, order)
        line = sorted((r for r in runs if r.state in (policy.WAITING, policy.FROZEN)),
                      key=lambda r: (-policy.priority(r, machine.immortal_s), r.order))
        for asking in line:
            runs, machine = _view(jobs, now, order)
            decision = policy.decide(runs, machine, asking.id)
            job = by_id[int(asking.id)]
            if decision.start:
                job.state, job.since_start, job.swapped, job.frozen_for = "running", 0.0, 0, None
                job.first_start = now if job.first_start is None else job.first_start
                for other in jobs:
                    if other.frozen_for == job.id:
                        other.frozen_for = None   # it started: what it held for itself is in use now
                continue
            if decision.freeze and any(j.frozen_for == job.id for j in jobs if j.state in ("ordered", "frozen")):
                ordered_again += 1   # it ordered while its own orders were still out
            for victim_id, reclaim_mb in decision.freeze:
                victim = by_id[int(victim_id)]
                heavy_by_heavy += victim.heavy and job.heavy
                immortal_frozen += victim.run_time * 60 >= machine.immortal_s
                victim.state, victim.freezes = "ordered", victim.freezes + 1
                victim.ordered_mb, victim.frozen_for = reclaim_mb, job.id
                victim.frozen_by.append("heavy" if job.heavy else "light")
        running = [j for j in jobs if j.state in ("running", "ordered")]
        for j in jobs:
            if j.state == "frozen":
                j.frozen_time += DT
        got = shares(running, CORES)
        for j in running:
            j.work -= got[j.id] * DT
            j.run_time += DT
            j.since_start += DT
            if j.work <= 1e-9:
                j.state, j.done_at = "done", now + DT
        now += DT
    done = [j for j in jobs if j.done_at is not None]
    unfinished = len(jobs) - len(done)
    light = [j for j in jobs if not j.heavy]
    waits = sorted((j.first_start if j.first_start is not None else LIMIT) - j.arrival for j in light)
    return {
        "makespan": max((j.done_at for j in done), default=LIMIT) if not unfinished else LIMIT,
        "light_wait_p95": waits[int(0.95 * (len(waits) - 1))],
        "freezes": sum(j.freezes for j in jobs),
        "max_freezes_one": max(j.freezes for j in jobs),
        "heavy_by_heavy": heavy_by_heavy,
        "immortal_frozen": immortal_frozen,
        "ordered_again": ordered_again,
        "unfinished": unfinished,
    }
