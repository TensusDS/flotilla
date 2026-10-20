"""What organize keeps between runs, per repository, in the machine's state directory (spec, sections 4 and 8).

The person's answers (each with the fingerprint of what they were shown), the session's proposals waiting for the
person's yes, the items the person ignored, the trees they let organize enter, and the commits they named for
publishing. Kept outside the repository: the ignore record is the person's view of their machine, not a fleet rule.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from flotilla.core import paths

FIELDS = {"answers": dict, "proposals": dict, "ignored": dict, "trees": list, "publish_named": list}
NUMBERED = re.compile(r"^(pr|chain):([0-9][0-9-]*)(:|$)")


def empty() -> dict:
    return {name: kind() for name, kind in FIELDS.items()}


def _path(state: Path, repo_key: str) -> Path:
    return Path(state) / "organize" / f"{repo_key}.json"


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


def forget(data: dict, key: str) -> None:
    """Drop every answer and the proposal of one item: its own question and its follow-ups (`<key>:...`)."""
    for qid in [qid for qid in data["answers"] if qid == key or qid.startswith(f"{key}:")]:
        del data["answers"][qid]
    data["proposals"].pop(key, None)


def publish_named(state: Path, repo_key: str) -> list[str]:
    return list(load(state, repo_key)["publish_named"])


def numbers(data: dict) -> set[int]:
    """The pull request numbers an open answer or proposal names: what the guard keeps a session's hands off."""
    found: set[int] = set()
    for qid in [*data["answers"], *data["proposals"]]:
        matched = NUMBERED.match(qid)
        if matched:
            found.update(int(part) for part in matched.group(2).split("-") if part)
    for proposal in data["proposals"].values():
        if isinstance(proposal, dict):
            found.update(int(number) for number in proposal.get("pulls") or [] if str(number).isdigit())
    return found
