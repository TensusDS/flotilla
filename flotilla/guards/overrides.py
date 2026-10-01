"""The record of knowing bypasses: a guard let through by a person's stated reason.

One log per repository in the state directory: a bypass describes this machine at this minute, not a branch.
"""

from __future__ import annotations

import re

from pathlib import Path

from flotilla.core.storage import LocalLogStore
from flotilla.ledger.model import now_iso


def _store(state_dir) -> LocalLogStore:
    return LocalLogStore(Path(state_dir) / "guards")


_ASSIGNMENT = re.compile(r"""\b([A-Za-z_][A-Za-z0-9_]*)=("[^"]*"|'[^']*'|\S+)""")
_USERINFO = re.compile(r"\b([a-z][a-z0-9+.-]*://)[^/@\s]+@", re.IGNORECASE)
_HEADER = re.compile(r"(?i)\b(authorization|cookie|x-api-key)(\s*:\s*)[^'\"\n]+")


def redact(text: str) -> str:
    """A command as the record keeps it: no value of a NAME=value assignment, quoted or not; nothing between a URL's
    scheme and its `@` (a password, or a token used as the user); no credential header (F23)."""
    text = _USERINFO.sub(lambda m: f"{m.group(1)}<redacted>@", text)
    text = _HEADER.sub(lambda m: f"{m.group(1)}{m.group(2)}<redacted>", text)
    return _ASSIGNMENT.sub(lambda m: f"{m.group(1)}=<redacted>", text)


def record_override(state_dir, repo_key: str, guard: str, reason: str, what) -> None:
    _store(state_dir).append(repo_key, {"at": now_iso(), "guard": guard, "reason": " ".join(reason.split()),
                                        "what": [redact(str(item)) for item in what]})


def recorded(state_dir, repo_key: str) -> list[dict]:
    return _store(state_dir).read(repo_key).records
