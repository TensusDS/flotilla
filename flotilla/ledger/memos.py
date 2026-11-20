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


def former_owner(row) -> str:
    """The owner a row was last adopted from: `adopted_from` holds a trail, `B <- A`, after more than one."""
    return (row.adopted_from or "").split(" <- ")[0].strip()


def lead_key(name: str) -> str:
    return f"lead:{name}"


def _store(state_dir) -> LocalLogStore:
    return LocalLogStore(Path(state_dir) / "memos")


def write(state_dir, repo_key: str, memo: Memo) -> None:
    _store(state_dir).append(repo_key, {"row": memo.row, "writer": memo.writer, "text": memo.text, "at": memo.at})


def _records(state_dir, repo_key: str) -> list:
    """Every line of the log that parses, one by one: a damaged line - a hand edit, an outside writer, a disk fault -
    costs itself, never the notes around it (review of d0517a3, I2)."""
    import json
    try:
        data = _store(state_dir)._path(repo_key).read_bytes()
    except (OSError, ValueError):
        return []
    found = []
    for raw in data.split(b"\n")[:-1]:   # a line counts once its newline is on disk, as in the store
        try:
            found.append(json.loads(raw.decode("utf-8")))
        except (UnicodeDecodeError, ValueError, RecursionError):
            continue
    return found


def _aware(moment: str) -> bool:
    try:
        return dt.datetime.fromisoformat(moment).tzinfo is not None
    except (TypeError, ValueError):
        return False


def latest(state_dir, repo_key: str) -> dict[tuple[str, str], Memo]:
    """The last memo of each writer on each row; a record that cannot be read, has no time with its zone, or holds
    text `checked` would refuse is left out, never raised."""
    found: dict[tuple[str, str], Memo] = {}
    for record in _records(state_dir, repo_key):
        if not isinstance(record, dict):
            continue
        fields = [record.get(key) for key in ("row", "writer", "text", "at")]
        if not all(isinstance(value, str) for value in fields) or not _aware(fields[3]):
            continue
        try:
            if checked(fields[2]) != fields[2]:
                continue
        except MoveRefused:
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


def may_keep(rows: dict, branch: str, name: str, *, move: str = ""):
    """The open row of `branch` when `name` owns or reads it - or is about to read it, by a `fix` on a row nobody
    reads yet, which makes it the reader (re-review of 76c078a, 1); else MoveRefused. Each session's note is its own,
    and only on a row it works on: a mover that records a wait keeps no note there (review of d0517a3, I3)."""
    found = [row for row in rows.values() if row.branch == branch and row.is_open]
    if not found:
        raise MoveRefused(f"no open row for `{branch}`")
    row = found[-1]
    reads_by_fixing = move == "fix" and not row.reader and name != row.owner
    if name not in (row.owner, row.reader) and not reads_by_fixing:
        raise MoveRefused(f"only the owner ({row.owner}) or the reader of `{branch}` keeps a working note on it; "
                          "each session's note is its own - make the move without --memo")
    return row


def note(ledger, actor, branch: str | None, *, text: str, post: bool) -> Memo:
    """Write the caller's working note: on a row it owns or reads, or (`post`) its seat-level note. It moves no row."""
    text = checked(text)
    rows = ledger.rows()
    if bool(branch) == bool(post):
        raise MoveRefused("name the branch the note is about, or --post for your seat-level note - one of them")
    if post:
        key = _post_key(rows, actor.name, actor.post.name if actor.post else "")
    else:
        key = may_keep(rows, branch, actor.name).id
    memo = Memo(key, actor.name, text, ledger.now())
    write(ledger.state_dir, ledger.repo_key, memo)
    return memo


def on_row(state_dir, repo_key: str, row_id: str) -> list[Memo]:
    return [memo for (row, _), memo in latest(state_dir, repo_key).items() if row == row_id]


def for_session(state_dir, repo_key: str, rows: dict, name: str) -> list[tuple[str, Memo]]:
    """(label, memo) for what a session should see of its notes: its own on each open row it owns or reads and on its
    post row (or lead key), and on an open row it owns the note of the owner it adopted the row from - labelled as
    theirs. A reader's or a mover's note on the row is theirs alone (review of d0517a3, I3)."""
    found = latest(state_dir, repo_key)
    said: list[tuple[str, Memo]] = []
    for row in rows.values():
        if not row.is_open or name not in (row.owner, row.reader):
            continue
        seat = row.state == "reserved" and row.owner == name
        mine = found.get((row.id, name))
        if mine is not None:
            said.append(("your seat note" if seat else f"your note on {row.branch}", mine))
        gone = former_owner(row)   # the owner before the last adoption: its notes are the heir's to see, as its
        if row.owner == name and not seat and gone and gone != name and (row.id, gone) in found:
            said.append((f"left by {gone} on {row.branch}", found[(row.id, gone)]))
    lead = found.get((lead_key(name), name))
    if lead is not None:
        said.append(("your seat note", lead))
    return said
