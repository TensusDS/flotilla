"""Who runs next on a shared machine, and whom to freeze for it: a pure function of the runs and the machine (lane
shared machine design, sections 2 and 3).

Runs are admitted by the memory free now, not by a count. The line is ordered by a priority built from a run's
purpose, how long it has waited and how long it has run; a run that does not fit may have a younger, cheaper run
frozen - never killed - to make room for it. Every waiter asks the same question of the same picture inside the
journal's lock, so all of them agree on the order; each answers only for itself.

Nothing here reads the machine or the journal: `admission.view` builds the picture, the grant acts on the answer.
"""

from __future__ import annotations

from dataclasses import dataclass

WAITING, RUNNING, ORDERED, FROZEN = "waiting", "running", "ordered", "frozen"
#: A run's purpose and its type value (section 2.5): set by meaning, not by the model.
TYPES = {"push": 1.0, "handover": 0.7, "review": 0.5, "run": 0.3}
#: The weights of type, wait and run time; the wait saturates at ten minutes, the run time at the immortality mark.
WEIGHTS = (0.2, 0.3, 0.5)
WAIT_FULL_S = 600.0
#: A run in its first minute is never a candidate: freezing what has not started working buys nothing (section 2.8).
GRACE_S = 60.0
#: How much lower a victim's priority must be: strictly lower, 0.05 being as good in the model (section 2.8).
MARGIN = 0.0
#: The mark between light and heavy: this share of the memory the lane can give. Builder's number, awaits the field
#: test (section 9).
HEAVY_SHARE = 0.2
#: What a freeze moves to swap beyond the memory the waiter lacks. Builder's number, awaits the field test.
RECLAIM_MARGIN_MB = 128
#: A freeze leaves the machine's swap at most this much used: systemd-oomd, on by default on Ubuntu and Fedora,
#: kills the group holding the most swap at 90 %, and that would be our frozen run (section 4.3, the person's ruling).
#: It also keeps ours within 90 % of (free swap + ours), the person's other ruling, on any machine: total swap is at
#: least free plus ours, so `take <= free - 0.2 * total` gives `take <= 0.9 * free - 0.1 * ours`.
SWAP_USED_CAP = 0.8
#: Why a freeze was ordered (lane daemon design, 4.3): for a waiter's start, for a machine over its floor, for a run
#: churning at its ceiling with no room to give it.
WAITER, OVER, PRESSED_CAUSE = "waiter", "over", "pressed"
#: Within this much of the lowest priority, the run that grew most over the last minute is the victim (the person's
#: ruling, 2026-11-07): booking order says nothing about which one hurt the machine.
TIE_P = 0.05
#: No ceiling, and no squeeze, below this; and a ceiling's headroom is at least this or a tenth of it. Builder's
#: numbers, from the bench of 2026-11-07; await the field test (lane daemon design, section 7).
MIN_CEILING_MB = 256
SLACK_SHARE = 0.10


@dataclass(frozen=True)
class Run:
    id: str
    state: str            # WAITING, RUNNING, ORDERED, FROZEN
    mem_mb: int           # running: held now (or reserved, if more); waiting/frozen: what it needs
    heavy: bool
    par: float            # cores it can use, for the heavy CPU bound
    kind: str = "run"
    waited_s: float = 0.0       # since it first asked
    ran_s: float = 0.0          # run time across thaws
    since_start_s: float = 0.0  # since its last start or thaw
    order: int = 0              # its place in line
    fair: tuple = ()            # tie-break key between equal priorities (fair share), smaller first
    pinned: bool = False        # never a victim: by hand, a `freeze = false` tier, an older flotilla's
    freezable: bool = True      # its wrapper can obey an order
    holds: bool = False         # its freeze orders are out: the runs behind it leave it the memory it needs
    refused_for: str = ""       # whose order it refused a moment ago: no waiter's candidate until the bound is over
    # what the lane daemon reads of a run's group and keeps between its ticks (lane daemon design, 4.3)
    ceiling_mb: int | None = None   # its soft ceiling (memory.high); None: none
    scoped: bool = False            # its group was found: a ceiling can be set and read
    at_ceiling: bool = False        # the kernel throttled it at its ceiling since the last tick
    pressure: float | None = None   # its group's memory pressure, "some avg10", percent
    pressed_s: float = 0.0          # how long it has stalled at its ceiling at or above the pressed mark
    grew_mb: int = 0                # its growth over the last minute
    peak_mb: int = 0                # the most it has held
    squeezed: bool = False          # its ceiling was lowered under its use for an overload
    cause: str = ""                 # its order's or its freeze's cause: WAITER, OVER, PRESSED_CAUSE
    frozen_s: float = 0.0           # how long the freeze in force has lasted
    held_freezes: int = 0           # its freezes for an overload or a pressure
    reclaimed_mb: int = 0           # what its freeze moved to swap, to fault back on a thaw
    rule: int = 5                   # its booking's rule: the daemon starts only rule 5 and later


