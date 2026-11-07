"""The holder's watch: the thread in a `lane run` or a receipt that obeys freeze orders on its own booking, and asks
to thaw through the queue (lane shared machine design, section 4.1).

It wakes on journal writes, as a waiter does, and looks at least every few seconds. On an order it freezes its
command and records it; while frozen it asks, as a waiter would, whether it may come back. A freeze held for nothing -
its waiter never started, because the memory never showed free or the waiter was never real - is let go and never
ordered again, whether it was a scope or a stop by signal. Each
look beats, so others can tell a hung wrapper from a live one. If the watch itself fails, it thaws its command
first: a broken watch must never leave a command frozen.
"""

from __future__ import annotations

import sys
import threading
import time
from collections.abc import Callable

from flotilla.lane import admission, book, freeze

LOOK_S = 5.0     # the longest a watch goes without a look
LET_GO_S = 120.0   # a freeze held this long while its waiter still waits is let go (builder's number)
#: An overload order is obeyed only while MemAvailable reads below the floor plus this: the wrapper reads the machine
#: for itself rather than take the journal's word (security review of c0b73f9). Builder's number.
OVER_MARGIN_MB = 512


class Watch:
    def __init__(self, lanes, booking_id: str, slot, value_of: Callable, *, clock=time.monotonic,
                 sleep=time.sleep, let_go_s: float = LET_GO_S, root=None, swap=None, groups_io=None):
        from flotilla.lane import groups
        self.lanes, self.booking_id, self.slot, self.value_of = lanes, booking_id, slot, value_of
        self.groups = groups_io or groups
        self.swap = swap or freeze.swap_mb
        self.clock, self.sleep, self.let_go_s = clock, sleep, let_go_s
        self.root = root if root is not None else getattr(lanes.store, "root", None)
        self.thread = threading.Thread(target=self._loop, daemon=True, name=f"lane watch {booking_id}")
        self._stop = threading.Event()
        self._frozen_since: float | None = None
        self._kind = ""

    def step(self) -> str:
        """One look: "froze", "thawed", "let go" or ""."""
        if self.root is not None:
            admission.beat(self.root, self.booking_id)
        item = self.lanes.bookings().get(self.booking_id)
        if item is None or item.state != book.HELD:
            if self.slot.frozen():   # released while frozen: nothing may stay frozen for a booking that ended
                self._thaw()
                return "thawed"
            return ""
        if getattr(self.slot, "done_freezing", False) and not self.slot.frozen() and \
                (item.frozen or not item.unanswered):
            # the command's own loop thawed it by itself - at the ceiling, or with this watch stuck: the record must
            # not go on saying frozen while the run computes, or every session would count its room as free
            # (review of d4c28c5; security review)
            self._frozen_since, self._kind = None, ""
            if self.lanes.thaw(self.booking_id, self.value_of(None), force=True, never_again=True) is None:
                self.lanes.unfreezable(self.booking_id)
            return "let go"
        if self.slot.frozen() and self._frozen_since is not None and \
                self.clock() - self._frozen_since >= self.value_of(None).freeze_ceiling_s:
            # no freeze outlasts its ceiling, whatever holds its room and whatever the record says now - a waiter
            # that turned into a take by hand, a forged one, a line that turned the record back to "ordered", or a
            # queue that never gives the room back (the person's ruling, 2026-11-06). Thawed first, the journal
            # after: its lock may be held by a process inside this very command (review of f32d91c)
            self.slot.done_freezing = True
            self._thaw()
            if self.lanes.thaw(self.booking_id, self.value_of(None), force=True, never_again=True) is None:
                self.lanes.unfreezable(self.booking_id)   # the record did not say frozen: it is never ordered again
            return "let go"
        if item.frozen == "ordered" and not self.slot.frozen():
            if not self._order_holds(item):
                self.lanes.refuse(self.booking_id, order_by=item.order_by, ordered_at=item.ordered_at)
                return "refused"
            self._mark(None)   # the freeze itself - systemctl, the kernel, the reclaim - is no journal call
            found = self.slot.freeze(self._reclaim(item.reclaim_mb or 0))
            self._mark(self._now())
            self._frozen_since, self._kind = self.clock(), found.get("freeze_kind", "")
            if self.lanes.frozen(self.booking_id, freeze_kind=found.get("freeze_kind", ""),
                                 unit=found.get("unit", ""), group=found.get("group"),
                                 held_mb=found.get("held_mb")) is None:
                self._thaw()   # the booking no longer holds: nothing may stay frozen for it
                return ""
            return "froze"
        if item.frozen == "frozen" and item.order_cause:
            return self._frozen_by_daemon(item)
        if item.frozen == "frozen":
            orderer = self.lanes.bookings().get(item.order_by)
            waits = orderer is not None and orderer.state == book.WAITING
            if waits and self._frozen_since is not None and \
                    self.clock() - self._frozen_since >= self.let_go_s:
                if self.lanes.thaw(self.booking_id, self.value_of(None), force=True, never_again=True,
                                   while_waits=item.order_by) is not None:
                    self.slot.done_freezing = True
                    self._thaw()
                    return "let go"
            if self.lanes.thaw(self.booking_id, self.value_of(None)) is not None:
                self._thaw()
                return "thawed"
            return ""
        if not item.frozen and self.slot.frozen():   # thawed from outside: a rescue by another grant
            self._thaw()
            return "thawed"
        return ""

    def _frozen_by_daemon(self, item) -> str:
        """Frozen by the daemon's order: the daemon thaws it (the record turns thawed, read above). The watch thaws by
        itself only when the daemon is gone - a dead daemon is a gone watcher (lane daemon design, section 6) - or a
        waiter's order outlasts the let-go bound; the freeze ceiling is guarded above, whatever the cause."""
        from flotilla.lane import daemon
        if self.root is not None and daemon.alive(self.root, self.lanes.procs) is None:
            if self.lanes.thaw(self.booking_id, self.value_of(None), force=True) is not None:
                self._thaw()
                return "thawed"
            return ""
        if item.order_cause == "waiter" and self._frozen_since is not None and \
                self.clock() - self._frozen_since >= self.let_go_s:
            orderer = self.lanes.bookings().get(item.order_by)
            if orderer is not None and orderer.state == book.WAITING and \
                    self.lanes.thaw(self.booking_id, self.value_of(None), force=True, never_again=True,
                                    while_waits=item.order_by) is not None:
                self.slot.done_freezing = True
                self._thaw()
                return "let go"
        return ""

    def _order_holds(self, item) -> bool:
        """Whether to obey an order on this booking. Any session writes the journal, so only an order the live lane
        daemon signed counts - its pid and start mark, matched against its heartbeat and the lock it holds now - and
        it must be fresh. A waiter's order then names a live waiter this run may be frozen for by the rules, its
        first minute included; an overload's or a pressure's needs no waiter (the person's ruling 5), but never
        freezes an immortal or pinned run whatever the daemon says. A run let go is never frozen again in this
        process, whatever a journal line says (review of f32d91c; lane daemon design, 4.8)."""
        import datetime as dt
        if getattr(self.slot, "done_freezing", False):
            return False
        from flotilla.lane import daemon, estimate, policy
        if self.root is None or not daemon.signed(self.root, self.lanes.procs, item):
            return False
        now = self.lanes._moment()
        given = book._when(item.ordered_at)
        if given is None or not dt.timedelta(0) <= now - given <= dt.timedelta(seconds=admission.ANSWER_S):
            return False
        found = self.lanes.bookings()
        if item.order_cause in ("over", "pressed"):
            value = self.value_of(None)
            runs, machine = admission.view(found, estimate.history(found, now=now), value, now=now,
                                           live=self.lanes.live, mine="")
            me = next((run for run in runs if run.id == self.booking_id), None)
            if me is None or me.pinned or policy.immortal(me, machine.immortal_s):
                return False
            return self._over_now(found, value) if item.order_cause == "over" else self._pressed_now(found, item)
        if item.order_cause != "waiter":
            return False
        waiter = found.get(item.order_by)
        # a waiter with a process and its start mark: one with neither reads as live for ever (review of 0.30.1, I1)
        if waiter is None or waiter.state != book.WAITING or not waiter.pid or not waiter.mark or \
                not self.lanes.live(waiter) or (waiter.rule or 0) < book.RULE:
            return False
        runs, machine = admission.view(found, estimate.history(found, now=now), self.value_of(None), now=now,
                                       live=self.lanes.live, mine=waiter.id)
        by_id = {run.id: run for run in runs}
        me, them = by_id.get(self.booking_id), by_id.get(waiter.id)
        # by the rules alone, never by this holder's own reading of the waiter's room: each project has its own memory
        # floor, a waiter may be a take by hand, and a reading that differs for good refused true orders for ever
        # (review of ed148e3, 1)
        return me is not None and them is not None and not policy.victim_reason(me, them, machine,
                                                                                 by_rules_only=True)

    def _over_now(self, found, value) -> bool:
        """The machine reads near or below the floor: this run's own project's, or the most cautious one the live
        bookings name as the daemon believes it - up to a quarter of the machine (final review C2)."""
        from flotilla.lane import machine, policy
        available = value.available_mb()
        floors = [item.floor_mb for item in found.values() if item.floor_mb is not None and self.lanes.live(item)
                  and item.state in (book.HELD, book.WAITING)]
        total = machine._memtotal()
        named = policy.clamp_floor(max(floors), total // 1024 if total else None) if floors else 0
        return available is not None and available < max(value.floor_mb, named) + OVER_MARGIN_MB

    def _pressed_now(self, found, item) -> bool:
        """The run the order is for - named in it - is held, and its own group reads pressed."""
        from flotilla.lane import tick
        pressed = found.get(item.order_by)
        if pressed is None or pressed.state != book.HELD or not pressed.unit or not self.lanes.live(pressed):
            return False
        path = self.groups.cgroup_of(pressed.unit, pressed.id)
        reading = self.groups.read(path) if path is not None else None
        return reading is not None and reading.pressure is not None and reading.pressure >= tick.PRESSED

    def _reclaim(self, asked: int) -> int:
        """What an order may move to swap: any session writes the journal, so the figure is held to what the swap
        can take within the 80 % the policy keeps (final review of 0.30.0, I7)."""
        from flotilla.lane import policy
        found = self.swap()
        if found is None or not found[1]:
            return 0
        free, total = found
        room = int(policy.SWAP_USED_CAP * total - (total - free))
        return max(0, min(int(asked), room))

    def _thaw(self) -> None:
        if self.slot.thaw() is False:
            return   # still frozen: the next look tries again, since the record says thawed
        self._frozen_since, self._kind = None, ""

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._look()
            except Exception as err:  # noqa: BLE001 - a broken watch thaws its command and freezes no more
                self._fail(err)
                return
            self._wait()

    def _now(self) -> float:
        return getattr(self.slot, "clock", time.monotonic)()

    def _mark(self, since: float | None) -> None:
        """Say whether this watch is inside a journal call now, and since when: the slot reads a watch inside one
        too long as stuck (review of 9d9d244)."""
        if hasattr(self.slot, "journal_since"):
            self.slot.journal_since = since

    def _look(self) -> str:
        """One step, marked as a journal call but for the freeze inside it."""
        self._mark(self._now())
        try:
            return self.step()
        finally:
            self._mark(None)

    def _wait(self) -> None:
        """Until the journal changes, LOOK_S at most, a second at a time; at once when stopped."""
        stamp = self.lanes.stamp() if hasattr(self.lanes, "stamp") else None
        began = time.monotonic()
        while not self._stop.is_set() and time.monotonic() - began < LOOK_S:
            self._stop.wait(1.0)
            if stamp is not None and self.lanes.stamp() != stamp:
                return

    def _fail(self, err: Exception) -> None:
        try:
            self._thaw()
        finally:
            try:
                self.lanes.unfreezable(self.booking_id)
            except Exception:  # noqa: BLE001 - the journal itself may be what failed; the command is thawed
                pass
            print(f"flotilla: the lane's watch on {self.booking_id} stopped ({err}); its run goes on, unfreezable",
                  file=sys.stderr)

    def start(self) -> None:
        self.slot.done_freezing = False   # a new booking is a new run: an earlier let-go was another run's
        self.slot.on_scope = lambda unit: self.lanes.scoped(self.booking_id, unit)   # the daemon sets its ceiling
        self._mark(None)
        self.slot.watcher = self.thread
        try:
            self.slot.ceiling_s = self.value_of(None).freeze_ceiling_s
        except Exception:  # noqa: BLE001 - an unreadable setting keeps the default the slot was given
            pass
        self.thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self.thread.is_alive():
            self.thread.join(timeout=LOOK_S + 2)
        self.slot.watcher = None
        self.slot.on_scope = None
        self._mark(None)
        if self.root is not None:
            admission.forget_beat(self.root, self.booking_id)

    def join(self, timeout: float | None = None) -> None:
        self.thread.join(timeout)

    def alive(self) -> bool:
        return self.thread.is_alive()
