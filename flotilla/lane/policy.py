"""Who runs next on a shared machine, and whom to freeze for it: a pure function of the runs and the machine (lane
shared machine design, sections 2 and 3).

Runs are admitted by the memory free now, not by a count. The line is ordered by a priority built from a run's
purpose, how long it has waited and how long it has run; a run that does not fit may have a younger, cheaper run
frozen - never killed - to make room for it. Every waiter asks the same question of the same picture inside the
journal's lock, so all of them agree on the order; each answers only for itself.

Nothing here reads the machine or the journal: `admission.view` builds the picture, the grant acts on the answer.
"""

from __future__ import annotations

from dataclasses import dataclass, field

WAITING, RUNNING, ORDERED, FROZEN = "waiting", "running", "ordered", "frozen"
#: A run's purpose and its type value (section 2.5): set by meaning, not by the model.
TYPES = {"push": 1.0, "handover": 0.7, "review": 0.5, "run": 0.3}
#: The weights of type, wait and run time; the wait saturates at ten minutes, the run time at the immortality mark.
WEIGHTS = (0.2, 0.3, 0.5)
WAIT_FULL_S = 600.0
#: A heavy waiter that has waited this long and still does not fit drains the lane: no new run starts after it in
#: line, light ones too, until the runs ahead of it end and it fits (the person's ruling, 2026-11-17: a heavy head
#: of the line waited hours while lighter runs and the other project's turns kept its room taken). The wait part of
#: its priority is full by then.
DRAIN_AFTER_S = WAIT_FULL_S
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
    fair: tuple = ()            # under the fair share: (its project's runs held, its last service); () fifo
    project: str = ""           # whose run it is: the fair share counts a turn per project
    pinned: bool = False        # never a victim: by hand, a `freeze = false` tier, an older flotilla's
    freezable: bool = True      # its wrapper can obey an order
    holds: bool = False         # its freeze orders are out: the runs behind it leave it the memory it needs
    refused_for: str = ""       # whose order it refused a moment ago: no waiter's candidate until the bound is over
    start_mb: int | None = None  # what it usually takes, its ceiling's start: admitted by mem_mb (its largest)
    admitted_mb: int = 0        # running: the memory it was admitted by, pinned at its start - promised while no
                                # ceiling holds it
    reserved_mb: int = 0        # running, first half-minute: what the view reserved for it (admitted less its tree)
    bound: bool = True          # no decided ceiling went unread: its group reads back what the daemon wrote
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
    expected_s: float | None = None  # its measured estimate in seconds; None: never measured (the wait estimate)
    hung: bool = False               # the daemon reads it as hung (`Scheduler._hung`): its estimate says nothing
    by_hand: bool = False            # taken by hand: held until released, no estimate ends it


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
    heavy_mb: int | None = None       # machine.toml `lane_heavy_mb`: the heavy runs' memory, set by the person; None:
                                      # the share of `give_mb` the light reserve leaves
    capacity_mb: int | None = None    # the room an empty lane would have with Claude Code's sessions retired too:
                                      # `give_mb` plus what they could free, the orchestrator's and the waiting
                                      # seat's kept; None: not read, or no heavy waiter needs it


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


#: A turn taken in the replay: later than every last service the journal can hold (ISO times sort as text).
SERVED_NOW = "~"


def _offered(runs: list[Run], immortal_s: float, turns: dict[str, int]):
    """The waiting and frozen runs in the order room is offered to them: by priority, then by place in line. Under the
    fair share, among equal priorities, a waiter whose freeze orders are out goes first - the room its freeze makes
    is its own - then the project holding the fewest runs and served least recently, its oldest waiter first. A
    waiter the replay starts counts as its project's turn (`turns`, kept by the replay as it goes), so the next place
    is weighed again (project share design, section 3): two projects whose runs held differ by one at most split two
    slots freed at once, and a waiter that cannot start costs its project nothing."""
    p = {r.id: priority(r, immortal_s) for r in runs}
    queue = sorted((r for r in runs if r.state in (WAITING, FROZEN)), key=lambda r: (-p[r.id], r.order))
    if not any(r.fair for r in queue):
        yield from queue
        return

    def rank(r: Run) -> tuple:
        taken = turns.get(r.project, 0)
        return (not r.holds, (r.fair[0] + taken, SERVED_NOW if taken else r.fair[1]) if r.fair else (), r.order)
    i = 0
    while i < len(queue):
        j = i
        while j < len(queue) and p[queue[j].id] == p[queue[i].id]:
            j += 1
        heads: dict[tuple, list[Run]] = {}   # each project's own line, in order: only its head is ever chosen
        for r in queue[i:j]:
            heads.setdefault((r.project, r.holds), []).append(r)
        for own in heads.values():
            own.reverse()
        while heads:
            key = min(heads, key=lambda k: rank(heads[k][-1]))
            r = heads[key].pop()
            if not heads[key]:
                del heads[key]
            yield r
        i = j