@dataclass(frozen=True)
class Machine:
    cpus: int
    give_mb: int | None   # the memory the lane can give (section 2.2); None: memory cannot be read
    free_mb: int | None   # free now, less the floor and the reservation for just-started runs
    swap_free_mb: int | None
    swap_total_mb: int | None = None
    swap_pending_mb: int = 0   # ordered to swap and not yet moved: the orders no victim has answered yet
    cpu_factor: float = 1.5
    light_reserve: float = 0.2
    immortal_s: float = 900.0
    max_runs: int = 10
    freeze: bool = True
    available_mb: int | None = None   # MemAvailable now (the daemon's reading)
    floor_mb: int = 0                 # the largest floor among the projects with a live booking
    over_s: float = 0.0               # how long MemAvailable has stood below the floor


@dataclass(frozen=True)
class Decision:
    start: bool
    freeze: tuple = ()    # ((victim id, MB to reclaim), ...)
    why: str = ""         # what it waits for, named (section 5); "" when it starts
    about: str = ""       # FREE when free memory alone holds it back: the only lack a freeze can cure


def priority(run: Run, immortal_s: float) -> float:
    type_part, wait_part, run_part = WEIGHTS
    return (type_part * TYPES.get(run.kind, TYPES["run"])
            + wait_part * min(1.0, max(0.0, run.waited_s) / WAIT_FULL_S)
            + run_part * min(1.0, max(0.0, run.ran_s) / immortal_s if immortal_s > 0 else 1.0))


def immortal(run: Run, immortal_s: float) -> bool:
    return run.pinned or run.ran_s >= immortal_s


def is_heavy(mem_mb: int, give_mb: int | None, was: bool = False) -> bool:
    """Heavy once, heavy for good: a run that grew past the mark keeps the rules of the heavy (section 2.3)."""
    if was:
        return True
    return give_mb is not None and mem_mb >= HEAVY_SHARE * give_mb


#: A floor a booking names is believed only up to a quarter of the machine - a cap that is itself never under the
#: default floor of 1500 MB - and never below zero: any session writes the journal, and one floor out of reason would
#: read the whole machine as over (final review C2). Within the cap a booking may still raise the floor: the lane
#: then starts less and squeezes sooner, never more (fix review R9).
FLOOR_SHARE_MAX, FLOOR_DEFAULT_MB, FLOOR_UNKNOWN_CAP_MB = 0.25, 1500, 4096


def clamp_floor(mb: int, total_mb: int | None) -> int:
    cap = max(FLOOR_DEFAULT_MB, int(total_mb * FLOOR_SHARE_MAX)) if total_mb else FLOOR_UNKNOWN_CAP_MB
    return max(0, min(int(mb), cap))


def slack(mb: int) -> int:
    """A ceiling's headroom: what a run may grow before the kernel holds it."""
    return max(MIN_CEILING_MB, round(SLACK_SHARE * mb))


def start_ceiling(estimate_mb: int) -> int:
    """The ceiling a run starts under: its estimate, at least 256 MB, and the slack (lane daemon design, 4.3 b)."""
    base = max(int(estimate_mb), MIN_CEILING_MB)
    return base + slack(base)


def room(runs: list[Run], machine: Machine) -> int | None:
    """What is left once every ceiling's unused headroom is counted as promised: MemAvailable, less the floor, the
    reservation for just-started runs, and each capped run's ceiling over its use. None when memory is unknown."""
    if machine.available_mb is None or machine.free_mb is None:
        return None
    reserved = max(0, machine.available_mb - machine.floor_mb - machine.free_mb)
    promised = sum(max(0, r.ceiling_mb - r.mem_mb) for r in runs
                   if r.state in (RUNNING, ORDERED) and r.ceiling_mb is not None)
    return machine.available_mb - machine.floor_mb - promised - reserved


