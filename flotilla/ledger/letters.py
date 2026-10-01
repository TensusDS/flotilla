"""The letters a command owes the sessions it passed a move to (field test F23).

An idle background session is woken only by a message, and `claude` has no command line to send one, so flotilla
never sends it: the command line prints the letter and the session that made the move sends it (SendMessage). The
comparison covers every row, because a move on one row can pass another one on (a part the judge may walk once the
row building on it ships). A letter is addressed from the census, or by post when the census cannot be asked:
liveness unknown never swallows a letter.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from flotilla.core.text import visible
from flotilla.ledger import views
from flotilla.ledger.model import Row
from flotilla.posts import PostError, post_for_session

WHAT = {"reserved": "reserved for you", "claimed": "yours to build", "fixing": "returned to you for fixes",
        "accepted": "accepted by its reader", "queued": "queued", "landed": "landed", "shipped": "shipped",
        "walked": "walked on the live build"}


@dataclass(frozen=True)
class Letter:
    mover: str
    to: tuple[str, ...]
    branch: str
    text: str


def _post_of(posts: dict, name: str) -> str:
    try:
        found = post_for_session(posts, name)
    except PostError:
        return ""
    return found.name if found is not None else ""


def _body(row: Row) -> str:
    if row.state == "handed":
        from flotilla.ledger.reading import letter
        return letter(row)
    said = f"`{row.branch}` (row {row.id}) is {WHAT.get(row.state, row.state)}; the next move is yours."
    if row.state == "fixing" and row.why:
        said += f" What must change: {visible(row.why)}"   # the reader's words, shown as data
    if row.state == "fixing" and row.pr:
        said += f" PR #{row.pr} stays open: push the fix to `{row.branch}` and it updates."
    return said + f" `flotilla work show {row.branch}` has the rest."


def changed(before: dict[str, Row], after: dict[str, Row], profile: dict, posts: dict, caller: str,
            live: Callable[[], set[str] | None]) -> list[Letter]:
    """A letter for every row whose mover changed to a session other than the caller's."""
    mine = _post_of(posts, caller)
    due = []
    for row_id, row in after.items():
        mover = views.who_moves(row, profile, after)
        if not mover or mover.startswith("the person"):
            continue
        old = before.get(row_id)
        if old is not None and views.who_moves(old, profile, before) == mover:
            continue
        if mover == caller or (mine and views.POST_OF_MOVER.get(mover) == mine):
            continue
        due.append((mover, row))
    if not due:
        return []
    names = live()
    found = []
    for mover, row in due:
        post = views.POST_OF_MOVER.get(mover)
        if post is None:
            to = (mover,) if names is None or mover in names else ()
        elif names is None:
            to = (f"the session holding the {post} post",)
        else:
            to = tuple(sorted(name for name in names if _post_of(posts, name) == post))
        found.append(Letter(mover=mover, to=to, branch=row.branch, text=_body(row)))
    return found


def render(letter: Letter) -> list[str]:
    if not letter.to:
        return [f"note: `{letter.branch}` is now {letter.mover}'s move and no live session can make it; "
                "tell the orchestrator"]
    return [f"letter for {', '.join(letter.to)} - send it with SendMessage; an idle background session is woken "
            "only by a message:", *(f"  {line}" for line in letter.text.splitlines())]
