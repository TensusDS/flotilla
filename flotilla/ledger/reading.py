"""Reading handed work (spec, sections 6.3 and 6.9).

`take` is the reader saying "I am reading this": a fact in the ledger, where an earlier tool inferred it from
transcripts. An author never reads, returns or accepts their own work — enforced by the row, not only by the posts.
A verdict stands over the handed tip only: the revision read must be that tip, and the branch must still be there.
"""

from __future__ import annotations

from flotilla.ledger import gitq
from flotilla.ledger.actor import Actor, require_may
from flotilla.ledger.core import Ledger
from flotilla.ledger.errors import MoveRefused
from flotilla.ledger.model import Row
from flotilla.posts import PostError, post_for_session


def _not_the_author(row: Row, actor: Actor, doing: str) -> None:
    if actor.name == row.owner:
        raise MoveRefused(f"`{row.branch}` is {actor.name}'s own work; an author never {doing} it")


def _not_assigned_elsewhere(row: Row, actor: Actor) -> None:
    if row.reader and row.reader != actor.name:
        raise MoveRefused(f"`{row.branch}` is assigned to {row.reader}")


def read_from(ledger: Ledger, row: Row) -> tuple[str, str]:
    """Where the reader's diff starts, and the recorded base it moved from when the owner rebased ("" if it did not).

    The recorded base stays the row's: what waits for the person's yes counts every commit from it. Only what is
    shown moves, only forward from the recorded base, and only when another row's verdict accounts for every commit
    the move skips - the same accounting the push guard trusts. Git alone cannot tell "rebased onto trunk" from "my
    own commits reached trunk unread", and the fork point is read from `origin/<trunk>`, a ref a seat can repoint
    (final review of 0.21.0, C1): either would otherwise start the reader above commits nobody read (security
    review of a7a44df, and its second review)."""
    tip = row.tip or gitq.branch_tip(ledger.root, row.branch, run=ledger.run) or ""
    if not row.base or not tip:
        return row.base, ""
    fresh = gitq.merge_base(ledger.root, gitq.trunk_ref(ledger.root, ledger.trunk, run=ledger.run), tip,
                            run=ledger.run)
    if fresh and fresh != row.base and gitq.is_ancestor(ledger.root, row.base, fresh, run=ledger.run) is True \
            and _read_by_others(ledger, row, fresh):
        return fresh, row.base
    return row.base, ""


#: The most commits a shown base may skip; past it the recorded base is shown, as before 0.31.1.
MOST_SKIPPED = 300


def _read_by_others(ledger: Ledger, row: Row, fresh: str) -> bool:
    """Whether every commit in `row.base..fresh` is accounted for by a verdict of a row other than this one."""
    from flotilla.ledger import batch
    listed = ledger.run(["git", "-C", str(ledger.root), "rev-list", "--count", fresh, f"^{row.base}"],
                        capture_output=True, text=True, check=False)
    if listed.returncode != 0 or not listed.stdout.strip().isdigit() or int(listed.stdout) > MOST_SKIPPED:
        return False
    others = {key: other for key, other in ledger.rows().items() if other.id != row.id}
    return batch.unaccounted(ledger, others, fresh, since=row.base) == []


def shown_base(base: str, moved_from: str, width: int | None = None) -> str:
    """`base <sha>`, and what a rebase moved it from: a field test's reader read a diff from a base two ships old."""
    said = f"base {base[:width] if width else base}"
    return said + (f" (rebased from {moved_from[:7]})" if moved_from else "")


def letter(row: Row, read: tuple[str, str] | None = None) -> str:
    base, moved_from = read if read is not None else (row.base, "")
    base_said = shown_base(base, moved_from, 7) if base else "base unknown"
    return (f"`{row.branch}` is handed to you to read (row {row.id}, owner {row.owner}, tip {row.tip[:7]}, "
            f"{base_said}).\n"
            f"Start with `flotilla work take {row.branch}`. Your verdict is "
            f"`flotilla work accept {row.branch} --reviewed <sha>` or "
            f"`flotilla work fix {row.branch} --why \"<what must change>\"`.")


