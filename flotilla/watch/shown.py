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

Measured again on 2.1.317 (2026-11-03, after the security review of 5f65ad5): a CronCreate or `/loop` firing and a
subagent's hand-back (`<agent-message`) reach the hook as well, the firing as plain text. At hook time the prompt's
record is not yet in the transcript (the first prompt finds no file at all); after the turn it is there, keyed by the
hook's `prompt_id`, saying `promptSource` typed or system and `turnOrigin` human, scheduled, peer or
task_notification. So the hook shows the line when it is due and leaves the turn pending; the Stop hook reads the
transcript and only a prompt recorded as typed by a human is counted - the line as shown, the person as having
prompted. Nothing confirmed, nothing counted: the line comes again rather than being spent on a tick nobody read. A
background session's launch prompt is recorded as typed, so a background session's first confirmed prompt counts
only as a prompt, never as the line shown.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path

MACHINE_PROMPTS = ("<cross-session-message", "<task-notification", "<agent-message")
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


def _pending_path(state_dir, session_id: str) -> Path:
    name = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:32]
    return Path(state_dir) / "watch" / "shown" / "turn" / f"{name}.json"


def hold_turn(state_dir, session_id: str, turn: dict) -> None:
    """This session's prompt, waiting for the transcript to say who typed it (settled at Stop)."""
    path = _pending_path(state_dir, session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(turn, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def typed_by_a_human(transcript_path, prompt_id: str) -> bool:
    """Whether the transcript records this prompt as typed by a human; anything else, or nothing found, is no."""
    if not prompt_id or not transcript_path:
        return False
    try:
        with open(transcript_path, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if prompt_id not in line:
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if record.get("type") == "user" and record.get("promptId") == prompt_id:
                    return record.get("promptSource") == "typed" and record.get("turnOrigin") == "human"
    except OSError:
        return False
    return False


def settle(state_dir, session_id: str, transcript_path, prompt_id: str = "") -> None:
    """At Stop: a pending turn whose prompt the transcript records as typed by a human is counted; any other is
    dropped. Counted: the person prompted; the line, if it showed, was shown, and its rows were shown to the person
    when the session is interactive. A background session's first counted prompt is its launch: a prompt only."""
    path = _pending_path(state_dir, session_id)
    try:
        turn = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    path.unlink(missing_ok=True)
    if not isinstance(turn, dict) or (prompt_id and turn.get("prompt_id") != prompt_id):
        return
    if not typed_by_a_human(transcript_path, str(turn.get("prompt_id") or "")):
        return
    repo, throttle = str(turn.get("repo") or ""), str(turn.get("throttle") or "")
    try:
        now = dt.datetime.fromisoformat(turn["at"])
    except (KeyError, TypeError, ValueError):
        return
    launch = not turn.get("interactive") and not load(state_dir, throttle)["prompt_at"]
    data = load(state_dir, throttle)
    at = now.isoformat(timespec="seconds")
    line = turn.get("line")
    if isinstance(line, dict) and not launch:
        data["line"] = {"at": at, "signature": line.get("signature") or [], "blockers": line.get("blockers") or []}
    data["prompt_at"] = at
    _save(state_dir, throttle, data)
    if isinstance(line, dict) and not launch and turn.get("interactive"):
        mark(state_dir, repo, line.get("keys") or [], at)


def clear_line(state_dir, throttle: str) -> None:
    data = load(state_dir, throttle)
    if data["line"]:
        data["line"] = {}
        _save(state_dir, throttle, data)


def line_record(entries) -> dict:
    return {"signature": _signature(entries), "blockers": sorted(entry.key for entry in entries if entry.blocker),
            "keys": [entry.key for entry in entries]}
