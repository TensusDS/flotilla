"""How big a fleet this project wants on this machine now: the rule of fleet sizing (design, section 3).

    authors = max(1, min(backlog, test runs, memory, disk, max seats))

Each cap is the largest number of authors it allows, with the rest of the fleet they bring (reviewers grow with the
authors, plus one sender and a judge when the project has one): a cap on seats or memory is a cap on the whole
fleet, not on authors alone. A signal that could not be read is left out and named; the cap that set the number is
named too. The orchestrator is the person's own session - already running, so it costs nothing new and counts in
no cap (ruling of Task 5).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

SEARCH = 200   # no cap allows more authors than this; past it the sizing says "at least"
DISK_SHARE = 0.5


@dataclass(frozen=True)
class Machine:
    free_mb: int | None
    floor_mb: int
    seat_mb: int
    live_seats: int | None        # seats running now: already inside free_mb, so reported, never subtracted again
    run_mb: int | None            # the heaviest tier's measured peak
    run_seconds: float | None     # one full run, all handover tiers
    lane_capacity: int
    free_disk_mb: int | None
    tree_mb: int | None


@dataclass
class Recommendation:
    counts: dict[str, int]
    authors: int
    caps: dict[str, int | None]
    binding: str
    lines: list[str] = field(default_factory=list)
    raise_nothing: str = ""


def _reviewers(authors: int, pace) -> int:
    return max(1, math.ceil(round(authors * pace.handovers_per_author_hour * pace.review_hours, 6)))


def _largest(ok) -> int:
    """The largest a in 0..SEARCH with ok(a); the caps only shrink as a grows, so the first failure ends it."""
    best = 0
    for a in range(1, SEARCH + 1):
        if not ok(a):
            break
        best = a
    return best


def _judge(profile) -> int:
    return 1 if (profile.get("judge") or {}).get("required") or profile.get("deploy") else 0


def _split(authors: int, backlog) -> tuple[int, int]:
    total = backlog.main + backlog.minor
    if total == 0:
        return authors, 0
    minor = round(authors * backlog.minor / total)   # authors never exceed a known backlog
    main = authors - minor
    if backlog.main > 0 and main < 1 and authors >= 1:
        main, minor = 1, authors - 1
    return main, minor


def recommend(machine: Machine, backlog, pace, profile) -> Recommendation:
    sizing = ((profile or {}).get("fleet") or {}).get("sizing") or {}
    judge = _judge(profile or {})

    def seats(a: int) -> int:
        return a + _reviewers(a, pace) + 1 + judge

    caps: dict[str, int] = {}
    detail: dict[str, str] = {}

    known = [s for s in backlog.sources if s.main is not None]
    if known:
        caps["backlog"] = backlog.main + backlog.minor
        detail["backlog"] = (f"backlog {caps['backlog']}: "
                             + ", ".join(f"{s.name} {s.main + s.minor}" + (f" ({s.note})" if s.note else "")
                                         for s in known))
    for s in backlog.sources:
        if s.main is None:
            detail.setdefault("backlog-unknown", "")
            detail["backlog-unknown"] += f"{s.name}: unknown ({s.note}), not counted\n"

    room = None if machine.free_mb is None else machine.free_mb - machine.floor_mb
    if room is not None:
        run = machine.run_mb or 0
        caps["memory"] = _largest(lambda a: seats(a) * machine.seat_mb + run <= room)
        detail["memory"] = (f"memory {caps['memory']}: {machine.free_mb} MB free, {machine.floor_mb} MB kept free, "
                            f"{machine.seat_mb} MB a seat"
                            + (f", {machine.run_mb} MB a test run" if machine.run_mb else
                               ", a test run's memory never measured (not reserved)"))
    else:
        detail["memory"] = "memory: unknown (free memory could not be read), not counted"

    if machine.run_seconds:
        def runs_fit(a: int) -> int:
            if room is None or not machine.run_mb:
                return machine.lane_capacity
            return max(0, min(machine.lane_capacity, (room - seats(a) * machine.seat_mb) // machine.run_mb))

        def verified(a: int) -> float:
            return runs_fit(a) * 3600 / machine.run_seconds

        caps["test runs"] = _largest(lambda a: a * pace.handovers_per_author_hour <= verified(a))
        detail["test runs"] = (f"test runs {caps['test runs']}: one full run takes {machine.run_seconds / 60:.1f} min"
                               + (f" and {machine.run_mb} MB" if machine.run_mb else "")
                               + f", the lane takes {machine.lane_capacity} at a time; authors hand over "
                               f"{pace.handovers_per_author_hour}/h each")
    else:
        detail["test runs"] = "test runs: unknown (no tier measured green on this machine), not counted"

    if machine.free_disk_mb is not None and machine.tree_mb:
        allowed = DISK_SHARE * machine.free_disk_mb
        caps["disk"] = _largest(lambda a: seats(a) * machine.tree_mb <= allowed)
        detail["disk"] = (f"disk {caps['disk']}: {machine.free_disk_mb} MB free, half of it for trees of "
                          f"{machine.tree_mb} MB each")
    else:
        detail["disk"] = "disk: unknown (free disk or a tree's size could not be read), not counted"

    limit = sizing.get("max_seats")
    if isinstance(limit, int) and not isinstance(limit, bool) and limit >= 0:
        caps["max seats"] = _largest(lambda a: seats(a) <= limit)
        detail["max seats"] = f"max seats {caps['max seats']}: [fleet.sizing] max_seats = {limit}"

    if caps:
        binding = min(caps, key=lambda name: caps[name])
        authors = max(1, caps[binding])
    else:
        binding, authors = "nothing known", 1
    reviewers = _reviewers(authors, pace)
    main, minor = _split(authors, backlog)
    counts = {"orchestrator": 1, "sender": 1, "reviewer": reviewers, "judge": judge, "main": main, "minor": minor}

    raise_nothing = ""
    if room is not None and room < machine.seat_mb:
        raise_nothing = (f"raise nothing now: {machine.free_mb} MB free, {machine.floor_mb} MB kept free and a seat "
                         f"takes {machine.seat_mb} MB - no room for one more seat")
    elif room is not None and seats(1) * machine.seat_mb + (machine.run_mb or 0) > room:
        raise_nothing = (f"raise nothing now: the smallest fleet ({seats(1)} seats of {machine.seat_mb} MB"
                         + (f" and a test run of {machine.run_mb} MB" if machine.run_mb else "")
                         + f") does not fit in {machine.free_mb} MB free above the {machine.floor_mb} MB floor")
    elif isinstance(limit, int) and not isinstance(limit, bool) and seats(1) > limit:
        raise_nothing = (f"raise nothing now: [fleet.sizing] max_seats = {limit} is below the smallest fleet "
                         f"({seats(1)} seats)")

    terms = ", ".join(f"{name} {value}" for name, value in caps.items())
    lines = [f"authors {authors} = max(1, min({terms or 'nothing known'})) - limited by {binding}"]
    for key in ("backlog", "test runs", "memory", "disk", "max seats"):
        if key in detail:
            lines.append("  " + detail[key])
    lines += ["  " + line for line in detail.get("backlog-unknown", "").splitlines()]
    if machine.live_seats:
        lines.append(f"  {machine.live_seats} seat(s) already running, inside the free memory above")
    lines.append(f"reviewers {reviewers}: {authors} author(s) x {pace.handovers_per_author_hour} handovers/h x "
                 f"{pace.review_hours} h a review (pace: {pace.note})")
    lines.append(f"judge {judge}: " + ("the profile requires one or has a deploy target" if judge else
                                       "no judge required and no deploy target"))
    lines.append("code: not measured in this version")
    lines.append("money: not measured in this version")
    return Recommendation(counts, authors, dict(caps), binding, lines, raise_nothing)
