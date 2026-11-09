"""What a run will take, from the runs before it (lane admission design, section 2). The most exact signature with
three measurements in the last 30 days answers; duration is the median of green runs (a red or killed run that ended
early says nothing about a full one; a run stopped at the ceiling counts as at least its time), cores the maximum of
runs on an unsaturated machine, peak the maximum. A command never seen takes its project's 90th percentile; a
project never seen, a fixed prior. Only the last ten measurements of a step count: suites grow."""

from __future__ import annotations

import datetime as dt
import os
import re
import statistics
from dataclasses import dataclass

from flotilla.lane import book
from flotilla.lane.signature import tier_signature

KEEP, ENOUGH, SATURATED = 10, 3, 0.85
#: A run stopped before its end measured how long it held the lane and nothing about the command: it is no sample,
#: or three Ctrl-Cs would push the measured runs out of the window (review of 0.7.16).
UNMEASURED = frozenset({"killed", "refused"})
SKEW = dt.timedelta(minutes=5)   # a clock this far ahead is not a run that happened


@dataclass(frozen=True)
class Sample:
    seconds: float | None
    peak_mb: int | None
    cores: float | None
    busy: float | None
    verdict: str
    at: str
    peak_cores: float | None = None


@dataclass(frozen=True)
class Estimate:
    seconds: float | None
    cores: float | None
    peak_mb: int | None
    source: str
    peak_cores: float | None = None   # the busiest stretch any sample saw: a heavy run's parallelism (section 4.4)
    #: What a run usually takes, the median peak: its ceiling starts there and grows, while `peak_mb`, the largest,
    #: admits it. One command often runs light and heavy (a test tier on a small row and a big one); a ceiling at the
    #: largest promised every light run the heavy one's memory (the field test of 2026-11-08, the person's option B).
    typical_mb: int | None = None


PRIOR = Estimate(None, 4.0, 2048, "fixed prior: 4 cores, 2 GB")


def _when(text: str) -> dt.datetime | None:
    try:
        return dt.datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None


def history(bookings: dict, *, now: dt.datetime, days: int = 30) -> dict[str, list[Sample]]:
    since, latest = now - dt.timedelta(days=days), now + SKEW
    found: dict[str, list[Sample]] = {}
    done = [item for item in bookings.values() if item.state == book.RELEASED and not item.cut
            and (item.seconds is not None or item.ran) and _when(item.ended) is not None
            and since <= _when(item.ended) <= latest]
    for item in sorted(done, key=lambda item: _when(item.ended)):
        if item.seconds is not None and item.verdict not in UNMEASURED:   # a red receipt's tiers still teach
            sample = Sample(item.seconds, item.peak_mb, item.cores, item.busy, item.verdict, item.ended,
                            item.peak_cores)
            for step in item.ladder:
                found.setdefault(step, []).append(sample)
        for tier in item.ran or []:
            if isinstance(tier, dict) and tier.get("name") and tier.get("seconds") is not None:
                key = f"setup:{item.project}" if tier.get("kind") == "setup" else \
                    tier_signature(tier["name"], project=item.project)   # a tier may be named "setup" too
                found.setdefault(key, []).append(Sample(
                    tier.get("seconds"), tier.get("peak_mb"), tier.get("cores"), tier.get("busy"),
                    {"green": "green", "timed-out": "ceiling"}.get(tier.get("status"), str(tier.get("status") or "")),
                    item.ended, tier.get("peak_cores")))
    return {step: samples[-KEEP:] for step, samples in found.items()}


def _duration(samples: list[Sample]) -> float | None:
    usable = [s.seconds for s in samples if s.seconds is not None and s.verdict in ("green", "ceiling")]
    return float(statistics.median(usable)) if usable else None


def _room(sample: Sample, cpus: int) -> bool:
    """Whether the run could have used more: the machine was not full, or full of the run itself. A 7-core receipt
    on 8 cores makes the machine 7/8 busy on its own; that is the run's demand, not a crowd (review of stage 1)."""
    if sample.busy is None or sample.busy < SATURATED:
        return True
    return sample.cores is not None and sample.busy - sample.cores / cpus < 1 - SATURATED


def _cores(samples: list[Sample], cpus: int) -> float | None:
    usable = [s.cores for s in samples if s.cores is not None and _room(s, cpus)]
    return max(usable) if usable else None


def _peak(samples: list[Sample]) -> int | None:
    usable = [s.peak_mb for s in samples if s.peak_mb is not None]
    return max(usable) if usable else None


def _typical(samples: list[Sample]) -> int | None:
    usable = [s.peak_mb for s in samples if s.peak_mb is not None]
    return round(statistics.median(usable)) if usable else None


