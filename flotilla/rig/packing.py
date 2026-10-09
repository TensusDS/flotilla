"""Packing runs on a rig machine by estimates (rig design, section 7, 0.11.0).

What a run will take comes from the runs measured on rig machines before it, by the lane's signature ladder: the
most exact step with a measured run answers (never the project-wide step); duration is the median of green runs (a
run stopped at the ceiling counts as at least its time), and a step with only red runs takes its duration a step
down; cores, memory and GPU memory are maxima. A GPU figure measured beside another run may raise an estimate but
never lower it. A command never measured takes a prior. One sample is enough: rig runs are few and paid.
"""

from __future__ import annotations

import datetime as dt
import math
import statistics
from dataclasses import dataclass

from flotilla.rig import journal as j

#: What a machine keeps free: memory and GPU memory no estimate may use (and the live readings' margins).
MARGIN_MB = 2048
GPU_MARGIN_MB = 1024
KEEP, DAYS = 10, 30
MEASURED = ("green", "red", "ceiling")   # a run that ended for another reason measured its end, not the command
KILLED = 137                              # usually the OOM killer: the run never reached its need
SKEW = dt.timedelta(minutes=5)


@dataclass(frozen=True)
class Sample:
    seconds: float
    cores: float | None
    ram_mb: int | None
    gpu_mb: int | None
    gpu_shared_mb: int | None
    verdict: str


@dataclass(frozen=True)
class Need:
    seconds: float | None
    cores: float
    ram_mb: int
    gpu_mb: int
    source: str
    gpu_prior: bool


PRIOR = Need(None, 4.0, 2048, 2048, "prior", True)


def _positive(value, kind):
    """A measurement, or None: zero (a command shorter than the sampler's period) is no measurement either."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        return None
    return kind(value)


def history(runs: dict, *, now: dt.datetime) -> dict[str, list[Sample]]:
    since, latest = now - dt.timedelta(days=DAYS), now + SKEW
    dated = []
    for run in runs.values():
        if run.state != j.DONE or run.verdict not in MEASURED:
            continue
        try:
            ended = dt.datetime.fromisoformat(run.ended)
        except (TypeError, ValueError):
            continue
        seconds = _positive(run.seconds, float)
        if ended.tzinfo is None or not since <= ended <= latest or seconds is None:
            continue
        killed = run.exit == KILLED
        sampled = _positive(run.peak_mb, int) is not None   # the sampler saw the run: its 0 GPU memory is a measure
        gpu = run.gpu_mb if sampled and isinstance(run.gpu_mb, int) and not isinstance(run.gpu_mb, bool) \
            and run.gpu_mb >= 0 else None
        dated.append((ended, run, Sample(seconds, _positive(run.cores, float),
                                         None if killed else _positive(run.peak_mb, int),
                                         gpu, _positive(run.gpu_shared_mb, int), run.verdict)))
    found: dict[str, list[Sample]] = {}
    for _, run, sample in sorted(dated, key=lambda item: item[0]):
        for step in run.ladder:
            if isinstance(step, str) and not step.startswith("project:"):
                found.setdefault(step, []).append(sample)
    return {step: samples[-KEEP:] for step, samples in found.items()}


def _duration(samples):
    usable = [s.seconds for s in samples if s.verdict in ("green", "ceiling")]
    return float(statistics.median(usable)) if usable else None


def _max(values):
    usable = [v for v in values if v is not None]
    return max(usable) if usable else None


def _kind(step: str) -> str:
    """The ladder step's kind for display: only the kinds the lane issues - a ladder is journal text."""
    kind = step.split(":", 1)[0]
    return kind if kind in ("exact", "norm", "prog") else "signature"