def daemon_reason(v: Run, machine: Machine) -> str:
    """Why `v` may not be squeezed or frozen for an overload or a pressure; "" when it may. The first-minute grace
    does not hold here (the person's ruling 5, 2026-11-07): the run that just ballooned is usually the newest."""
    if v.state != RUNNING:
        return f"{v.id} is not running"
    if v.pinned:
        return f"{v.id} is pinned"
    if not v.freezable:
        return f"{v.id} cannot be frozen"
    if immortal(v, machine.immortal_s):
        return f"{v.id} is immortal"
    if v.refused_for:
        return f"{v.id} refused an order a moment ago"
    return ""


def victim(candidates: list[Run], immortal_s: float) -> Run | None:
    """The lowest priority first; within TIE_P of it, the run that grew most over the last minute; then the lowest
    priority, then the newest."""
    if not candidates:
        return None
    lowest = min(priority(r, immortal_s) for r in candidates)
    near = [r for r in candidates if priority(r, immortal_s) <= lowest + TIE_P]
    return min(near, key=lambda r: (-r.grew_mb, priority(r, immortal_s), -r.order))


def _memory_why(need: int, free: int, machine: Machine) -> str:
    reserve = round(machine.light_reserve * machine.give_mb)
    return f"memory: {need} MB needed, {max(0, free)} MB free, reserve {reserve} MB for light runs"


#: What a failed check was about: only a lack of free memory is one that freezing a run can cure.
FREE, BOUND = "free", "bound"


def _fits(r: Run, pool: list[Run], free: int | None, machine: Machine) -> tuple[str, str]:
    """("", "") when `r` may start beside `pool` with `free` MB; else the first check that fails, named, and whether
    it was free memory (FREE) or a bound of the lane (BOUND)."""
    if not pool:
        return "", ""   # an empty machine takes its first run whatever its size (section 2.2)
    if len(pool) >= machine.max_runs:
        return f"runs at once: {len(pool)} of {machine.max_runs}", BOUND
    known = machine.give_mb is not None and free is not None
    limit = machine.cpu_factor * machine.cpus
    if not known:   # no heavy runs to tell by memory: the bound on cores holds for every run (focus 4, final review)
        used = sum(p.par for p in pool)
        if used + r.par > limit:
            return f"cores: {used:g} of {limit:g}", BOUND
    heavies = [p for p in pool if p.heavy]
    if r.heavy:   # beside any run: only an empty machine lets one heavy run past them (sections 2.2, 2.4)
        used = sum(p.par for p in heavies)
        if heavies and used + r.par > limit:
            return f"heavy runs' cores: {used:g} of {limit:g}", BOUND
        if known:
            room = (1 - machine.light_reserve) * machine.give_mb - sum(p.mem_mb for p in heavies)
            if r.mem_mb > room:
                return _memory_why(r.mem_mb, min(free, int(room)), machine), BOUND
    # free memory last: FREE then means nothing but memory stands in the way, so a freeze would let the run in -
    # never a freeze for a run a bound would still hold back
    if known and r.mem_mb > free:
        return _memory_why(r.mem_mb, free, machine), FREE
    return "", ""


def _why_not(v: Run, mine: Run, machine: Machine) -> str:
    """Why `v` cannot be frozen for `mine`; "" when it can."""
    if v.state == ORDERED:
        return f"{v.id} is being frozen already"
    if v.state != RUNNING:
        return ""
    if v.refused_for:   # no order and refusal trading lines in the journal, whichever waiters ask
        return f"{v.id} refused an order a moment ago"
    return victim_reason(v, mine, machine)


def victim_reason(v: Run, mine: Run, machine: Machine, *, by_rules_only: bool = False) -> str:
    """Why running `v` may not be frozen for waiting `mine` by the rules of section 2.8; "" when it may. The holder
    asks it too, of an order it finds on its booking, before it obeys (security review of 0.30.0) - `by_rules_only`:
    only what no reading of the machine changes. Heaviness depends on the memory the lane can give, which each
    project reads with its own floor, so the holder leaves it to the waiter (review of the 0.30.1 follow-up, I1)."""
    if v.pinned:
        return f"{v.id} is pinned"
    if not v.freezable:
        return f"{v.id} cannot be frozen"
    if immortal(v, machine.immortal_s):
        return f"{v.id} is immortal"
    if v.since_start_s < GRACE_S:
        return f"{v.id} is in its first minute"
    if v.heavy and mine.heavy and not by_rules_only:
        return f"{v.id} is heavy, as this run is"
    if not priority(v, machine.immortal_s) + MARGIN < priority(mine, machine.immortal_s):
        return f"{v.id} ranks as high"
    return ""