@dataclass
class _Replay:
    seen: list            # the runs the replay reached, in its order
    pool: list
    free: int | None
    kept: int
    unfit_ahead: list
    done: Decision | None = None   # decided in the loop: `mine` started, or waits behind a heavy run
    why: str = ""
    about: str = ""
    behind: str = ""               # the heavy waiter `mine` waits behind, when that decided it
    outside: dict = field(default_factory=dict)   # heavy waiters no drain can serve: {id: {need_mb, room_mb}}


def _replay(runs: list[Run], machine: Machine, mine: str) -> _Replay:
    """Admission replayed in the line's order, as every waiter replays it, up to `mine` (to the end for "")."""
    by_id = {r.id: r for r in runs}
    pool = [r for r in runs if r.state in (RUNNING, ORDERED)]
    # a waiter whose orders are out holds what it needs against the runs behind it: a run passing it would take the
    # room the freeze made, and the waiter would order again, freezing run after run for nothing (found replaying the
    # model's queues: one run frozen four times by the same heavy waiter that never started)
    out = _Replay([], pool, machine.free_mb, 0, [])
    blocker = None
    drain = None   # a heavy waiter past DRAIN_AFTER_S that does not fit: no new run starts after it
    turns: dict[str, int] = {}
    for r in _offered(runs, machine.immortal_s, turns):
        out.seen.append(r)
        if drain is not None and r.state == WAITING:   # a frozen run holds its slot already; it comes back as before
            if r.id == mine:
                alone = ", and starts only when the lane is empty" if alone_only(drain, machine) else ""
                out.done = Decision(False, (), f"behind heavy {drain.id}, which has waited over "
                                               f"{round(DRAIN_AFTER_S / 60)} min{alone}: the lane starts no new run "
                                               "until it fits")
                out.behind = drain.id
                return out
            continue
        if r.heavy and blocker is not None:
            if r.id == mine:   # and why it waits, when that wait has no end while the lane is busy (2026-11-11)
                alone = alone_only(by_id[blocker], machine)
                out.done = Decision(False, (), f"behind heavy {blocker}"
                                    + (", which starts only when the lane is empty" if alone else ""))
                out.behind = blocker
                return out
            continue
        failed, kind = _fits(r, pool, None if out.free is None else out.free - out.kept, machine)
        if not failed:
            pool.append(r)
            if out.free is not None:
                out.free -= r.mem_mb
            if r.state == WAITING:   # a frozen run holds its slot already: it is counted in its project's runs
                turns[r.project] = turns.get(r.project, 0) + 1
            if r.id == mine:
                out.done = Decision(True)
                return out
            continue
        if r.heavy:
            blocker = r.id
            if r.state == WAITING and r.waited_s >= DRAIN_AFTER_S:
                # the reason holds no running count: it is what a waiting `lane run` reprints (review of 1241703, 1)
                # draining behind a run that never ends stops the whole lane for nothing (review, 2) - but only
                # when that run is what keeps it out: once every other run ahead has ended, would it fit beside the
                # endless ones? (review of 4cc2e8c, 1: a small hung run turned every drain off)
                endless = [p for p in pool if p.state in (RUNNING, ORDERED) and (p.hung or p.by_hand)]
                room_after = None if out.free is None else \
                    out.free - out.kept + sum(p.mem_mb for p in pool if p not in endless)
                if outside_lane(r, machine, _by_hand_mb(pool)):
                    # it would not fit in an empty lane either: the memory is held outside the lane - Claude Code's
                    # sessions, most often - and a drain would stop every project for nothing. The lane stays open,
                    # it stays first among the heavy runs, and its orchestrator is told (the person's ruling,
                    # 2026-11-17)
                    out.outside[r.id] = {"need_mb": r.mem_mb, "room_mb": machine.give_mb + _by_hand_mb(pool)}
                    if pool:   # in an empty lane its reason says so already
                        failed = (f"{failed}; no drain for it: it would not fit in an empty lane either - the memory "
                                  "is held outside the lane")
                elif endless and _fits(r, endless, room_after, machine)[0]:
                    # a run taken by hand frees what `give_mb` does not count: it is named, not read as outside
                    # (review of 5edc98d, M1); a small hung one does not hide memory held outside (review of 0ff3a22)
                    failed = (f"{failed}; no drain for it while {', '.join(p.id for p in endless)} holds the lane "
                              "(hung or taken by hand)")
                else:
                    drain = r
                    failed = (f"{failed}; waited over {round(DRAIN_AFTER_S / 60)} min: the lane starts no new run "
                              "until this one fits")
        if r.id == mine:
            out.why, out.about = failed, kind
            return out
        if r.holds:
            out.kept += r.mem_mb
        if kind == FREE:
            out.unfit_ahead.append(r)
    return out


