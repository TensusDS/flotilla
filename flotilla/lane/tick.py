"""One pure decision over the whole lane, taken by the lane daemon each tick (lane daemon design, section 4.3).

Starts go by the waiters' rules of 0.30 (`policy.decide`), with free memory read as the room every ceiling leaves.
Every run then lives under a soft ceiling that grows while there is room. A run that churns at its ceiling with no
room to give it, or a machine below its floor, has its lowest-priority run squeezed (a lower ceiling: its cold pages
go to swap and it keeps running) and then frozen - never killed. One victim at a time; the swap budget limits every
move that pushes pages out.

Nothing here reads the machine or the clock: every timer arrives measured on the runs and the machine
(`scheduler.History`), so a tick replays from a record.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass

from flotilla.lane import policy
from flotilla.lane.policy import FROZEN, ORDERED, RUNNING, WAITING, Machine, Run

#: Pressure ("some avg10", percent) at or above which a run held at its ceiling is pressed: it stalls on memory
#: instead of working. Builder's number until the bench re-run measures the hot and cold profiles (plan, Task 11).
PRESSED = 50.0
#: How long a run must stay pressed, and the machine below its floor, before the daemon acts. Builder's numbers;
#: await the field test (lane daemon design, section 7).
PRESSED_S = 10.0
OVER_S = 10.0
#: A run frozen for an overload or a pressure stands frozen at least this long: thawed sooner it would only fault its
#: pages back and be frozen again. Builder's number.
THAW_AFTER_S = 30.0


@dataclass(frozen=True)
class Actions:
    starts: tuple[tuple[str, bool, float], ...] = ()      # (id, heavy, P)
    ceilings: tuple[tuple[str, int | None], ...] = ()     # (id, MB); None lifts it to max
    squeezed: tuple[str, ...] = ()                        # ids whose entry in `ceilings` is a squeeze
    restored: tuple[str, ...] = ()                        # squeezed runs given back used + slack
    orders: tuple[tuple[str, int, str, str], ...] = ()    # (id, reclaim MB, cause, the waiter's or pressed run's id)
    thaws: tuple[str, ...] = ()
    why: tuple[tuple[str, str], ...] = ()                 # (id, why it does not move)
    over_mb: int = 0                                      # over the floor and nothing can be taken (ruling 8)


def _promised(ceiling: int | None, mem: int) -> int:
    return max(0, ceiling - mem) if ceiling is not None else 0


def _swap_room(machine: Machine) -> int:
    """What may still be pushed to swap within the budget; 0 when the swap cannot be read."""
    if machine.swap_free_mb is None or not machine.swap_total_mb:
        return 0
    used = machine.swap_total_mb - machine.swap_free_mb + machine.swap_pending_mb
    return max(0, int(policy.SWAP_USED_CAP * machine.swap_total_mb - used))


class _Tick:
    def __init__(self, runs: list[Run], machine: Machine):
        self.runs, self.m = runs, machine
        self.p = {r.id: policy.priority(r, machine.immortal_s) for r in runs}
        self.ceiling = {r.id: r.ceiling_mb for r in runs}
        self.space = policy.room(runs, machine)
        self.known = machine.available_mb is not None and self.space is not None
        self.changed: dict[str, int | None] = {}
        self.squeezed: list[str] = []
        self.restored: list[str] = []
        self.orders: list[tuple[str, int, str, str]] = []
        self.starts: list[tuple[str, bool, float]] = []
        self.thaws: list[str] = []
        self.why: dict[str, str] = {}
        self.over_mb = 0

    def set_ceiling(self, r: Run, mb: int | None) -> None:
        old = self.ceiling[r.id]
        if old == mb:
            return
        if self.space is not None and r.state in (RUNNING, ORDERED):
            self.space += _promised(old, r.mem_mb) - _promised(mb, r.mem_mb)
        self.ceiling[r.id] = mb
        self.changed[r.id] = mb

    def order(self, r: Run, reclaim: int, cause: str, waiter: str = "") -> bool:
        if not self.m.freeze:
            return False
        self.orders.append((r.id, max(0, int(reclaim)), cause, waiter))
        return True

    # 1. a run that became immortal, or is pinned, is never held by a ceiling: a ceiling under what it would use is a
    #    hidden freeze, and nothing freezes it
    def lift(self) -> None:
        for r in self.runs:
            if r.state != WAITING and self.ceiling[r.id] is not None and \
                    (r.pinned or policy.immortal(r, self.m.immortal_s)):
                self.set_ceiling(r, None)

    def over(self) -> bool:
        return self.known and self.m.available_mb < self.m.floor_mb

    # 2. squeezed runs get their room back before any new run starts
    def restore(self) -> bool:
        """True when starts must wait this tick."""
        if self.over():
            return False
        hold = False
        for r in sorted((r for r in self.runs if r.squeezed and r.state == RUNNING), key=lambda r: -self.p[r.id]):
            hold = True
            target = r.mem_mb + policy.slack(r.mem_mb)
            need = _promised(target, r.mem_mb) - _promised(self.ceiling[r.id], r.mem_mb)
            if self.space is not None and self.space >= need:
                self.set_ceiling(r, target)
                self.restored.append(r.id)
        return hold

    def busy(self) -> bool:
        """An order not yet answered, or a squeeze whose memory has not yet shown: one victim at a time."""
        return any(r.state == ORDERED for r in self.runs) or bool(self.orders) or any(
            r.squeezed and self.ceiling[r.id] is not None and r.mem_mb > self.ceiling[r.id] + policy.slack(
                self.ceiling[r.id]) for r in self.runs if r.state == RUNNING)

    # 3. the machine below its floor for OVER_S: squeeze the victim, freeze it when that does not hold
    def overload(self) -> bool:
        """True when the machine is over its floor: nothing starts this tick."""
        if not self.over():
            return False
        if self.m.over_s < OVER_S:
            return True
        deficit = self.m.floor_mb - self.m.available_mb
        candidates = [r for r in self.runs if not policy.daemon_reason(r, self.m)]
        if not candidates:
            self.over_mb = deficit
            return True
        if self.busy():
            return True
        pressed = [r for r in candidates if r.squeezed and r.pressed_s >= PRESSED_S]
        swap = _swap_room(self.m)
        if pressed:
            v = policy.victim(pressed, self.m.immortal_s)
            self.order(v, min(deficit + policy.RECLAIM_MARGIN_MB, v.mem_mb, swap), policy.OVER)
            return True
        fresh = [r for r in candidates if not r.squeezed]
        v = policy.victim(fresh or candidates, self.m.immortal_s)
        if not v.scoped or swap <= 0:   # nothing to squeeze, or the swap can take nothing: frozen in RAM (ruling 7)
            self.order(v, 0, policy.OVER)
            return True
        push = min(v.mem_mb - max(policy.MIN_CEILING_MB, v.mem_mb - deficit), swap)
        self.set_ceiling(v, v.mem_mb - push)
        self.squeezed.append(v.id)
        return True

    # 4. a run that churns at its ceiling with no room to give it waits for memory, with its own priority
    def pressure(self) -> None:
        if not self.known or self.busy() or self.squeezed:
            return
        if self.space >= policy.MIN_CEILING_MB:
            return
        pressed = [r for r in self.runs if r.state == RUNNING and self.ceiling[r.id] is not None
                   and r.pressed_s >= PRESSED_S and not policy.daemon_reason(r, self.m)]
        if not pressed:
            return
        p = max(pressed, key=lambda r: self.p[r.id])
        lower = [r for r in self.runs if r.id != p.id and self.p[r.id] < self.p[p.id]
                 and not policy.daemon_reason(r, self.m)]
        v = policy.victim([p, *lower], self.m.immortal_s)
        reclaim = 0 if v.id == p.id else min(v.mem_mb, self.ceiling[p.id], _swap_room(self.m))
        self.order(v, reclaim, policy.PRESSED_CAUSE, p.id)   # the wrapper checks that this run reads pressed

    def decide_machine(self) -> Machine:
        return self.m if self.space is None else dataclasses.replace(self.m, free_mb=self.space)

    # 5. starts, by the waiters' rules of 0.30
    def start(self) -> None:
        machine = self.decide_machine()
        waiters = sorted((r for r in self.runs if r.state == WAITING), key=lambda r: (-self.p[r.id], r.fair, r.order))
        taken = bool(self.orders)
        for w in waiters:
            if w.rule < 5:
                self.why[w.id] = "an older flotilla's booking: it decides for itself"
                continue
            d = policy.decide(self.runs, machine, w.id, may_freeze=not taken)
            if d.start:
                self.starts.append((w.id, w.heavy, self.p[w.id]))
                ceiling = policy.start_ceiling(w.mem_mb)
                self.ceiling[w.id] = ceiling
                self.changed[w.id] = ceiling
                if self.space is not None:
                    self.space -= ceiling
            elif d.freeze and not taken:
                for victim_id, mb in d.freeze:
                    self.orders.append((victim_id, mb, policy.WAITER, w.id))
                taken = True
                self.why[w.id] = d.why
            else:
                self.why[w.id] = d.why

    # 6. a run at its ceiling grows while there is room, by priority, up to doubling each tick
    def grow(self) -> None:
        if self.space is None:
            return
        for r in sorted(self.runs, key=lambda r: -self.p[r.id]):
            if self.space <= 0:
                break
            ceiling = self.ceiling[r.id]
            if r.state not in (RUNNING, ORDERED) or ceiling is None or not r.at_ceiling or r.squeezed:
                continue
            self.set_ceiling(r, ceiling + min(ceiling, self.space))

    # 7. thaws: a waiter's freeze by the waiters' rules, the daemon's own when room covers what the run will take
    def thaw(self) -> None:
        machine = self.decide_machine()
        for r in sorted((r for r in self.runs if r.state == FROZEN), key=lambda r: -self.p[r.id]):
            if r.cause in (policy.OVER, policy.PRESSED_CAUSE):
                why = self._daemon_thaw(r)
                if why:
                    self.why[r.id] = why
                continue
            d = policy.decide(self.runs, machine, r.id, may_freeze=False)
            if d.start:
                self.thaws.append(r.id)
            else:
                self.why[r.id] = d.why

    def _daemon_thaw(self, r: Run) -> str:
        if self.space is None:
            return "memory cannot be read"
        if self.over():
            return "the machine is over its floor"
        if r.frozen_s < THAW_AFTER_S:
            return f"frozen {r.frozen_s:.0f} s of at least {THAW_AFTER_S:.0f}"
        ceiling = self.ceiling[r.id]
        headroom = (ceiling if r.cause == policy.PRESSED_CAUSE and ceiling is not None
                    else policy.slack(ceiling if ceiling is not None else r.mem_mb))
        need = headroom + r.reclaimed_mb
        if r.held_freezes >= 2:
            need = max(need, r.peak_mb - r.mem_mb)
        if self.space < need:
            return f"memory: {need} MB to come back, {max(0, self.space)} MB of room"
        self.space -= need
        self.thaws.append(r.id)
        return ""

    def actions(self) -> Actions:
        return Actions(starts=tuple(self.starts), ceilings=tuple(self.changed.items()),
                       squeezed=tuple(self.squeezed), restored=tuple(self.restored), orders=tuple(self.orders),
                       thaws=tuple(self.thaws), why=tuple(self.why.items()), over_mb=self.over_mb)


def tick(runs: list[Run], machine: Machine) -> Actions:
    """The lane daemon's decision for this tick, in the order the design gives (4.3): ceilings of the untouchable
    lifted, squeezed runs restored, the overload, the pressure, the starts, the growth, the thaws."""
    t = _Tick(runs, machine)
    t.lift()
    hold = t.restore()
    over = t.overload()
    if not over:
        t.pressure()
    if t.over_mb:
        for r in runs:
            if r.state in (WAITING, FROZEN):
                t.why[r.id] = (f"the machine is over its floor by {t.over_mb} MB and nothing can be frozen: every run "
                               "over the line is immortal or pinned")
    elif not over and not hold:
        t.start()
    else:
        for r in runs:
            if r.state == WAITING:
                t.why.setdefault(r.id, "the machine is over its floor" if over else "a squeezed run gets its room "
                                 "back first")
    t.grow()
    t.thaw()
    return t.actions()
