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


def _post_key(rows: dict, name: str, post_name: str) -> str:
    """Where a session's seat-level note lives: its open post row; for the orchestrator leading from the person's
    own session, which has none, a key of its own (design 2a); else refused."""
    mine = [row for row in rows.values() if row.is_open and row.state == "reserved" and row.owner == name]
    if mine:
        return mine[-1].id
    if post_name == "orchestrator":
        return lead_key(name)
    raise MoveRefused(f"{name} holds no post row, so it has no seat-level note; name the branch the note is about")


def note(ledger, actor, branch: str | None, *, text: str, post: bool) -> Memo:
    """Write the caller's working note: on a row it owns or reads, or (`post`) its seat-level note. It moves no row."""
    text = checked(text)
    rows = ledger.rows()
    if bool(branch) == bool(post):
        raise MoveRefused("name the branch the note is about, or --post for your seat-level note - one of them")
    if post:
        key = _post_key(rows, actor.name, actor.post.name if actor.post else "")
    else:
        found = [row for row in rows.values() if row.branch == branch and row.is_open]
        if not found:
            raise MoveRefused(f"no open row for `{branch}`")
        row = found[-1]
        if actor.name not in (row.owner, row.reader):
            raise MoveRefused(f"only the owner ({row.owner}) or the reader of `{branch}` keeps a working note on it; "
                              "each session's note is its own")
        key = row.id
    memo = Memo(key, actor.name, text, ledger.now())
    write(ledger.state_dir, ledger.repo_key, memo)
    return memo


def on_row(state_dir, repo_key: str, row_id: str) -> list[Memo]:
    return [memo for (row, _), memo in latest(state_dir, repo_key).items() if row == row_id]


def for_session(state_dir, repo_key: str, rows: dict, name: str) -> list[tuple[str, Memo]]:
    """(label, memo) for what a session should see of its notes: its own on each open row it owns or reads and on its
    post row (or lead key), and what others left on the open rows it owns - labelled as theirs."""
    found = latest(state_dir, repo_key)
    said: list[tuple[str, Memo]] = []
    for row in rows.values():
        if not row.is_open or name not in (row.owner, row.reader):
            continue
        seat = row.state == "reserved" and row.owner == name
        mine = found.get((row.id, name))
        if mine is not None:
            said.append(("your seat note" if seat else f"your note on {row.branch}", mine))
        if row.owner == name and not seat:
            said += [(f"left by {memo.writer} on {row.branch}", memo) for (key, writer), memo in found.items()
                     if key == row.id and writer != name and writer != row.reader]
    lead = found.get((lead_key(name), name))
    if lead is not None:
        said.append(("your seat note", lead))
    return said
