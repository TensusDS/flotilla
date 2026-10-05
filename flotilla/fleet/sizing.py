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
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

SEARCH = 200   # no cap allows more authors than this; a cap that reaches it prints "200+"
DISK_SHARE = 0.5
CEILING = 12   # seats on one machine unless the person's machine.toml says otherwise (review of 0.7.14)


@dataclass(frozen=True)
class Machine:
    free_mb: int | None
    floor_mb: int
    seat_mb: int
    live_seats: int | None        # this project's seats running now (not the orchestrator): outside free_mb
    run_mb: int | None            # the heaviest tier's measured peak
    run_seconds: float | None     # one full run, all handover tiers
    lane_capacity: int
    free_disk_mb: int | None
    tree_mb: int | None
    ceiling: int = CEILING        # the person's own limit, from machine.toml; a trunk profile may only lower it


@dataclass
class Recommendation:
    counts: dict[str, int]
    authors: int
    caps: dict[str, int | None]
    binding: str
    lines: list[str] = field(default_factory=list)
    raise_nothing: str = ""


def _reviewers(authors: int, pace) -> int:
    """Enough readers for the handovers the authors make, never more than the authors: the pace comes from the
    ledger, which sessions write, and a gamed pace once called for sixty reviewers for one task (review of 0.7.14)."""
    wanted = max(1, math.ceil(round(authors * pace.handovers_per_author_hour * pace.review_hours, 6)))
    return min(wanted, max(1, authors))