def take(ledger: Ledger, actor: Actor, branch: str) -> Row:
    require_may(actor, "take", ledger.posts)
    with ledger.session() as s:
        row = s.need_open_row(branch)
        _not_the_author(row, actor, "reads")
        _not_assigned_elsewhere(row, actor)
        state = s.next_state(row, "take")
        return s.append(actor, row.id, "take", state, fields={"reader": actor.name, "taken": True})


def recuse(ledger: Ledger, actor: Actor, branch: str) -> Row:
    require_may(actor, "recuse", ledger.posts)
    with ledger.session() as s:
        row = s.need_open_row(branch)
        if row.reader != actor.name:
            raise MoveRefused(f"`{branch}` is not assigned to {actor.name}")
        state = s.next_state(row, "recuse")
        return s.append(actor, row.id, "recuse", state, fields={"reader": "", "taken": False})


def fix(ledger: Ledger, actor: Actor, branch: str, *, why: str) -> Row:
    require_may(actor, "fix", ledger.posts)
    if not why.strip():
        raise MoveRefused("a return names what must change (--why)")
    with ledger.session() as s:
        row = s.need_open_row(branch)
        _not_the_author(row, actor, "returns")
        _not_assigned_elsewhere(row, actor)
        state = s.next_state(row, "fix")
        return s.append(actor, row.id, "fix", state,
                        fields={"reader": actor.name, "taken": True, "verdict": "", "why": why.strip()},
                        evidence={"why": why.strip()})


def assign(ledger: Ledger, actor: Actor, branch: str, *, reader: str) -> tuple[Row, str]:
    require_may(actor, "assign", ledger.posts)
    live = ledger.live_names()
    try:
        reader_post = post_for_session(ledger.posts, reader)
    except PostError as err:
        raise MoveRefused(str(err)) from err
    with ledger.session() as s:
        row = s.need_open_row(branch)
        state = s.next_state(row, "assign")
        if reader == row.owner:
            raise MoveRefused(f"{reader} is the author of `{branch}`; an author never reads their own work")
        if reader not in live:
            raise MoveRefused(f"{reader} is not alive in the census; assign a live reader")
        if reader_post is None or "accept" not in reader_post.may:
            raise MoveRefused(f"{reader} holds no post that may accept")
        new = s.append(actor, row.id, "assign", state, fields={"reader": reader, "taken": False})
    return new, letter(new, read_from(ledger, new))


def accept(ledger: Ledger, actor: Actor, branch: str, *, reviewed: str) -> Row:
    require_may(actor, "accept", ledger.posts)
    with ledger.session() as s:
        row = s.need_open_row(branch)
        _not_the_author(row, actor, "accepts")
        _not_assigned_elsewhere(row, actor)
        state = s.next_state(row, "accept")
        same = gitq.same_revision(ledger.root, reviewed, row.tip, run=ledger.run)
        if same is None:
            raise MoveRefused(f"git could not resolve `{reviewed}` or the handed tip; nothing is accepted over a "
                              "revision nobody can name")
        if not same:
            raise MoveRefused(f"you read {reviewed}, but the handed tip is {row.tip[:7]}; accept the revision that "
                              "was handed over")
        current = gitq.branch_tip(ledger.root, branch, run=ledger.run)
        if current is None:
            raise MoveRefused(f"git could not resolve the tip of `{branch}`; nothing is accepted over a branch nobody "
                              "can name")
        if current != row.tip:
            raise MoveRefused(f"`{branch}` moved since handover ({row.tip[:7]} -> {current[:7]}); the owner records "
                              "it with `moved` first")
        trunk = gitq.trunk_ref(ledger.root, ledger.trunk, run=ledger.run)
        if gitq.merges_cleanly(ledger.root, trunk, row.tip, run=ledger.run) is False:
            head = gitq.resolve(ledger.root, trunk, run=ledger.run) or trunk
            raise MoveRefused(f"{row.tip[:7]} does not merge with trunk {head[:7]}: the sender could land it only "
                              "by resolving the conflict itself, which nobody would read. return it with `fix` "
                              "(the author merges trunk and hands it over again)")
        return s.append(actor, row.id, "accept", state,
                        fields={"verdict": row.tip, "reader": actor.name, "taken": True},
                        evidence={"reviewed": reviewed})
