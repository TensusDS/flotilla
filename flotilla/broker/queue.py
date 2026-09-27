"""The permission queue: questions a background session could not show a person, answered one at a time.

A question is a file in `<state>/broker/<repo key>/`, written by the asking session's `PermissionRequest` hook,
which then waits for the answer file beside it. The first answer written wins and is never replaced: an answer and
the hook's own withdrawal racing at the deadline cannot both count (`os.link` refuses the second). A question is
live while its hook still waits — its process is alive and its deadline has not passed. Anything else is closed and
never shown, because an answer nobody waits for would read as applied.
"""

from __future__ import annotations

import dataclasses
import json
import os
import time
from pathlib import Path

ALLOW, SESSION, DENY, WITHDRAWN = "allow", "session", "deny", "withdrawn"
CHOICES = (ALLOW, SESSION, DENY)


class QueueRefused(RuntimeError):
    """The answer cannot be recorded; the message says why."""


@dataclasses.dataclass(frozen=True)
class Question:
    id: str
    at: float
    deadline: float
    pid: int
    session: str
    session_id: str
    tool: str
    tool_input: dict
    suggestions: list


def folder(state_dir, repo_key: str) -> Path:
    return Path(state_dir) / "broker" / repo_key


def _read(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def ask(state_dir, repo_key: str, *, session: str, session_id: str, tool: str, tool_input, suggestions, wait: float,
        now: float | None = None, pid: int | None = None) -> Question:
    now = time.time() if now is None else now
    pid = os.getpid() if pid is None else pid
    asked = Question(f"{int(now * 1000):013d}-{pid}", now, now + wait, pid, session, session_id, tool,
                     dict(tool_input or {}), list(suggestions or []))
    base = folder(state_dir, repo_key)
    base.mkdir(parents=True, exist_ok=True)
    staged = base / f".q-{asked.id}.tmp"
    staged.write_text(json.dumps(dataclasses.asdict(asked)), encoding="utf-8")
    os.replace(staged, base / f"q-{asked.id}.json")
    return asked


def question(state_dir, repo_key: str, qid: str) -> Question | None:
    data = _read(folder(state_dir, repo_key) / f"q-{qid}.json")
    try:
        return Question(**data) if isinstance(data, dict) else None
    except TypeError:
        return None


def answer_of(state_dir, repo_key: str, qid: str) -> dict | None:
    return _read(folder(state_dir, repo_key) / f"a-{qid}.json")


def _close(state_dir, repo_key: str, qid: str, record: dict) -> bool:
    """Write the one answer this question gets; False when another was written first."""
    base = folder(state_dir, repo_key)
    base.mkdir(parents=True, exist_ok=True)
    staged = base / f".a-{qid}-{os.getpid()}.tmp"
    staged.write_text(json.dumps(record), encoding="utf-8")
    try:
        os.link(staged, base / f"a-{qid}.json")
        return True
    except FileExistsError:
        return False
    finally:
        staged.unlink(missing_ok=True)


def live(state_dir, repo_key: str, *, now: float | None = None, alive=is_alive) -> list[Question]:
    now = time.time() if now is None else now
    base = folder(state_dir, repo_key)
    found = []
    for path in sorted(base.glob("q-*.json")) if base.is_dir() else []:
        asked = question(state_dir, repo_key, path.name[2:-5])
        if asked is None or (base / f"a-{asked.id}.json").exists():
            continue
        if asked.deadline <= now or not alive(asked.pid):
            continue
        found.append(asked)
    return sorted(found, key=lambda item: (item.at, item.id))


def answer(state_dir, repo_key: str, qid: str, choice: str, *, why: str = "", now: float | None = None,
           alive=is_alive) -> Question:
    if choice not in CHOICES:
        raise QueueRefused(f"`{choice}` is not an answer; one of {', '.join(CHOICES)}")
    now = time.time() if now is None else now
    asked = question(state_dir, repo_key, qid)
    if asked is None:
        raise QueueRefused(f"no question `{qid}`")
    closed = answer_of(state_dir, repo_key, qid)
    if closed is not None:
        raise QueueRefused(f"question `{qid}` is already closed: "
                           f"{closed.get('choice')} {closed.get('why', '')}".strip())
    if asked.deadline <= now:
        raise QueueRefused(f"question `{qid}` was withdrawn: nobody answered in time, and {asked.session} was told no")
    if not alive(asked.pid):
        raise QueueRefused(f"question `{qid}` was abandoned: the hook that asked it is gone ({asked.session} "
                           "stopped)")
    if not _close(state_dir, repo_key, qid, {"choice": choice, "why": why.strip(), "at": now}):
        raise QueueRefused(f"question `{qid}` was closed a moment ago; `flotilla permit next` shows what is left")
    return asked


def withdraw(state_dir, repo_key: str, qid: str, why: str, *, now: float | None = None) -> bool:
    return _close(state_dir, repo_key, qid,
                  {"choice": WITHDRAWN, "why": why, "at": time.time() if now is None else now})
