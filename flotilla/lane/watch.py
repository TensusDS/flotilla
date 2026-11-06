"""The holder's watch: the thread in a `lane run` or a receipt that obeys freeze orders on its own booking, and asks
to thaw through the queue (lane shared machine design, section 4.1).

It wakes on journal writes, as a waiter does, and looks at least every few seconds. On an order it freezes its
command and records it; while frozen it asks, as a waiter would, whether it may come back. A stop by signal held for
nothing - its waiter never started, because the memory never showed free - is let go and never ordered again. Each
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
LET_GO_S = 120.0   # a stop by signal held this long while its waiter still waits is let go (builder's number)


class Watch:
    def __init__(self, lanes, booking_id: str, slot, value_of: Callable, *, clock=time.monotonic,
                 sleep=time.sleep, let_go_s: float = LET_GO_S, root=None, swap=None):
        self.lanes, self.booking_id, self.slot, self.value_of = lanes, booking_id, slot, value_of
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
            return ""
        if item.frozen == "ordered" and not self.slot.frozen():
            found = self.slot.freeze(self._reclaim(item.reclaim_mb or 0))
            self._frozen_since, self._kind = self.clock(), found.get("freeze_kind", "")
            if self.lanes.frozen(self.booking_id, freeze_kind=found.get("freeze_kind", ""),
                                 unit=found.get("unit", ""), group=found.get("group"),
                                 held_mb=found.get("held_mb")) is None:
                self._thaw()   # the booking no longer holds: nothing may stay frozen for it
                return ""
            return "froze"
        if item.frozen == "frozen":
            orderer = self.lanes.bookings().get(item.order_by)
            waits = orderer is not None and orderer.state == book.WAITING
            if self._kind == freeze.SIGNAL and waits and self._frozen_since is not None and \
                    self.clock() - self._frozen_since >= self.let_go_s:
                self.lanes.thaw(self.booking_id, self.value_of(None), force=True, never_again=True)
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
                self.step()
            except Exception as err:  # noqa: BLE001 - a broken watch thaws its command and freezes no more
                self._fail(err)
                return
            self._wait()

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
        self.slot.watcher = self.thread
        self.thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self.thread.is_alive():
            self.thread.join(timeout=LOOK_S + 2)
        self.slot.watcher = None
        if self.root is not None:
            admission.forget_beat(self.root, self.booking_id)

    def join(self, timeout: float | None = None) -> None:
        self.thread.join(timeout)

    def alive(self) -> bool:
        return self.thread.is_alive()