def eta_s(runs: list[Run], machine: Machine, mine: str, _depth: int = 0) -> float | None:
    """By the estimates of the runs ahead, in how many seconds `mine` fits: the runs in its way end one by one in the
    order their estimates say, and the first moment it fits is the answer (field notes 2026-11-16: "heavy runs'
    cores: 7.58 of 12" was true and said nothing of when). Behind a heavy waiter, that heavy run's moment. None when
    it fits only once a run without an estimate ends, or never by these runs. An estimate is not a promise: a run past
    its estimate is taken to end now."""
    seen = _replay(runs, machine, mine)
    if seen.done is not None:
        if seen.done.start:   # the replay starts it: whatever holds it is not the runs ahead (review of 1241703, 4)
            return None
        return eta_s(runs, machine, seen.behind, _depth + 1) if seen.behind and _depth < 8 else None
    me = next((r for r in runs if r.id == mine), None)
    if me is None or not seen.why:
        return None
    pool = list(seen.pool)
    room = None if seen.free is None else seen.free - seen.kept
    ends = sorted(((max(0.0, r.expected_s - r.ran_s), r) for r in pool if r.expected_s and not r.hung
                   and not r.by_hand), key=lambda pair: (pair[0], pair[1].order))
    for left, r in ends:
        pool.remove(r)
        room = None if room is None else room + r.mem_mb
        if not _fits(me, pool, room, machine)[0]:
            return left
    return None


def eta_words(seconds: float | None) -> str:
    """The wait estimate as a waiter's line says it; "" when there is none."""
    if seconds is None or seconds > 7 * 86400:   # a week out is no estimate (a forged beat, say)
        return ""
    if seconds < 30:
        return "by their estimates the runs ahead should end about now"
    minutes = max(1, round(seconds / 60))
    when = f"~{minutes} min" if minutes < 90 else f"~{minutes / 60:.1f} h"
    # the waiters ahead of it may take the room first: the runs' ends are its earliest (review of 1241703, 5)
    return f"by the estimates of the runs ahead it fits in {when} at the earliest"


def outside_lane(r: Run, machine: Machine, held_mb: int = 0) -> bool:
    """Whether heavy `r` would not fit even with every lane run ended, yet would on this machine with nothing else
    running: the memory it lacks is held outside the lane - Claude Code's sessions, most often - and no wait for the
    lane's runs cures that. `give_mb` is that empty-lane room: MemAvailable less the floor, plus what the held runs
    take, frozen ones too. Only what retiring sessions can free counts as curable (`capacity_mb`): a run larger than
    that - the kernel, the services, the orchestrator's own session never leave - starts alone in an empty lane, as
    no wait would let it in (reviews of f16df11, 1, and 5edc98d, 1). `held_mb`: what runs taken by hand hold - no
    process tree of theirs is in `give_mb` - which they free when they end (review of 0ff3a22, 1)."""
    return (r.heavy and machine.give_mb is not None and machine.capacity_mb is not None
            and machine.give_mb + held_mb < r.mem_mb <= machine.capacity_mb + held_mb)


def starved_room(r: Run, runs: list[Run], machine: Machine) -> int | None:
    """The most this machine could give heavy `r`, MB, when `r` needs more: every lane run ended, what runs taken by
    hand hold freed, Claude Code's sessions retired but the orchestrator's and the waiting seat's (`capacity_mb`). No
    wait gives it that memory: it starts alone in an empty lane, as before, and its orchestrator is told it may be
    killed - split or narrow it (the person's ruling, 2026-11-18). None when it is not short, or `capacity_mb` was not
    read."""
    if not r.heavy or machine.capacity_mb is None:
        return None
    room = machine.capacity_mb + _by_hand_mb([p for p in runs if p.id != r.id])
    return room if r.mem_mb > room else None


