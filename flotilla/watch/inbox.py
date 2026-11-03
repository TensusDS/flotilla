"""What waits on the person, as one queue (person inbox design, goal 1).

Computed from the ledger and the fleet's items every time and never stored: no memory, file or session removes an
entry, only a ledger move does. The line on the person's screen and `flotilla inbox` both count `collect()`, so the
line never says a number the listing would not reproduce.

An entry's group is what the person is asked to do. A wait on the person goes by the post whose session recorded it
(the why is free text, and the trace found keywords in it a rough guide); a row held until the person is put off,
whatever recorded it, and stays listed as put off.
"""

from __future__ import annotations

from dataclasses import dataclass

from flotilla.core.text import visible
from flotilla.ledger import views

THE_PERSON = "the person"
GROUPS = ("ship", "check", "question", "approve", "merge", "permission", "rig", "window", "other", "put off")
#: The post of the session that recorded a wait, and the group it asks the person for.
BY_POST = {"sender": "ship", "judge": "check", "main": "question", "minor": "question"}
APPROVABLE = ("accepted", "queued")
RIG_REQUEST = "rig:request:"


@dataclass(frozen=True)
class Entry:
    key: str      # the same entry across prompts: "row:<id>", "approve:<id>", "merge:<id>", "question:<session>:<at>",
                  # "rig:<request id>", "window:<session name>"
    group: str    # one of GROUPS
    branch: str
    why: str      # one line, safe to show (core.text.visible)
    since: str    # ISO time, "" when unknown
    by: str       # who recorded it, "" when flotilla found it
    urgent: bool
    blocker: bool


def _last_wait(row) -> dict:
    return next((step for step in reversed(row.history or []) if step.get("move") == "wait"), {})


def _waits_on_person(row) -> bool:
    return row.waiting_on.strip().lower() == THE_PERSON


def _busy(name: str, post: str, rows: dict, profile: dict, but: str) -> bool:
    """Whether `name` holds another move it can make now: not this row, not a post seat, not waiting, not held."""
    from flotilla.watch.whose import holds_move
    return any(other.id != but and other.is_open and other.state != "reserved" and not other.waiting_on
               and not other.held_until and holds_move(other, profile, name, post, rows) for other in rows.values())


def _approval_asked(ctx, row, must_approve) -> bool:
    if row.state not in APPROVABLE or (ctx.profile.get("flow") or {}).get("merge_authorized_by") != "human":
        return False
    from flotilla.ledger import batch
    if batch.approved(row):
        return False
    try:
        return bool(must_approve(row))
    except Exception:  # noqa: BLE001 - a yes that cannot be asked about is still owed: counted, never dropped
        return True


def _row_entry(ctx, row, rows: dict, must_approve) -> Entry | None:
    profile = ctx.profile
    urgent = bool(row.urgent_at)
    since = row.updated_at
    if row.held_until.strip().lower() == THE_PERSON:
        return Entry(f"row:{row.id}", "put off", row.branch, visible(row.held_why or row.note), since, row.held_by,
                     urgent, urgent)
    if _approval_asked(ctx, row, must_approve):
        return Entry(f"approve:{row.id}", "approve", row.branch, "the batch waits for your yes to ship", since, "",
                     urgent, True)
    if views.who_moves(row, profile, rows) == "the person who merges the PR":
        return Entry(f"merge:{row.id}", "merge", row.branch, f"merge the pull request{f' {row.pr}' if row.pr else ''}",
                     since, "", urgent, True)
    if not _waits_on_person(row):
        return None
    step = _last_wait(row)
    by, post = step.get("by", ""), step.get("post", "")
    blocker = urgent or not (by and _busy(by, post, rows, profile, row.id))
    return Entry(f"row:{row.id}", BY_POST.get(post, "other"), row.branch, visible(row.note or "no question recorded"),
                 since, by, urgent, blocker)


def _item_entry(item) -> Entry | None:
    from flotilla.watch.fleet import PERSON, QUESTION
    if item.kind == QUESTION:
        return Entry(f"question:{item.branch}:{item.who}", "permission", "", visible(item.text), item.since,
                     item.branch, False, True)
    if item.kind == "rig" and item.who.startswith(RIG_REQUEST):
        return Entry(f"rig:{item.who[len(RIG_REQUEST):]}", "rig", "", visible(item.text), item.since, "", False,
                     False)
    if item.kind == PERSON and not item.branch:   # a census session waiting at its own prompt; rows come from rows
        return Entry(f"window:{item.who}", "window", "", visible(item.text), item.since, item.who, False, True)
    return None


def covers(item) -> bool:
    """Whether a fleet item is in the person's queue, and so counted in its line rather than folded as standing."""
    from flotilla.watch.fleet import PERSON
    return item.kind == PERSON or _item_entry(item) is not None


def collect(ctx, *, fleet_items=None, must_approve=None) -> list[Entry]:
    """The person's queue: urgent first, then by group, then oldest first. `fleet_items` defaults to `ctx.fleet()`;
    `must_approve(row)` to the batch's own question, which asks git."""
    if ctx.ledger is None:
        return []
    rows = ctx.rows
    if must_approve is None:
        from flotilla.ledger import batch

        def must_approve(row):
            return batch.person_must_approve(ctx.ledger, row)
    found = [entry for row in rows.values() if row.is_open and row.state != "reserved"
             for entry in [_row_entry(ctx, row, rows, must_approve)] if entry is not None]
    items = ctx.fleet() if fleet_items is None else fleet_items
    found += [entry for item in items for entry in [_item_entry(item)] if entry is not None]
    return sorted(found, key=lambda entry: (not entry.urgent, GROUPS.index(entry.group), entry.since or "~"))


def counts(entries) -> dict[str, int]:
    counted = {group: 0 for group in GROUPS}
    for entry in entries:
        counted[entry.group] += 1
    counted["urgent"] = sum(entry.urgent for entry in entries)
    counted["blocker"] = sum(entry.blocker for entry in entries)
    counted["total"] = len(entries)
    return counted
