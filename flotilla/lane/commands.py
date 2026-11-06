"""`flotilla lane`, `lane take`, `lane release`, `lane run`, `lane sweep`, and the lane around a receipt."""

from __future__ import annotations

import contextlib
import datetime as dt
import os
import shlex
import signal
import subprocess
import time
from pathlib import Path

from flotilla.core import config, paths, repo
from flotilla.core.storage import LocalLogStore, StorageCorrupt
from flotilla.core.text import visible
from flotilla.lane import acquire as acq
from flotilla.lane import book, estimate, machine, signature
from flotilla.lane import run as runner
from flotilla.lane.procs import ProcessTable
from flotilla.ledger import gitq, runs
from flotilla.ledger.actor import resolve_actor
from flotilla.ledger.errors import ActorMismatch, ActorUnknown, MoveRefused
from flotilla.ledger.model import LedgerVersionError
from flotilla.onboard.machine import read_machine
from flotilla.posts import PostError


def _machine() -> dict:
    try:
        return read_machine(paths.state_dir()) or {}
    except (OSError, ValueError):
        return {}


def verdict_line(result) -> str:
    """What `lane run` says at its end. A red after a freeze says so: a test that times itself may have failed on
    the frozen minutes, not on the code (lane shared machine design, section 5)."""
    line = f"lane run: {result.verdict} - {result.summary}"
    frozen = getattr(result, "frozen_s", 0.0) or 0.0
    if result.verdict == "red" and frozen > 0:
        line += f" (red after a freeze of {max(1, round(frozen / 60))} min)"
    return line


def capacity() -> int:
    """machine.toml `lane_capacity`, 1 when unset, malformed or unreadable: a torn file never stops a run."""
    value = _machine().get("lane_capacity", 1)
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 1 else 1


#: machine.toml `lane_cpu_factor`: how far past its cores the machine is filled by light runs beside a heavy one.
#: CPU over-subscription only slows runs; the bounds keep it bounded (lane light runs design, section 2).
CPU_FACTOR, CPU_FACTOR_BOUNDS = 1.5, (1.0, 4.0)


def cpu_factor() -> float:
    """machine.toml `lane_cpu_factor`, 1.5 when unset, out of 1-4, not a number, or unreadable."""
    value = _machine().get("lane_cpu_factor", CPU_FACTOR)
    ok = isinstance(value, (int, float)) and not isinstance(value, bool) and \
        CPU_FACTOR_BOUNDS[0] <= value <= CPU_FACTOR_BOUNDS[1]
    return float(value) if ok else CPU_FACTOR


LIGHT_RESERVE, LIGHT_RESERVE_BOUNDS = 0.2, (0.0, 0.5)
IMMORTAL_MINUTES, IMMORTAL_BOUNDS = 15, (1, 240)
MAX_RUNS, MAX_RUNS_BOUNDS = 10, (1, 64)


def _number(key: str, default, bounds, *, whole: bool = False):
    """machine.toml `key`, the default when unset, out of bounds, the wrong type or unreadable."""
    value = _machine().get(key, default)
    kinds = (int,) if whole else (int, float)
    ok = isinstance(value, kinds) and not isinstance(value, bool) and bounds[0] <= value <= bounds[1]
    return value if ok else default


def light_reserve() -> float:
    """machine.toml `lane_light_reserve`: the share of the memory the lane can give kept for light runs (section 2.4)."""
    return float(_number("lane_light_reserve", LIGHT_RESERVE, LIGHT_RESERVE_BOUNDS))


def immortal_minutes() -> float:
    """machine.toml `lane_immortal_minutes`: the run time after which a run is never frozen (section 2.8)."""
    return _number("lane_immortal_minutes", IMMORTAL_MINUTES, IMMORTAL_BOUNDS)


def freeze_on() -> bool:
    """machine.toml `lane_freeze`: on unless it is the boolean false (section 4.2); anything else keeps it on."""
    return _machine().get("lane_freeze", True) is not False


def max_runs() -> int:
    """machine.toml `lane_max_runs`: runs at once at most, whatever memory allows (section 2.4)."""
    return _number("lane_max_runs", MAX_RUNS, MAX_RUNS_BOUNDS, whole=True)


#: A receipt's purpose that is also a run's type; any other run is a reviewer's or a plain one (section 2.5).
RECEIPT_KINDS = ("push", "handover")
REVIEWER_POST = "reviewer"


