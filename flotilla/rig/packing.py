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
        dated.append((ended, run, Sample(seconds, _positive(run.cores, float),
                                         None if killed else _positive(run.peak_mb, int),
                                         _positive(run.gpu_mb, int), _positive(run.gpu_shared_mb, int),
                                         run.verdict)))
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
                gpu if gpu is not None else PRIOR.gpu_mb, f"{len(samples)} run(s), {step.split(':', 1)[0]} match",
                gpu is None)
