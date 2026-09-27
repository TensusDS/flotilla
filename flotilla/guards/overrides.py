"""The record of knowing bypasses: a guard let through by a person's stated reason.

One log per repository in the state directory: a bypass describes this machine at this minute, not a branch.
"""

from __future__ import annotations

from pathlib import Path

from flotilla.core.storage import LocalLogStore
from flotilla.ledger.model import now_iso


def _store(state_dir) -> LocalLogStore:
    return LocalLogStore(Path(state_dir) / "guards")


def record_override(state_dir, repo_key: str, guard: str, reason: str, what) -> None:
    _store(state_dir).append(repo_key, {"at": now_iso(), "guard": guard, "reason": " ".join(reason.split()),
                                        "what": list(what)})


def recorded(state_dir, repo_key: str) -> list[dict]:
    return _store(state_dir).read(repo_key).records