def kind_of(purpose: str, who: str, root) -> str:
    """A run's purpose, for its priority: a push or handover receipt by its purpose; a run of a session holding the
    reviewer post is a reviewer's; anything else is a plain run. A post that cannot be read makes a plain run."""
    if purpose in RECEIPT_KINDS:
        return purpose
    try:
        from flotilla.ledger.commands import fleet_name
        from flotilla.posts import load_posts, post_for_session
        profile, top, _problem = _profile(root)
        post = post_for_session(load_posts(top, project=fleet_name(top, profile)), who)
    except Exception:  # noqa: BLE001 - a post unread is a plain run, never a refused one
        return "run"
    return "review" if post is not None and post.name == REVIEWER_POST else "run"


def pinned_for(profile: dict, purpose: str, will_run) -> bool:
    """A receipt is never frozen when a tier it runs says `freeze = false` (section 4.6): a test that times itself
    against a clock, or holds a lease, would fail for the freeze, not for the code."""
    from flotilla.ledger.receipts import tiers_for
    names = set(will_run or ())
    return any(tier.get("freeze") is False for tier in tiers_for(profile, purpose) if tier.get("name") in names)


def value_for(profile: dict):
    """What a grant by value reads, built from each poll's reading: settings read once here, memory and swap read
    when the grant decides, inside the journal's lock."""
    from flotilla.core import resources
    from flotilla.lane import admission, freeze, peak
    cpus, floor = machine.cpus(), machine.memory_floor_mb(profile)
    kind = freeze.backend(setting=freeze_on())[0]
    reserve, immortal, cap, factor = light_reserve(), immortal_minutes() * 60.0, max_runs(), cpu_factor()
    root = paths.state_dir() / "lane"

    def make(reading=None):
        return admission.Value(cpus=cpus, cpu_factor=factor, light_reserve=reserve, immortal_s=immortal,
                               max_runs=cap, freeze=kind, available_mb=resources.available_mb, floor_mb=floor,
                               swap_mb=freeze.swap_mb, tree_mb=peak.tree_rss_mb,
                               beat_age_s=lambda booking: admission.beat_age(root, booking),
                               foreign=len(reading.computing) if reading is not None else 0)
    return make


def room_for(profile: dict):
    """What a light run may take, built from each poll's reading: this machine's cores, the factor, the memory
    available less the profile's floor (read when the grant decides, inside the lock), the foreign runs computing."""
    from flotilla.core import resources
    cpus = machine.cpus()
    floor, factor = machine.memory_floor_mb(profile), cpu_factor()

    def memory_left():
        available = resources.available_mb()
        return None if available is None else available - floor

    return lambda reading: book.Room(cpus=cpus, factor=factor, memory_left=memory_left,
                                     foreign=len(reading.computing))


def share() -> str:
    """machine.toml `lane_share`: `fair`, or first come, first served for anything else, unset, or unreadable."""
    value = _machine().get("lane_share", "fifo")
    return value if value in book.SHARES else "fifo"


def _share_line() -> str:
    value = _machine().get("lane_share", "fifo")
    if value in book.SHARES:
        return f"share: {value} (machine.toml lane_share)"
    return (f"share: fifo - machine.toml lane_share = {visible(repr(value))} is not understood (fifo or fair), so "
            "first come, first served")


def _lanes(table) -> book.Book:
    return book.Book(LocalLogStore(paths.state_dir() / "lane"), table)


def _profile(root) -> tuple[dict, Path, str]:
    """The profile, the repository root, and why the profile could not be read ("" when it was, or there is none)."""
    from flotilla.ledger.commands import trunk_rules
    try:
        top = repo.identify(Path(root)).root
    except repo.NotARepository:
        return {}, Path(root), ""
    if config.find_project(Path(root)) is None:
        return {}, top, ""
    try:
        return trunk_rules(Path(root)).profile, top, ""
    except (config.ConfigError, PostError) as err:
        return {}, top, str(err)


#: A run's ceiling when nothing was measured and the profile names none.
CEILING_UNMEASURED = 1800
#: Never under ten minutes: a ceiling exists for a run that will not end, not for a slow one.
CEILING_FLOOR = 600


