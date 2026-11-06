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


@dataclass(frozen=True)
class Machine:
    cpus: int
    give_mb: int | None   # the memory the lane can give (section 2.2); None: memory cannot be read
    free_mb: int | None   # free now, less the floor and the reservation for just-started runs
    swap_free_mb: int | None
    swap_total_mb: int | None = None
    cpu_factor: float = 1.5
    light_reserve: float = 0.2
    immortal_s: float = 900.0
    max_runs: int = 10
    freeze: bool = True


@dataclass(frozen=True)
class Decision:
    start: bool
    freeze: tuple = ()    # ((victim id, MB to reclaim), ...)
    why: str = ""         # what it waits for, named (section 5); "" when it starts


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
    if known and r.mem_mb > free:
        return _memory_why(r.mem_mb, free, machine), FREE
    heavies = [p for p in pool if p.heavy]
    if r.heavy and heavies:   # the bounds hold beside other heavy runs (section 2.4)
        used, limit = sum(p.par for p in heavies), machine.cpu_factor * machine.cpus
        if used + r.par > limit:
            return f"heavy runs' cores: {used:g} of {limit:g}", BOUND
        if known:
            room = (1 - machine.light_reserve) * machine.give_mb - sum(p.mem_mb for p in heavies)
            if r.mem_mb > room:
                return _memory_why(r.mem_mb, min(free, int(room)), machine), BOUND
    return "", ""


def _why_not(v: Run, mine: Run, machine: Machine) -> str:
    """Why `v` cannot be frozen for `mine`; "" when it can."""
    if v.state == ORDERED:
        return f"{v.id} is being frozen already"
    if v.state != RUNNING:
        return ""
    if v.pinned:
        return f"{v.id} is pinned"
    if not v.freezable:
        return f"{v.id} cannot be frozen"
    if immortal(v, machine.immortal_s):
        return f"{v.id} is immortal"
    if v.since_start_s < GRACE_S:
        return f"{v.id} is in its first minute"
    if v.heavy and mine.heavy:
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
    used = machine.swap_total_mb - machine.swap_free_mb
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
    blocker, why, about, unfit_ahead = None, "", "", []
    for r in queue:
        if r.heavy and blocker is not None:
            if r.id == mine:
                return Decision(False, (), f"behind heavy {blocker}")
            continue
        failed, kind = _fits(r, pool, free, machine)
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
        if kind == FREE:
            unfit_ahead.append(r)
    memory_known = machine.give_mb is not None and free is not None
    if not memory_known or me.state == FROZEN or not may_freeze or about != FREE:
        return Decision(False, (), why)   # a frozen run comes back when it fits, never by an order (section 2.8)
    if not machine.freeze:
        return Decision(False, (), f"{why}; freezing is off")
    if any(_victims(r, pool, free, machine)[0] for r in unfit_ahead if r.state == WAITING):
        return Decision(False, (), why)   # a higher waiter that does not fit makes its room first
    chosen, reasons = _victims(me, pool, free, machine)
    if not chosen:
        return Decision(False, (), f"{why}; no candidate to freeze: {', '.join(reasons)}" if reasons else why)
    take = sum(mb for _id, mb in chosen)
    if not _swap_takes(take, machine):
        return Decision(False, (), f"{why}; swap cannot take {take} MB")
    return Decision(False, chosen, f"freezing {', '.join(v for v, _mb in chosen)} for memory")
