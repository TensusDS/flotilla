"""When the collector's next pass is due (gc design 4 and 8). A move that leaves trees behind - ship, release, a
helper's done - marks it due instead of sweeping in the move: shipping stays fast, a batch gets one pass, and the
pass runs where its report reaches someone (the orchestrator's `flotilla fleet clean --daily`). A day without a
pass is due by itself. Kept beside the gc store, per repository, outside git."""

from __future__ import annotations

import json
import os
from pathlib import Path

from flotilla.core import paths

DAY_HOURS = 24
DAILY = "a day since the last pass"
NOTE = "note: the collector's next pass is due (the orchestrator's flotilla fleet clean --daily)"


def _path(state, repo_key: str) -> Path:
    return Path(state) / "gc" / f"{repo_key}-due.json"


def _read(state, repo_key: str) -> dict:
    try:
        data = json.loads(_path(state, repo_key).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"marks": [], "last": None}
    marks = data.get("marks") if isinstance(data, dict) else None
    last = data.get("last") if isinstance(data, dict) else None
    return {"marks": [str(item) for item in marks] if isinstance(marks, list) else [],
            "last": last if isinstance(last, (int, float)) else None}


def _write(state, repo_key: str, data: dict) -> None:
    path = _path(state, repo_key)
    paths.ensure_private(Path(state))
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def mark(state, repo_key: str, reason: str) -> None:
    data = _read(state, repo_key)
    if reason not in data["marks"]:
        data["marks"].append(reason)
        _write(state, repo_key, data)


def due(state, repo_key: str, *, now: float) -> list[str]:
    data = _read(state, repo_key)
    stale = data["last"] is None or now - data["last"] >= DAY_HOURS * 3600
    return data["marks"] + ([DAILY] if stale else [])


def hours_left(state, repo_key: str, *, now: float) -> int:
    last = _read(state, repo_key)["last"]
    return 0 if last is None else max(0, int((last + DAY_HOURS * 3600 - now) // 3600))


def passed(state, repo_key: str, *, now: float) -> None:
    _write(state, repo_key, {"marks": [], "last": now})


def noted(ledger, reason: str) -> str:
    """Mark the pass due after a move and say so; a mark that cannot be written is a note, never a refusal - the move
    is already recorded."""
    try:
        mark(ledger.state_dir, ledger.repo_key, reason)
    except OSError as err:
        return f"note: the collector's next pass could not be marked due ({err}); the daily pass still comes"
    return NOTE