def ceiling(profile: dict, measured: dict, *, tiers: bool = False) -> int:
    """Seconds a run in the lane may take (worldcore field test W21): the profile's `[lane] max_run_seconds`; else ten
    times the slowest tier measured on this machine - never under ten minutes for a receipt, whose commands are those
    tiers, and never under half an hour for any other run, which no measurement describes; else half an hour."""
    named = (profile.get("lane") or {}).get("max_run_seconds")
    if isinstance(named, (int, float)) and not isinstance(named, bool) and named > 0:
        return int(named)
    slowest = max((float(v) for v in measured.values() if isinstance(v, (int, float))), default=0.0)
    if not slowest:
        return CEILING_UNMEASURED
    return max(CEILING_FLOOR if tiers else CEILING_UNMEASURED, int(slowest * 10))


def ceiling_for(root, profile: dict, *, tiers: bool = False) -> int:
    from flotilla.onboard.firstrun import load_measurements
    try:
        measured = load_measurements(paths.state_dir(), repo.identify(Path(root)).key)
    except (repo.NotARepository, OSError, ValueError):
        measured = {}
    return ceiling(profile, measured, tiers=tiers)


def _who(as_name) -> str:
    try:
        return resolve_actor({}, as_name=as_name).name
    except ActorMismatch as err:   # the census names someone else: never their word (review of 0.6.2, I2)
        raise MoveRefused(str(err)) from err
    except (ActorUnknown, MoveRefused):
        return as_name or "outside any listed session"


def _reader(lanes, table, profile, root, problem=""):
    return lambda: machine.read(lanes, table, profile, own_pid=os.getpid(), root=root, problem=problem)


def _on_signals() -> None:
    def leave(number, _frame):
        raise SystemExit(128 + number)
    for number in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(number, leave)


def _project(root) -> str:
    try:
        return repo.identify(Path(root)).key
    except repo.NotARepository:
        return ""


@contextlib.contextmanager
def booked(root, *, note: str, wait: float, run_for: str = "", as_name=None, command: list[str] | None = None,
           will_run: list[str] | None = None, purpose: str = ""):
    """Book the lane around a run. A command, or a receipt's tiers, gives the booking its full text and its ladder
    of signatures, so the run's measurement joins the history of its command (lane admission, stage 1)."""
    table = ProcessTable.for_machine()
    lanes = _lanes(table)
    profile, top, problem = _profile(root)
    project = _project(root)
    text, ladder = "", []
    if will_run is not None:
        text = f"receipt {purpose}: " + ", ".join(will_run)
        ladder = signature.receipt_ladder(purpose, list(will_run), project=project)
    elif command:
        text = shlex.join(command)
        ladder = signature.ladder(list(command), tree=str(Path(root).resolve()), project=project)
    who = _who(as_name)
    with acq.held(lanes, _reader(lanes, table, profile, top, problem), who=who, note=note,
                  capacity=capacity, share=share, wait=wait, table=table, run_for=run_for, say=print, command=text,
                  ladder=ladder, will_run=list(will_run or []), project=project, room=room_for(profile),
                  value=value_for(profile), kind=kind_of(purpose, who, root),
                  pinned=pinned_for(profile, purpose, will_run)) as grant:
        yield grant