def _by_hand_mb(pool: list[Run]) -> int:
    return sum(p.mem_mb for p in pool if p.by_hand and p.state in (RUNNING, ORDERED))


def held_outside(runs: list[Run], machine: Machine) -> dict[str, dict]:
    """The heavy waiters past DRAIN_AFTER_S that would not fit even in an empty lane, with the memory each needs and
    the room an empty lane would leave it (MB): the lane does not drain for them, their orchestrators are told."""
    return _replay(runs, machine, "").outside


def line(runs: list[Run], machine: Machine) -> list[Run]:
    """The waiting and frozen runs in the order the replay reaches them: the order the daemon's tick asks them in."""
    return _replay(runs, machine, "").seen


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


#: A run started within this window is counted at least at the memory it was admitted by: a just-started process has
#: not grown into its memory yet, and the next waiter would read it as free (section 2.2). Builder's number, awaits
#: the field test.
WINDOW_S = 30.0


def promised(r: Run, ceiling: int | None) -> int:
    """What a running run may still take that the room must not give away, beyond the view's reservation for it. A
    ceiling in a scope holds it: its headroom. With no scope, no ceiling binds - the signal backend, macOS, a refused
    scope - so the memory it was admitted by stays promised, as the 0.30 reservation did (review of option B,
    finding 1). In its first half-minute the view has reserved `reserved_mb` for it already, sized from its process
    tree; only what the headroom holds beyond that counts again (second review, important 1)."""
    if r.state not in (RUNNING, ORDERED):
        return 0
    held = r.scoped and r.bound   # a group whose write did not hold is held by nothing (third review of option B)
    admitted = r.admitted_mb + slack(r.admitted_mb) if r.admitted_mb else 0   # as its start ceiling at that figure was
    top = ceiling if held else max(ceiling or 0, admitted)
    if top is None:
        return 0
    return max(0, top - r.mem_mb - r.reserved_mb)


def reserved_for(r: Run, ceiling: int | None) -> int:
    """What the view reserved for `r` that its ceiling's headroom does not cover yet: it may grow into that without
    taking the room of others."""
    if r.state not in (RUNNING, ORDERED):
        return 0
    headroom = max(0, ceiling - r.mem_mb) if ceiling is not None else 0
    return max(0, r.reserved_mb - headroom)


def room(runs: list[Run], machine: Machine) -> int | None:
    """What is left once every ceiling's unused headroom is counted as promised: MemAvailable, less the floor, the
    reservation for just-started runs, and each capped run's ceiling over its use. None when memory is unknown."""
    if machine.available_mb is None or machine.free_mb is None:
        return None
    reserved = max(0, machine.available_mb - machine.floor_mb - machine.free_mb)
    return machine.available_mb - machine.floor_mb - sum(promised(r, r.ceiling_mb) for r in runs) - reserved


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


def _memory_why(need: int, free: int, machine: Machine, *, heavy_room: int | None = None) -> str:
    """The room is not what the machine has free: the daemon reads it as MemAvailable less the floor and less what
    the runs started or ahead in line are held to - ceilings' headroom, first half-minutes (field test item 11).
    `heavy_room`: the share the heavy runs may still take, when that and not the room holds a heavy run back."""
    if heavy_room is not None and heavy_room < free:
        return f"memory: {need} MB needed, {max(0, heavy_room)} MB of room for heavy runs, {_reserve_words(machine)}"
    why = f"memory: {need} MB needed, " + (f"{free} MB of room" if free > 0 else "no room")
    if machine.available_mb is not None:
        held = max(0, machine.available_mb - machine.floor_mb - free)
        why += (f" ({machine.available_mb} MB available, less the {machine.floor_mb} MB floor and {held} MB held for "
                f"runs started or ahead in line)")
    return f"{why}, {_reserve_words(machine)}"


#: What a failed check was about: only a lack of free memory is one that freezing a run can cure.
FREE, BOUND = "free", "bound"


