"""What the collector keeps between runs, per repository, in the machine's state directory (gc design, section 9):
the person's answers, each bound to the fingerprint of what they were shown and the time it was given. Kept outside
the repository, like organize's answers: the person's word about their machine, not a fleet rule.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from flotilla.core import paths

FIELDS = {"answers": dict}


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


def save(state: Path, repo_key: str, data: dict) -> None:
    path = _path(state, repo_key)
    paths.ensure_private(Path(state))
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(path)
