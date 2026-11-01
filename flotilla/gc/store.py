"""What the collector keeps between runs, per repository, in the machine's state directory (gc design, section 9):
the person's answers, each bound to the fingerprint of what they were shown and the time it was given. Kept outside
the repository, like organize's answers: the person's word about their machine, not a fleet rule.
"""

from __future__ import annotations

import fcntl
import json
import os
from contextlib import contextmanager
from pathlib import Path

from flotilla.core import paths

FIELDS = {"answers": dict, "letters": dict, "holders": dict, "expiry": dict, "dead_since": dict}


def empty() -> dict:
    return {name: kind() for name, kind in FIELDS.items()}


def _path(state: Path, repo_key: str) -> Path:
    return Path(state) / "gc" / f"{repo_key}.json"


def load(state: Path, repo_key: str) -> dict:
    try:
        data = json.loads(_path(state, repo_key).read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError, OSError):
        return empty()
    if not isinstance(data, dict):
        return empty()
    out = empty()
    for name, kind in FIELDS.items():
        if isinstance(data.get(name), kind):
            out[name] = data[name]
    return out


@contextmanager
def locked(state: Path, repo_key: str):
    """One writer of the store at a time: the person's commands and the background daily pass both write it."""
    path = _path(state, repo_key)
    paths.ensure_private(Path(state))
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with open(path.with_suffix(".lock"), "a", encoding="utf-8") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _write(path: Path, data: dict) -> None:
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")   # never one temporary file for two writers
    temporary.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def save(state: Path, repo_key: str, data: dict) -> None:
    with locked(state, repo_key):
        _write(_path(state, repo_key), data)


#: What the daily pass itself keeps; everything else - the person's answers above all - is theirs.
PASS_FIELDS = ("letters", "holders", "dead_since")
PASS_EXPIRY = ("asked", "asked_for", "until")


def save_pass(state: Path, repo_key: str, data: dict) -> None:
    """Save what the daily pass changed onto the store as it is now: an answer the person recorded while the pass ran
    stays (final review of the branch, I4)."""
    with locked(state, repo_key):
        fresh = load(state, repo_key)
        for name in PASS_FIELDS:
            fresh[name] = data.get(name, {})
        mine, theirs = data.get("expiry") or {}, fresh.setdefault("expiry", {})
        for name in PASS_EXPIRY:
            if name in mine:
                theirs[name] = mine[name]
            else:
                theirs.pop(name, None)
        _write(_path(state, repo_key), fresh)