def _victims(mine: Run, pool: list[Run], free: int, machine: Machine) -> tuple[tuple, list[str]]:
    """The lowest-priority candidates, lowest first, until `mine` fits; () when all of them together do not make
    room. Each gives what is still lacking, at most what it holds, and the reclaim margin."""
    candidates, reasons = [], []
    for v in pool:
        if v.state not in (RUNNING, ORDERED):
            continue   # a run that starts ahead of mine in this very pass holds nothing yet
        reason = _why_not(v, mine, machine)
        (reasons.append(reason) if reason else candidates.append(v))
    lacking = mine.mem_mb - free
    chosen = []
    for v in sorted(candidates, key=lambda v: (priority(v, machine.immortal_s), -v.order)):
        if lacking <= 0:
            break
        share = min(v.mem_mb, lacking)
        chosen.append((v.id, share + RECLAIM_MARGIN_MB))
        lacking -= share
    return (tuple(chosen) if lacking <= 0 else ()), (reasons if not candidates else [])


def _swap_takes(take: int, machine: Machine) -> bool:
    if machine.swap_free_mb is None or not machine.swap_total_mb:
        return False
    used = machine.swap_total_mb - machine.swap_free_mb + machine.swap_pending_mb
    return used + take <= SWAP_USED_CAP * machine.swap_total_mb


def decide(runs: list[Run], machine: Machine, mine: str, *, may_freeze: bool = True) -> Decision:
    """Whether `mine` starts now, and if not, whom it orders frozen and what it waits for.

    Admission is replayed in priority order, as every waiter would replay it: those ahead take their room first. The
    class rules (section 2.7a): a light run passes a heavy one that does not fit, and a light one; no heavy run
    passes a higher heavy that does not fit. A heavy run may pass a light one that does not fit - the light one has
    freezing to make room and ends in minutes (the builder's call)."""
    by_id = {r.id: r for r in runs}
    me = by_id.get(mine)
    if me is None or me.state not in (WAITING, FROZEN):
        return Decision(False, (), "not in line")
    queue = sorted((r for r in runs if r.state in (WAITING, FROZEN)),
                   key=lambda r: (-priority(r, machine.immortal_s), r.fair, r.order))
    pool = [r for r in runs if r.state in (RUNNING, ORDERED)]
    free = machine.free_mb
    # a waiter whose orders are out holds what it needs against the runs behind it: a run passing it would take the
    # room the freeze made, and the waiter would order again, freezing run after run for nothing (found replaying the
    # model's queues: one run frozen four times by the same heavy waiter that never started)
    kept = 0
    blocker, why, about, unfit_ahead = None, "", "", []

    def room(r: Run) -> int | None:
        return None if free is None else free - kept
    for r in queue:
        if r.heavy and blocker is not None:
            if r.id == mine:
                return Decision(False, (), f"behind heavy {blocker}")
            continue
        failed, kind = _fits(r, pool, room(r), machine)
        if not failed:
            pool.append(r)
            if free is not None:
                free -= r.mem_mb
            if r.id == mine:
                return Decision(True)
            continue
        if r.heavy:
            blocker = r.id
        if r.id == mine:
            why, about = failed, kind
            break
        if r.holds:
            kept += r.mem_mb
        if kind == FREE:
            unfit_ahead.append(r)
    memory_known = machine.give_mb is not None and free is not None
    if not memory_known or me.state == FROZEN or not may_freeze or about != FREE:
        return Decision(False, (), why, about)   # a frozen run comes back when it fits, never by an order (2.8)
    if not machine.freeze:
        return Decision(False, (), f"{why}; freezing is off")
    if me.holds:   # its orders are out and their memory not yet free: ordering more would freeze runs for nothing
        return Decision(False, (), f"{why}; waits for its freeze to free the memory")
    if any(_victims(r, pool, room(r), machine)[0] for r in unfit_ahead if r.state == WAITING):
        return Decision(False, (), why)   # a higher waiter that does not fit makes its room first
    chosen, reasons = _victims(me, pool, room(me), machine)
    if not chosen:
        return Decision(False, (), f"{why}; no candidate to freeze: {', '.join(reasons)}" if reasons else why)
    take = sum(mb for _id, mb in chosen)
    if not _swap_takes(take, machine):
        return Decision(False, (), f"{why}; swap cannot take {take} MB")
    return Decision(False, chosen, f"freezing {', '.join(v for v, _mb in chosen)} for memory")