def _for_how_long(since: str) -> str:
    try:
        began = dt.datetime.fromisoformat(since)
    except (TypeError, ValueError):
        return ""
    minutes = int((dt.datetime.now(dt.timezone.utc) - began).total_seconds() // 60)
    return f" ({minutes} min)"


def _size(mb) -> str:
    if mb is None:
        return "? memory"
    return f"{mb / 1024:.1f} GB" if mb >= 1024 else f"{mb} MB"


def describe_estimate(item, hist) -> str:
    """A booking's estimate as the person reads it, with where it comes from (lane admission, stage 1)."""
    if not item.ladder and not item.will_run:
        return "estimate: none (an older flotilla booked it)" if item.rule is None else "estimate: none (taken by hand)"
    found = estimate.for_booking(item, hist)
    seconds = "? s" if found.seconds is None else f"{found.seconds:.0f} s"
    cores = "? cores" if found.cores is None else f"{found.cores:g} cores"
    return f"estimate: {seconds}, {cores}, {_size(found.peak_mb)} ({found.source})"


def _foreign(table, proc, tail: str) -> str:
    where = visible(table.cwd(proc.pid) or "an unknown directory")
    return f"foreign run pid {proc.pid} `{visible(proc.command[:80])}` in {where}{tail}"


def _ordinal(number: int) -> str:
    suffix = "th" if number % 100 in (11, 12, 13) else {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")
    return f"{number}{suffix}"


def _gb(mb) -> str:
    return "?" if mb is None else f"{mb / 1024:.1f}"


def _clock(moment: dt.datetime) -> str:
    return moment.astimezone().strftime("%H:%M")


def value_of(run, item, machine, now: dt.datetime) -> str:
    """A run's place by value, as `flotilla lane` prints it (lane shared machine design, section 5)."""
    from flotilla.lane import policy
    type_part, wait_part, run_part = policy.WEIGHTS
    parts = (type_part * policy.TYPES.get(run.kind, policy.TYPES["run"]),
             wait_part * min(1.0, run.waited_s / policy.WAIT_FULL_S),
             run_part * min(1.0, run.ran_s / machine.immortal_s if machine.immortal_s else 1.0))
    text = f"P {sum(parts):.2f} (type {parts[0]:.2f}, wait {parts[1]:.2f}, run {parts[2]:.2f})"
    size = "heavy" if run.heavy else "light"
    if run.state == policy.WAITING:
        text += f"; {size} (needs {_gb(run.mem_mb)} GB)"
    else:
        text += f"; {size} (memory now {_gb(run.mem_mb)} GB)"
    if run.pinned:
        text += "; never frozen"
    elif run.state in (policy.RUNNING, policy.ORDERED):
        left = machine.immortal_s - run.ran_s
        text += "; immortal" if left <= 0 else f"; immortal from {_clock(now + dt.timedelta(seconds=left))}"
    if item.frozen == "frozen":
        since = book._when(item.frozen_at)
        text += f"; frozen since {_clock(since) if since else '?'} by {visible(item.order_by)}'s order"
    elif item.frozen == "ordered":
        text += f"; ordered frozen by {visible(item.order_by)}, not yet answered"
    if item.unanswered:
        text += f"; {item.id} did not answer a freeze order"
    return text


def _status(args) -> int:
    table = ProcessTable.for_machine()
    lanes = _lanes(table)
    profile, top, problem = _profile(args.root)
    # a dead booking blocks nobody since grant skips it, but it misleads whoever reads this; only a process that does
    # not exist is swept here - one failed look (a `ps` that did not answer) must not release a live holder for good
    try:
        swept = lanes.sweep(gone=lambda item: table.exists(item.pid) is False)
    except OSError as err:
        swept = []
        print(f"could not sweep bookings whose process is gone: {err}")
    if swept:
        print(f"swept {len(swept)} booking(s) whose process is gone: " + ", ".join(visible(item.id) for item in swept))
    slots, mode = capacity(), share()
    found = lanes.bookings()   # read once: each waiter's reason would read the whole journal again
    held, waiting = lanes.holders(found), lanes.waiters(found)
    reading = _reader(lanes, table, profile, top, problem)()
    now = dt.datetime.now(dt.timezone.utc)
    hist = estimate.history(found, now=now)
    room = room_for(profile)(reading)
    counted = book.older_waiting(found, lanes.live)   # an older flotilla waits: every run is counted, none is light
    if not counted:
        return _status_by_value(lanes, found, held, waiting, reading, now, hist, profile, mode)
    print(f"capacity: {slots} heavy run(s) at a time (machine.toml lane_capacity); light runs take no place")
    print(_share_line())
    used, total, reserved = book.budget(found, room, hist, lanes.live)
    free = room.memory_left()
    memory = "memory cannot be read" if free is None else f"{max(free - reserved, 0)} MB of memory left after the " \
        f"holders' peaks"
    print(f"budget for light runs: {used:g} of {total:g} cores (lane_cpu_factor {room.factor:g}), {memory} "
          f"(floor {machine.memory_floor_mb(profile)} MB); a run measured at <= {estimate.LIGHT_SECONDS:g} s and "
          f"<= {estimate.LIGHT_CORES:g} cores is light and takes no place")
    print("held:" if held or reading.computing else "held: nobody")
    for item in held:
        state = "alive" if lanes.live(item) else "its process is gone - `flotilla lane sweep`"
        by = f"pid {item.pid}" if item.pid else "taken by hand - `flotilla lane release`"
        target = f", for {item.run_for}" if item.run_for else ""
        kind = "light" if item.path.startswith("light") else "heavy"
        print(f"  {item.id} {kind} {item.who} ({item.note or 'no note'}) since {item.since}"
              f"{_for_how_long(item.since)}{target}; {by}; {state}; {describe_estimate(item, hist)}")
    for proc in reading.computing:
        print("  " + _foreign(table, proc, " (takes a slot, no booking)"))
    if mode == "fair":
        now = dt.datetime.now(dt.timezone.utc)
        served = book.last_served(found, now)
        for project in sorted({item.project for item in waiting if lanes.live(item)}):
            count = sum(1 for item in waiting if item.project == project and lanes.live(item))
            holds = sum(1 for item in held if item.project == project and lanes.live(item))
            print(f"project {visible(project) or '(none)'}: {count} waiting, holds {holds}, "
                  f"last turn {served.get(project) or 'not in 24 h'}")
    print("waiting:" if waiting else "waiting: nobody")
    starts = book.heavy_starts(found, hist, slots=slots - len(reading.computing), now=now, live=lanes.live,
                               ceiling=ceiling_for(args.root, profile), cpus=room.cpus, counted=counted)
    order = {item_id: place for place, item_id in enumerate(starts, start=1)}
    if counted:
        print("  a seat of a flotilla before 0.30 waits: lane_capacity counts every run until it goes")
    for item in waiting:
        gone = "" if lanes.live(item) else "; its process is gone - `flotilla lane sweep`"
        if not counted and book.is_light(item, hist, room.cpus):
            kind, why = "light", book.light_verdict(found, item, room, hist, lanes.live) or "next to start"
        else:
            kind, why = "heavy", acq.in_the_way(lanes, reading, slots, item.id, found, share=mode)
            if item.id in order:
                when = starts[item.id]
                why += (f"; {_ordinal(order[item.id])} of the heavy runs; " +
                        (f"by estimates it starts around {when.astimezone().strftime('%H:%M')} or sooner"
                         if when is not None else "no place for a heavy run now"))
        print(f"  {item.id} {kind} {item.who} ({item.note or 'no note'}) since {item.since}{gone}; "
              f"{describe_estimate(item, hist)}; waits because: {why}")
    _machine_answers(table, reading)
    return 0


def _status_by_value(lanes, found, held, waiting, reading, now, hist, profile, mode) -> int:
    """`flotilla lane` while every waiter goes by value: what the machine gives, each run's priority, and why each
    waiter waits (lane shared machine design, section 5)."""
    from flotilla.lane import admission, freeze, policy
    value = value_for(profile)(reading)
    runs, m = admission.view(found, hist, value, now=now, live=lanes.live, mine="", share=mode)
    by_id = {run.id: run for run in runs}
    kind, why = freeze.backend(setting=freeze_on())
    swap = value.swap_mb()
    print(f"machine: {m.cpus} cores shared by the kernel; memory the lane can give {_gb(m.give_mb)} GB, "
          f"{_gb(m.free_mb)} GB free now; swap {_gb(swap[0]) if swap else '?'} GB free; "
          f"freezing: {kind or 'off'} ({why})")
    heavies = [run for run in runs if run.heavy and run.state in (policy.RUNNING, policy.ORDERED)]
    room = None if m.give_mb is None else (1 - m.light_reserve) * m.give_mb
    print(f"heavy runs: {sum(run.par for run in heavies):g} of {m.cpu_factor * m.cpus:g} cores in use "
          f"(lane_cpu_factor {m.cpu_factor:g}), {_gb(sum(run.mem_mb for run in heavies))} of {_gb(room)} GB "
          f"(light reserve {round(m.light_reserve * 100)} %); at most {m.max_runs} runs at once")
    if "lane_capacity" in _machine():
        print(f"lane_capacity = {visible(repr(_machine()['lane_capacity']))} in machine.toml no longer has an effect "
              "(0.30); runs share the machine by memory and priority")
    print(_share_line())
    print("held:" if held or reading.computing else "held: nobody")
    live_held = [item for item in held if lanes.live(item)]
    for item in held:
        state = "alive" if lanes.live(item) else "its process is gone - `flotilla lane sweep`"
        by = f"pid {item.pid}" if item.pid else "taken by hand - `flotilla lane release`"
        target = f", for {item.run_for}" if item.run_for else ""
        size = ("heavy" if by_id[item.id].heavy else "light") if item.id in by_id else "heavy"
        worth = f"; {value_of(by_id[item.id], item, m, now)}" if item.id in by_id else ""
        print(f"  {item.id} {size} {item.who} ({item.note or 'no note'}) since {item.since}"
              f"{_for_how_long(item.since)}{target}; {by}; {state}; {describe_estimate(item, hist)}{worth}")
    for proc in reading.computing:
        print("  " + _foreign(lanes.procs, proc, " (foreign: its memory is simply not free)"))
    if mode == "fair":
        served = book.last_served(found, now)
        for project in sorted({item.project for item in waiting if lanes.live(item)}):
            count = sum(1 for item in waiting if item.project == project and lanes.live(item))
            holds = sum(1 for item in live_held if item.project == project)
            print(f"project {visible(project) or '(none)'}: {count} waiting, holds {holds}, "
                  f"last turn {served.get(project) or 'not in 24 h'}")
    print("waiting:" if waiting else "waiting: nobody")
    holding = "; ".join(f"{item.who} ({item.note or 'no note'}) since {item.since}" for item in live_held)
    for item in waiting:
        gone = "" if lanes.live(item) else "; its process is gone - `flotilla lane sweep`"
        if item.id in by_id:
            decided = policy.decide(runs, m, item.id, may_freeze=False)
            because = "next to start" if decided.start else decided.why
            if holding and not decided.start:
                because += f"; held by {holding}"
            size = "heavy" if by_id[item.id].heavy else "light"
            print(f"  {item.id} {size} {item.who} ({item.note or 'no note'}) since {item.since}{gone}; "
                  f"{describe_estimate(item, hist)}; {value_of(by_id[item.id], item, m, now)}; "
                  f"waits because: {because}")
        else:
            print(f"  {item.id} {item.who} ({item.note or 'no note'}) since {item.since}{gone}")
    _machine_answers(lanes.procs, reading)
    return 0


def _machine_answers(table, reading) -> None:
    print("machine:")
    for answer in reading.answers:
        flag = {True: "busy", False: "ok", None: "unknown"}[answer.blocks]
        print(f"  {answer.question}: {flag} - {answer.text}")
    for proc in reading.computing:
        print("  " + _foreign(table, proc, ", computing"))
    for proc in reading.idle:
        print("  " + _foreign(table, proc, ", idle"))


def _take(args) -> int:
    table = ProcessTable.for_machine()
    lanes = _lanes(table)
    profile, top, problem = _profile(args.root)
    _on_signals()
    pid = os.getpid()
    grant = acq.acquire(lanes, _reader(lanes, table, profile, top, problem), who=_who(args.as_name), note=args.note,
                        capacity=capacity, share=share, wait=args.wait, pid=pid, mark=table.start_mark(pid) or "",
                        by_hand=True, say=print, project=_project(args.root), room=room_for(profile),
                        value=value_for(profile))
    if grant.booking is None:
        print(f"refused: {grant.why}")
        return 2
    print(f"the lane is yours: booking {grant.booking.id}; release it with `flotilla lane release` when done")
    return 0


def _release(args) -> int:
    lanes = _lanes(ProcessTable.for_machine())
    who = _who(args.as_name)
    if args.booking is not None:
        item = lanes.bookings().get(args.booking)
        if item is not None and item.state == book.WAITING:
            lanes.leave(item.id)
            print(f"ended {item.id}: {item.who} no longer waits")
            return 0
    held = [item for item in lanes.holders() if args.booking in (None, item.id)]
    if args.booking is None:
        held = [item for item in held if item.who == visible(who)]   # who is stored visible (F2)
    if not held:
        print(f"refused: no booking held by {who}" if args.booking is None else f"refused: {args.booking} is not held")
        return 2
    if len(held) > 1:
        print("refused: several bookings match; name one with --booking: " + ", ".join(item.id for item in held))
        return 2
    lanes.release(held[0].id, why=f"released by {who}")
    print(f"released {held[0].id}")
    return 0


def _sweep(args) -> int:
    swept = _lanes(ProcessTable.for_machine()).sweep()
    if not swept:
        print("nothing to sweep: every booking has its process, or was taken by hand")
    for item in swept:
        print(f"swept {item.id} {item.who} ({item.note or 'no note'}): no process behind it")
    return 0


def _where(tree: Path) -> tuple[str, str, bool | None]:
    """The tree's branch, its HEAD and whether it is clean, asked before the command runs."""
    done = subprocess.run(["git", "-C", str(tree), "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True,
                          text=True, check=False)
    return (done.stdout.strip() if done.returncode == 0 else "", gitq.resolve(tree, "HEAD") or "",
            gitq.is_clean(tree))


def _record(args, result, where, started: str) -> None:
    from flotilla.ledger.commands import open_ledger
    branch, revision, clean = where
    if branch != args.for_:
        print(f"note: the run was not recorded on the ledger: the tree is on {branch or 'no branch'}, not "
              f"{args.for_}, so the result is not about that branch")
        return
    try:
        ledger = open_ledger(Path(args.tree))
        who = resolve_actor(ledger.posts, as_name=args.as_name)
        runs.record_run(ledger, who, args.for_, verdict=result.verdict, summary=result.summary, revision=revision,
                        dirty=clean is not True,
                        evidence={"exit": result.exit, "verdict": result.verdict, "summary": result.summary,
                                  "revision": revision, "clean": clean, "signal": result.signal,
                                  "started": started})
    except (MoveRefused, PostError, config.ConfigError, repo.NotARepository, StorageCorrupt,
            LedgerVersionError) as err:
        print(f"note: the run was not recorded on the ledger: {err}")


def _run(args) -> int:
    command = list(args.run_command)
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        print("refused: name the command after --, for example `flotilla lane run -- uv run pytest`")
        return 2
    _on_signals()
    where, started = None, None
    try:
        with booked(args.tree, note=args.note or " ".join(command)[:80], wait=args.wait, run_for=args.for_ or "",
                    as_name=args.as_name, command=command) as grant:
            where = _where(Path(args.tree))
            started = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
            limit = args.max if args.max else ceiling_for(args.tree, _profile(args.tree)[0])
            began = time.monotonic()
            try:
                result = runner.execute(command, cwd=args.tree, ceiling=limit)
            except BaseException:   # stopped: it held the lane all the same, and says so on release
                grant.measured.update({"seconds": round(time.monotonic() - began, 2), "verdict": "killed"})
                raise
            if result.usage is not None:
                grant.measured.update({"seconds": result.usage.seconds, "peak_mb": result.usage.peak_mb,
                                       "cores": result.usage.cores, "busy": result.usage.busy,
                                       "peak_cores": result.usage.peak_cores,
                                       "verdict": "ceiling" if result.at_ceiling else result.verdict})
    except acq.LaneRefused as err:
        print(f"refused: {err}")
        return 2
    except SystemExit as stop:   # `lane stop`, or the session ending: the command's group was stopped first
        if started is None:
            raise
        result = runner.RunResult(stop.code if isinstance(stop.code, int) else 143, "killed",
                                  "stopped by a signal (`flotilla lane stop`, or the session ending) - no verdict",
                                  15)
    print(verdict_line(result))
    if args.for_:
        _record(args, result, where, started)
    return result.exit


def _stop_run(args) -> int:
    """Stop the caller's own run in the lane by the pid flotilla recorded, so nobody has to find it with `pkill`
    (worldcore field test W22: a seat's `pkill -f` matched its own command line and killed itself first)."""
    lanes = _lanes(ProcessTable.for_machine())
    who = _who(args.as_name)
    held = [item for item in lanes.holders() if item.pid and args.booking in (None, item.id)]
    mine = [item for item in held if item.who == visible(who)]   # who is stored visible (F2)
    if not mine:
        others = ", ".join(f"{item.id} ({item.who})" for item in held)
        print(f"refused: no run in the lane is {who}'s" + (f"; held: {others} - ask its holder" if others else ""))
        return 2
    if len(mine) > 1:
        print("refused: several runs are yours; name one with --booking: " + ", ".join(item.id for item in mine))
        return 2
    item = mine[0]
    if not lanes.live(item):
        print(f"{item.id}'s process is already gone; `flotilla lane sweep` clears the booking")
        return 0
    os.kill(item.pid, signal.SIGTERM)   # the run stops its command's whole group and releases the lane itself
    for _ in range(100):
        if not lanes.live(item):
            print(f"stopped {item.id} ({item.note or 'no note'}) with everything it started: a `lane run` is recorded "
                  "as killed, with no verdict; a receipt issues none")
            return 0
        time.sleep(0.1)
    print(f"sent the stop to {item.id} (pid {item.pid}); it has not ended within 10 s - `flotilla lane` shows it")
    return 1


def run_lane_command(args) -> int:
    try:
        return {None: _status, "status": _status, "take": _take, "release": _release, "run": _run, "sweep": _sweep,
                "stop": _stop_run}[args.action](args)
    except (StorageCorrupt, config.ConfigError, MoveRefused) as err:
        print(f"refused: {err}")
        return 2
