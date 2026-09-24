"""Answers given so far, per repository, in the machine's state directory.

Kept outside the repository: half an onboarding is nobody's project fact, and it must survive the
skill's session ending between two questions.
"""

from __future__ import annotations

import json
from pathlib import Path


def _path(state: Path, repo_key: str) -> Path:
    return state / "onboarding" / f"{repo_key}.json"


def load(state: Path, repo_key: str) -> dict:
    try:
        data = json.loads(_path(state, repo_key).read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save(state: Path, repo_key: str, data: dict) -> None:
    path = _path(state, repo_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def reset(state: Path, repo_key: str) -> None:
    _path(state, repo_key).unlink(missing_ok=True)