def heavy_share(give_mb: int | None, light_reserve: float, heavy_mb: int | None = None) -> int | None:
    """The most memory the heavy runs may take together: the person's `lane_heavy_mb` where set, else the share of
    what the lane can give that the light reserve leaves; None when memory cannot be read. Either way a run starts
    only into the room the machine has free above its floor."""
    if give_mb is None:
        return None
    return int(heavy_mb) if heavy_mb else int((1 - light_reserve) * give_mb)


def _heavy_share(machine: Machine) -> int | None:
    return heavy_share(machine.give_mb, machine.light_reserve, machine.heavy_mb)


def _reserve_words(machine: Machine) -> str:
    if machine.heavy_mb:
        return f"heavy runs' memory set to {machine.heavy_mb} MB (lane_heavy_mb)"
    return f"reserve {round(machine.light_reserve * machine.give_mb)} MB for light runs"


def alone_only(r: Run, machine: Machine) -> bool:
    """A heavy run larger than the heavy share fits beside no run: only an empty lane takes it (section 2.2)."""
    share = _heavy_share(machine)
    return r.heavy and share is not None and r.mem_mb > share


def _fits(r: Run, pool: list[Run], free: int | None, machine: Machine) -> tuple[str, str]:
    """("", "") when `r` may start beside `pool` with `free` MB; else every check that fails, named, and whether
    only free memory failed (FREE) or a bound of the lane did (BOUND). Every bound is named, not the first: a run
    held by the cores of the moment and by a size that never fits was told only of the cores (field test
    2026-11-11)."""
    if not pool:
        # an empty machine takes its first run whatever its size (section 2.2) - a heavy one not while the memory
        # it needs is held outside the lane: started into the memory Claude Code's sessions held, one took the
        # machine down to their reap line (field notes 2026-11-17, the person's ruling). No freeze cures that.
        if outside_lane(r, machine):
            return (f"memory: {r.mem_mb} MB needed, {max(0, machine.give_mb)} MB with the lane empty: the rest is "
                    "held outside the lane"), BOUND
        return "", ""
    failed = []
    if len(pool) >= machine.max_runs:
        failed.append(f"runs at once: {len(pool)} of {machine.max_runs}")
    known = machine.give_mb is not None and free is not None
    limit = machine.cpu_factor * machine.cpus
    if not known:   # no heavy runs to tell by memory: the bound on cores holds for every run (focus 4, final review)
        used = sum(p.par for p in pool)
        if used + r.par > limit:
            failed.append(f"cores: {used:g} of {limit:g}")
    heavies = [p for p in pool if p.heavy]
    if r.heavy:   # beside any run: only an empty machine lets one heavy run past them (sections 2.2, 2.4)
        used = sum(p.par for p in heavies)
        if heavies and used + r.par > limit:
            failed.append(f"heavy runs' cores: {used:g} of {limit:g}")
        if known:
            share = _heavy_share(machine)
            room = share - sum(p.mem_mb for p in heavies)
            if alone_only(r, machine):
                failed.append(f"memory: {r.mem_mb} MB needed, more than the {share} MB heavy runs may take "
                              f"({_reserve_words(machine)}): it starts only when the lane is empty")
            elif r.mem_mb > room:
                failed.append(_memory_why(r.mem_mb, free, machine, heavy_room=min(free, int(room))))
    if failed:   # the room of the moment is not named beside a bound: it would reprint the wait at every look
        return "; ".join(failed), BOUND
    # free memory alone: FREE then means nothing but memory stands in the way, so a freeze would let the run in -
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
    me = next((r for r in runs if r.id == mine), None)
    if me is None or me.state not in (WAITING, FROZEN):
        return Decision(False, (), "not in line")
    seen = _replay(runs, machine, mine)
    if seen.done is not None:
        return seen.done
    why, about, pool, free, kept, unfit_ahead = seen.why, seen.about, seen.pool, seen.free, seen.kept, seen.unfit_ahead

    def room(r: Run) -> int | None:
        return None if free is None else free - kept
    memory_known = machine.give_mb is not None and free is not None
    if not memory_known or me.state == FROZEN or not may_freeze or about != FREE:
        return Decision(False, (), why, about)   # a frozen run comes back when it fits, never by an order (2.8)
    # what it lacks is held outside the lane: no freeze lets it in (review of 5edc98d, M2: with `lane_heavy_mb` set,
    # its reason can read as free memory)
    if outside_lane(me, machine, _by_hand_mb(pool)):
        return Decision(False, (), why, about)
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
