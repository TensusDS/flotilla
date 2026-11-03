"""What flotilla put on the person's screen, and when the person's line is due (person inbox design, goals 3 and 6).

Only flotilla's own displays are recorded here - the line it printed as the prompt hook's `systemMessage`, and the
listing the person ran themselves - never a model's word that it told the person. One file per project, not per
session, so moving between windows does not repeat the line.

Measured on Claude Code 2.1.317 (2026-11-03, a probe hook in an interactive session and in a background session the
person attached to): a `UserPromptSubmit` hook's `systemMessage` shows on screen as "UserPromptSubmit says: <text>"
in both; the model does not see it; it goes with `hookSpecificOutput.additionalContext` in one output, and the model
sees that; the transcript keeps it as a `hook_system_message` attachment. Every prompt's payload carries the same
keys: a peer's message arrives as a prompt starting `<cross-session-message`, a background task's end as one
starting `<task-notification>`, while a typed prompt and a background session's launch prompt are plain text and
cannot be told apart. So the line is shown only on a prompt that is not one of those, and recorded as shown only in an
interactive session.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path

MACHINE_PROMPTS = ("<cross-session-message", "<task-notification")
AWAY = dt.timedelta(hours=1)


def persons_prompt(prompt) -> bool:
    """Whether a prompt can be the person's: not a peer's message or a task's notification (measured, above)."""
    return not (isinstance(prompt, str) and prompt.lstrip().startswith(MACHINE_PROMPTS))


def _path(state_dir, repo_key: str) -> Path:
    name = hashlib.sha256(repo_key.encode("utf-8")).hexdigest()[:32]
    return Path(state_dir) / "watch" / "shown" / f"{name}.json"


def load(state_dir, repo_key: str) -> dict:
    try:
        data = json.loads(_path(state_dir, repo_key).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    first = data.get("first") if isinstance(data.get("first"), dict) else {}
    line = data.get("line") if isinstance(data.get("line"), dict) else {}
    prompt_at = data.get("prompt_at") if isinstance(data.get("prompt_at"), str) else ""
    return {"first": first, "line": line, "prompt_at": prompt_at}


def _save(state_dir, repo_key: str, data: dict) -> None:
    path = _path(state_dir, repo_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def mark(state_dir, repo_key: str, keys, at: str) -> None:
    """The first time each key was shown to the person; a later showing keeps the first."""
    data = load(state_dir, repo_key)
    for key in keys:
        data["first"].setdefault(key, at)
    _save(state_dir, repo_key, data)


def _signature(entries) -> list[str]:
    return sorted(f"{entry.key}|{entry.group}" for entry in entries)


def line_due(state: dict, entries, *, mode: str, every: int, now: dt.datetime) -> bool:
    """Whether the person's line shows on this prompt. `all`: when what waits changed since it last showed, or the
    person comes back after an hour away; `digest`: once per period, and at once for a new blocker."""
    if not entries:
        return False
    last = state.get("line") or {}
    try:
        at = dt.datetime.fromisoformat(last["at"])
    except (KeyError, TypeError, ValueError):
        return True
    if mode == "digest":
        blockers = {entry.key for entry in entries if entry.blocker}
        return now - at >= dt.timedelta(minutes=every) or bool(blockers - set(last.get("blockers") or []))
    if _signature(entries) != last.get("signature"):
        return True
    try:
        prompted = dt.datetime.fromisoformat(state.get("prompt_at") or "")
    except ValueError:
        return True
    return now - prompted > AWAY


def remember(state_dir, repo_key: str, *, now: dt.datetime, line_entries=None, clear_line: bool = False,
             shown_keys=(), prompt: bool = False) -> None:
    """One write for one prompt: the line shown (or cleared), the keys counted as shown, the person's prompt time."""
    data = load(state_dir, repo_key)
    at = now.isoformat(timespec="seconds")
    if clear_line:
        data["line"] = {}
    if line_entries is not None:
        data["line"] = {"at": at, "signature": _signature(line_entries),
                        "blockers": sorted(entry.key for entry in line_entries if entry.blocker)}
    for key in shown_keys:
        data["first"].setdefault(key, at)
    if prompt:
        data["prompt_at"] = at
    _save(state_dir, repo_key, data)