def _peak_cores(samples: list[Sample]) -> float | None:
    usable = [s.peak_cores for s in samples if s.peak_cores is not None]
    return max(usable) if usable else None


def _p90(values: list) -> float | int | None:
    values = sorted(v for v in values if v is not None)
    if not values:
        return None
    return values[min(len(values) - 1, int(round(0.9 * (len(values) - 1))))]


def estimate(ladder: list[str], hist: dict[str, list[Sample]], *, project: str, cpus: int | None = None) -> Estimate:
    cpus = cpus or os.cpu_count() or 1
    steps = [(step, hist.get(step, [])) for step in ladder if not step.startswith("project:")]
    steps = [(step, samples) for step, samples in steps if len(samples) >= ENOUGH]
    if steps:
        step, samples = steps[0]
        source = f"{len(samples)} runs ({step.split(':', 1)[0]} match)"
        seconds = _duration(samples)
        if seconds is None:   # three red runs of the exact command must not hide the green ones a step down
            for coarser, more in steps[1:]:
                seconds = _duration(more)
                if seconds is not None:
                    source += f"; duration from {len(more)} runs ({coarser.split(':', 1)[0]} match)"
                    break
        return Estimate(seconds, _cores(samples, cpus), _peak(samples), source, _peak_cores(samples),
                        _typical(samples))
    samples = hist.get(f"project:{project}", [])
    if len(samples) >= ENOUGH:
        return Estimate(None, _p90([s.cores for s in samples]), _p90([s.peak_mb for s in samples]),
                        f"project prior: 90th percentile of {len(samples)} runs",
                        _p90([s.peak_cores for s in samples]), _typical(samples))
    return PRIOR


def receipt_estimate(will_run: list[str], hist: dict[str, list[Sample]], *, project: str,
                     cpus: int | None = None) -> Estimate:
    parts = [estimate([tier_signature(name, project=project)], hist, project=project, cpus=cpus)
             for name in will_run]
    if not parts:
        return Estimate(0.0, None, None, "nothing to run")
    seconds = None if any(p.seconds is None for p in parts) else sum(p.seconds for p in parts)
    cores = max((p.cores for p in parts if p.cores is not None), default=None)
    peak = max((p.peak_mb for p in parts if p.peak_mb is not None), default=None)
    sources = sorted({p.source for p in parts})
    busiest = max((p.peak_cores for p in parts if p.peak_cores is not None), default=None)
    typical = max((p.typical_mb for p in parts if p.typical_mb is not None), default=None)
    return Estimate(seconds, cores, peak, f"{len(parts)} tier(s): " + "; ".join(sources), busiest, typical)


#: A run is light when its own measurements say it is short and small (lane light runs design, section 2): it then
#: takes what the heavy runs leave instead of a place of its own. A prior never makes a run light.
LIGHT_SECONDS, LIGHT_CORES = 60.0, 2.0


def for_booking(item, hist: dict[str, list[Sample]], *, cpus: int | None = None) -> Estimate:
    """A booking's estimate: a receipt's by the tiers it will run, any other run's by its signature ladder."""
    if item.will_run:
        return receipt_estimate(list(item.will_run), hist, project=item.project, cpus=cpus)
    return estimate(list(item.ladder), hist, project=item.project, cpus=cpus)


#: The ladder steps that are this command's own history: never `prog:` (every run of the same program) nor the
#: project's prior, so a full suite with a new flag is not light because single-file runs of its program are (the
#: final review of 0.28.0). A receipt's parts answer by their tier.
OWN_STEPS = ("exact", "norm", "tier")
_ANSWERED = re.compile(r"^\d+ runs \((\w+) match\)$")


def _own(found: Estimate) -> bool:
    """Measured whole by this command's own history: duration, cores and peak, all from an own step."""
    answered = _ANSWERED.match(found.source)
    return (answered is not None and answered.group(1) in OWN_STEPS and found.seconds is not None
            and found.cores is not None and found.peak_mb is not None)


def light(found: Estimate) -> bool:
    return _own(found) and found.seconds <= LIGHT_SECONDS and found.cores <= LIGHT_CORES


def light_booking(item, hist: dict[str, list[Sample]], *, cpus: int | None = None) -> bool:
    """Whether a booking is light: a run by its own estimate; a receipt only when every tier that will run is measured
    whole by its own history, and together they stay within the bounds (lane light runs design, section 2)."""
    if not item.will_run:
        return light(for_booking(item, hist, cpus=cpus))
    parts = [estimate([tier_signature(name, project=item.project)], hist, project=item.project, cpus=cpus)
             for name in item.will_run]
    return (bool(parts) and all(_own(part) for part in parts) and sum(p.seconds for p in parts) <= LIGHT_SECONDS
            and max(p.cores for p in parts) <= LIGHT_CORES)