def _shown(value: int) -> str:
    return f"{value}+" if value >= SEARCH else str(value)


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
    live = max(0, machine.live_seats or 0)

    def seats(a: int) -> int:
        return a + _reviewers(a, pace) + 1 + judge

    caps: dict[str, int] = {}
    detail: dict[str, str] = {}
    unknown: list[str] = []

    known = [s for s in backlog.sources if s.main is not None]
    if known:
        caps["backlog"] = min(backlog.main + backlog.minor, SEARCH)
        detail["backlog"] = (f"backlog {backlog.main + backlog.minor}: "
                             + ", ".join(f"{s.name} {s.main + s.minor}" + (f" ({s.note})" if s.note else "")
                                         for s in known))
    unknown += [f"{s.name}: unknown ({s.note}), not counted" for s in backlog.sources if s.main is None]

    # MemAvailable already excludes the seats running now, and the recommendation is a total - spawn subtracts the
    # posts they hold - so their memory counts back into the room (review of 0.7.14, I1)
    room = None if machine.free_mb is None else machine.free_mb + live * machine.seat_mb - machine.floor_mb
    if room is not None:
        run = machine.run_mb or 0
        caps["memory"] = _largest(lambda a: seats(a) * machine.seat_mb + run <= room)
        detail["memory"] = (f"memory {_shown(caps['memory'])}: {machine.free_mb} MB free"
                            + (f" plus {live} running seat(s)" if live else "")
                            + f", {machine.floor_mb} MB kept free, {machine.seat_mb} MB a seat"
                            + (f", {machine.run_mb} MB a test run" if machine.run_mb else
                               ", a test run's memory never measured (not reserved)"))
    else:
        unknown.append("memory: unknown (free memory could not be read), not counted - "
                       "`spawn --recommended` raises nothing without --anyway")

    if machine.run_seconds:
        def runs_fit(a: int) -> int:
            if room is None or not machine.run_mb:
                return machine.lane_capacity
            return max(0, min(machine.lane_capacity, (room - seats(a) * machine.seat_mb) // machine.run_mb))

        def verified(a: int) -> float:
            return runs_fit(a) * 3600 / machine.run_seconds

        caps["test runs"] = _largest(lambda a: a * pace.handovers_per_author_hour <= verified(a))
        detail["test runs"] = (f"test runs {_shown(caps['test runs'])}: one full run takes "
                               f"{machine.run_seconds / 60:.1f} min"
                               + (f" and {machine.run_mb} MB" if machine.run_mb else "")
                               + f", the lane takes {machine.lane_capacity} at a time; authors hand over "
                               f"{pace.handovers_per_author_hour}/h each")
    else:
        unknown.append("test runs: unknown (no tier measured green on this machine), not counted")

    if machine.free_disk_mb is not None and machine.tree_mb:
        allowed = DISK_SHARE * machine.free_disk_mb + live * machine.tree_mb
        caps["disk"] = _largest(lambda a: seats(a) * machine.tree_mb <= allowed)
        detail["disk"] = (f"disk {_shown(caps['disk'])}: {machine.free_disk_mb} MB free, half of it for trees of "
                          f"{machine.tree_mb} MB each")
    else:
        unknown.append("disk: unknown (free disk or a tree's size could not be read), not counted")

    # the ceiling: the person's machine file sets it (12 by default); a profile on trunk may only lower it
    limit, said = machine.ceiling, f"at most {machine.ceiling} seats on this machine (machine.toml max_seats)"
    wanted = sizing.get("max_seats")
    if wanted is not None:
        if isinstance(wanted, int) and not isinstance(wanted, bool) and wanted >= 0:
            if wanted < limit:
                limit, said = wanted, f"[fleet.sizing] max_seats = {wanted}"
        else:
            unknown.append(f"[fleet.sizing] max_seats = {wanted!r} is not a whole number: ignored")
    caps["max seats"] = _largest(lambda a: seats(a) <= limit)
    detail["max seats"] = f"max seats {caps['max seats']}: {said}"

    order = ("memory", "test runs", "disk", "max seats", "backlog")   # a tie names the cap that refuses, not the work
    binding = min(caps, key=lambda name: (caps[name], order.index(name)))
    authors = max(1, caps[binding])
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
    elif seats(1) > limit:
        raise_nothing = f"raise nothing now: {said} is below the smallest fleet ({seats(1)} seats)"

    terms = ", ".join(f"{name} {_shown(value)}" for name, value in caps.items())
    lines = [f"authors {authors} = max(1, min({terms})) - limited by {binding}"]
    for key in ("backlog", "test runs", "memory", "disk", "max seats"):
        if key in detail:
            lines.append("  " + detail[key])
    lines += ["  " + line for line in unknown]
    lines.append(f"reviewers {reviewers}: {authors} author(s) x {pace.handovers_per_author_hour} handovers/h x "
                 f"{pace.review_hours} h a review, never more than the authors (pace: {pace.note})")
    lines.append(f"judge {judge}: " + ("the profile requires one or has a deploy target" if judge else
                                       "no judge required and no deploy target"))
    lines.append("code: not measured in this version")
    lines.append("money: not measured in this version")
    return Recommendation(counts, authors, dict(caps), binding, lines, raise_nothing)


def handover_cost(profile, seconds: dict, peaks: dict, *, problem: str = "") -> tuple[float | None, int | None, str]:
    """(seconds, peak MB, note) of one handover's run: the tiers required for a handover, run one after another, so
    their seconds add up and the heaviest one's peak is the run's. A tier never measured makes the time unknown -
    never zero (design, section 2.1); the peak is the largest measured one, said when some are missing. An
    unreadable measurements file is named as that, not as tiers that never ran (review of 0.7.14)."""
    tiers = [tier.get("name") for tier in ((profile.get("tests") or {}).get("tier") or [])
             if "handover" in (tier.get("required_for") or [])]
    if not tiers:
        return None, None, "no tier is required for a handover"
    if problem:
        return None, None, problem
    missing = [name for name in tiers if seconds.get(name) is None]
    total = None if missing else float(sum(seconds[name] for name in tiers))
    known = [peaks[name] for name in tiers if peaks.get(name) is not None]
    peak = max(known) if known else None
    notes = []
    if missing:
        notes.append(f"never run green here: {', '.join(missing)}")
    if peak is None:
        notes.append("peak memory never measured (a receipt run records it)")
    elif len(known) < len(tiers):
        notes.append("peak memory measured for some tiers only")
    return total, peak, "; ".join(notes)


SEAT_TREES_TRIED = 2


def _tree_size(ledger, main) -> tuple[int | None, str]:
    """A seat tree's size: an existing seat tree measured with du (its `.git` is a file, the objects are shared);
    with none yet, the main checkout's tracked files - build artefacts not counted, and said so. Only trees beside
    the main checkout named after it are measured, and at most two: `row.tree` is written by sessions, and a
    `--tree /` would cost a 20-second du per row (review of 0.7.14)."""
    from flotilla.core import resources
    main = Path(main).resolve()
    tried: list[str] = []
    for row in ledger.rows().values():
        if len(tried) >= SEAT_TREES_TRIED:
            break
        try:
            tree = Path(row.tree).resolve() if row.tree else None
        except (OSError, RuntimeError, ValueError):
            continue
        if tree is None or tree == main or tree.parent != main.parent or not tree.name.startswith(f"{main.name}-") \
                or not tree.is_dir():
            continue
        size = resources.tree_mb(tree)
        if size is not None:
            return size, f"measured on {tree}"
        tried.append(str(tree))
    failed = f"seat tree {', '.join(tried)} could not be measured (du failed or took over 20 s); " if tried else \
        "no seat tree yet: "
    try:
        listed = ledger.run(["git", "-C", str(main), "ls-files", "-z"], capture_output=True, text=True,
                            check=False, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None, failed + "git ls-files could not be asked"
    if listed.returncode != 0:
        return None, failed + "git ls-files failed"
    total = 0
    for name in filter(None, (listed.stdout or "").split("\0")):
        try:
            total += (main / name).lstat().st_size
        except OSError:
            continue
    return max(1, -(-total // 2**20)), failed + "the checkout's tracked files, build artefacts not counted"


def _machine_ceiling() -> tuple[int, str]:
    """The person's own seat ceiling: machine.toml `max_seats`, outside any repository, so no trunk raises it."""
    from flotilla.core import paths
    from flotilla.onboard.machine import read_machine
    try:
        value = (read_machine(paths.state_dir()) or {}).get("max_seats", CEILING)
    except (OSError, ValueError) as err:
        return CEILING, f"machine.toml could not be read ({err.__class__.__name__}): the ceiling of {CEILING} stands"
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value, ""
    return CEILING, f"machine.toml max_seats = {value!r} is not a whole number: {CEILING} stands"


def gather(ledger, *, tasks=None, tasks_file=None, census=None, run=subprocess.run, now=None) -> Recommendation:
    """Read this machine and this repository, then recommend: one census call, the measurements file, the ledger,
    the backlog sources the profile names (gh only when a tracker is set)."""
    import datetime as dt

    from flotilla.core import resources
    from flotilla.fleet import backlog as backlog_
    from flotilla.fleet import launch, spawn
    from flotilla.fleet import pace as pace_
    from flotilla.lane.commands import capacity
    from flotilla.onboard.firstrun import load_measurements, load_peaks, measurements_problem

    floor, seat, setting_notes = spawn.memory_settings(ledger)
    main = launch.main_checkout(ledger.root, run=ledger.run)
    notes = list(setting_notes)
    live = None
    if census is not None:
        from flotilla.core.census import CensusUnavailable
        from flotilla.ledger import project
        try:
            mine = project.members(census(), ledger.rows(), project.roots(ledger.root))
        except CensusUnavailable as err:
            notes.append(f"census: unknown ({err}); running seats not counted back into memory")
        else:
            held = spawn._live_posts(ledger, mine)
            live = sum(n for post, n in held.items() if post != "orchestrator")
    problem = measurements_problem(ledger.state_dir, ledger.repo_key)
    run_seconds, run_mb, run_note = handover_cost(ledger.profile, load_measurements(ledger.state_dir, ledger.repo_key),
                                                  load_peaks(ledger.state_dir, ledger.repo_key), problem=problem)
    if run_note:
        notes.append(f"test runs: {run_note}")
    tree, tree_note = _tree_size(ledger, main)
    notes.append(f"tree size: {tree_note}")
    ceiling, ceiling_note = _machine_ceiling()
    if ceiling_note:
        notes.append(ceiling_note)
    machine = Machine(free_mb=resources.available_mb(), floor_mb=floor, seat_mb=seat, live_seats=live,
                      run_mb=run_mb, run_seconds=run_seconds, lane_capacity=capacity(),
                      free_disk_mb=resources.free_disk_mb(Path(main).parent), tree_mb=tree, ceiling=ceiling)
    rows = ledger.rows()
    work = backlog_.gather(main, ledger.profile, rows, tasks=tasks, tasks_file=tasks_file, run=run)
    measured = pace_.pace(rows, now=now or dt.datetime.now(dt.timezone.utc))
    rec = recommend(machine, work, measured, ledger.profile)
    rec.lines.extend(f"  note: {line}" for line in notes)
    return rec


def render(rec: Recommendation) -> list[str]:
    """The recommendation as the person reads it: the counts first, then why."""
    order = ("orchestrator", "main", "minor", "reviewer", "sender", "judge")
    parts = [f"{post} {rec.counts[post]}" + (" (this session)" if post == "orchestrator" else "")
             for post in order if rec.counts.get(post)]
    if rec.raise_nothing:   # the refusal first: a headline of counts would read as a yes (review of 0.7.14)
        return [rec.raise_nothing, f"the smallest fleet would be: {', '.join(parts)}", *rec.lines]
    return [f"recommended: {', '.join(parts)}", *rec.lines]
