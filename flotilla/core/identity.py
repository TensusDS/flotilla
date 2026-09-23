"""Which live session is calling flotilla.

Walk up from our own process until a pid the census lists. Match by pid only: a session
process's command name is its version number (`2.1.280`), not `claude` (probe, 2026-09-22).
"""

from __future__ import annotations

import os
from collections.abc import Callable

from flotilla.core.census import Session


def find_calling_session(sessions: list[Session], *, parent_of: Callable[[int], int | None],
                         start_pid: int | None = None, max_depth: int = 64) -> Session | None:
    by_pid = {s.pid: s for s in sessions if s.pid is not None}
    pid: int | None = os.getpid() if start_pid is None else start_pid
    seen: set[int] = set()
    for _ in range(max_depth):
        if pid is None or pid <= 1 or pid in seen:
            return None
        if pid in by_pid:
            return by_pid[pid]
        seen.add(pid)
        pid = parent_of(pid)
    return None
