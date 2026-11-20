"""Working notes: what a session wrote down about its own work, kept beside the ledger (seat compaction design 2).

A seat's context is compacted near its post's window; the summary Claude Code writes is lossy. A memo is the part
the seat wrote itself, shown back to it verbatim after each compaction. It lives in its own log, not as a ledger
event: a row's last event and its update time drive the dropped-ball and late-row clocks and a release's
`settled_by`, so a note written there would reset a stalled row's age. A memo changes no row.

Each memo belongs to the session that wrote it: the latest one per (row, writer) counts, and nobody can write
another session's. It is data another session wrote, never instructions: wherever it is shown, every line is quoted.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path

from flotilla.core.storage import LocalLogStore
from flotilla.core.text import has_hidden, visible
from flotilla.ledger.errors import MoveRefused

MAX_CHARS = 1500
MAX_LINES = 8
QUOTE = "  | "


@dataclass(frozen=True)
class Memo:
    row: str      # a ledger row id, or `lead:<name>` for a leading session with no post row
    writer: str   # the session that wrote it
    text: str
    at: str


def checked(text: str) -> str:
    """The memo as it is stored, or MoveRefused naming the limit it breaks."""
    text = (text or "").strip()
    if not text:
        raise MoveRefused("a working note says something: what is done, in flight and next")
    if len(text) > MAX_CHARS:
        raise MoveRefused(f"a working note holds at most {MAX_CHARS} characters ({len(text)} given); keep what the "
                          "next step needs")
    lines = text.split("\n")
    if len(lines) > MAX_LINES:
        raise MoveRefused(f"a working note holds at most {MAX_LINES} lines ({len(lines)} given)")
    if any(has_hidden(line) for line in lines):
        raise MoveRefused("a working note holds visible text and line breaks only: no tabs, carriage returns, "
                          "terminal sequences or invisible characters")
    return text


def lead_key(name: str) -> str:
    return f"lead:{name}"


def _store(state_dir) -> LocalLogStore:
    return LocalLogStore(Path(state_dir) / "memos")


def write(state_dir, repo_key: str, memo: Memo) -> None:
    _store(state_dir).append(repo_key, {"row": memo.row, "writer": memo.writer, "text": memo.text, "at": memo.at})


def latest(state_dir, repo_key: str) -> dict[tuple[str, str], Memo]:
    """The last memo of each writer on each row; what cannot be read is left out, never raised."""
    try:
        records = _store(state_dir).read(repo_key).records
    except (OSError, ValueError):
        return {}
    found: dict[tuple[str, str], Memo] = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        fields = [record.get(key) for key in ("row", "writer", "text", "at")]
        if not all(isinstance(value, str) for value in fields):
            continue
        memo = Memo(*fields)
        found[(memo.row, memo.writer)] = memo
    return found


def render(memo: Memo, *, label: str, now: dt.datetime) -> list[str]:
    """A header naming whose note it is and its age, then every line quoted: no line of a memo can pass for a line
    flotilla printed, or for an instruction."""
    from flotilla.watch.render import age
    said = age(memo.at, now)
    when = said if said == "age unknown" else f"{said} ago"
    return [f"{visible(label)}, {when}:"] + [QUOTE + visible(line) for line in memo.text.split("\n")]