def estimate(ladder, hist: dict[str, list[Sample]]) -> Need:
    steps = [(step, hist[step]) for step in ladder
             if isinstance(step, str) and not step.startswith("project:") and hist.get(step)]
    if not steps:
        return PRIOR
    step, samples = steps[0]
    seconds = _duration(samples)
    for _, more in steps[1:]:
        if seconds is not None:
            break
        seconds = _duration(more)
    alone, shared = _max(s.gpu_mb for s in samples), _max(s.gpu_shared_mb for s in samples)
    gpu = alone   # measured alone is the base; a shared figure only raises it, or the prior
    if shared is not None and shared > (alone if alone is not None else PRIOR.gpu_mb):
        gpu = shared
    cores, ram = _max(s.cores for s in samples), _max(s.ram_mb for s in samples)
    return Need(seconds, cores if cores is not None else PRIOR.cores, ram if ram is not None else PRIOR.ram_mb,
                gpu if gpu is not None else PRIOR.gpu_mb, f"{len(samples)} run(s), {_kind(step)} match",
                gpu is None)


#: A run waiting this long is senior: until it fits, no later run takes room it needs.
SENIOR = dt.timedelta(minutes=10)
CORES, MEMORY, GPU = "cores", "MB of memory", "MB of GPU memory"


@dataclass(frozen=True)
class Pick:
    run_id: str | None
    why: dict


def shape_known(machine) -> bool:
    return bool(machine.cpus) and bool(machine.ram_mb)


def _number(run) -> int:
    return int(run.id[1:])


def _age(run, now) -> dt.timedelta:
    try:
        return now - dt.datetime.fromisoformat(run.since)
    except (TypeError, ValueError):
        return dt.timedelta(0)


def choose(waiting, running, machine, needs, now) -> Pick:
    """Which of `waiting` (the live waiting runs of the machine's session) starts on `machine` now, and why each
    other one waits. Memory and GPU memory bind from the first run; cores only past two (a machine always takes two
    runs, as it did before estimates: CPU over-commit slows runs, memory over-commit kills them)."""
    room = {CORES: float(machine.cpus), MEMORY: machine.ram_mb - MARGIN_MB}
    if machine.gpu_total_mb is not None and machine.gpu_total_mb > 0:
        room[GPU] = machine.gpu_total_mb - GPU_MARGIN_MB

    def amounts(item):
        n = needs[item.id]
        found = {CORES: min(n.cores, room[CORES]), MEMORY: n.ram_mb}
        if GPU in room:
            found[GPU] = n.gpu_mb
        return found

    used = {key: sum(amounts(r)[key] for r in running) for key in room}
    binding = [key for key in room if key != CORES or len(running) >= 2]

    def short(item, held_for=None) -> str:
        """The first resource `item` lacks, with a senior's share held back where `item` needs any, or ""."""
        mine = amounts(item)
        for key in binding:
            held = amounts(held_for)[key] if held_for is not None and mine[key] > 0 else 0
            left = room[key] - used[key] - held
            if mine[key] > left:
                return f"needs {mine[key]:g} {key}, {max(left, 0):g} left"
        return ""

    def bigger_than_machine(item) -> bool:
        return any(amounts(item)[key] > room[key] for key in room if key != CORES)

    def fits(item) -> bool:
        return not running or not short(item)

    order = sorted(waiting, key=_number)
    seniors = [r for r in order if _age(r, now) >= SENIOR]
    senior = seniors[0] if seniors else None
    if senior is not None and fits(senior):
        return Pick(senior.id, {r.id: f"senior {senior.id} goes first" for r in order if r is not senior})
    why: dict[str, str] = {}
    candidates = []
    for item in order:
        if item is senior:
            why[item.id] = f"senior; {short(item) or 'waits for the machine to empty'}"
        elif senior is not None and bigger_than_machine(senior):
            why[item.id] = f"held for senior {senior.id}, which needs the whole machine"
        elif senior is not None and short(item, senior):
            why[item.id] = f"held for senior {senior.id}: {short(item, senior)}"
        elif not fits(item):
            why[item.id] = short(item)
        else:
            candidates.append(item)
    if not candidates:
        return Pick(None, why)
    chosen = max(candidates, key=lambda r: (math.inf if needs[r.id].seconds is None else needs[r.id].seconds,
                                            -_number(r)))
    for item in candidates:
        if item is not chosen:
            why[item.id] = f"{chosen.id} goes first ({'not measured yet' if needs[chosen.id].seconds is None
                                                       else 'longer'})"
    return Pick(chosen.id, why)
