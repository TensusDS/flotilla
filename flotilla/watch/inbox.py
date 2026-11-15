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
#: What each group asks of the person, as the listing heads it and the line counts it (singular, plural).
LABELS = {"ship": ("a yes to ship", "yes to ship"), "check": ("a check only you can make", "checks only you can make"),
          "question": ("a question", "questions"), "approve": ("your yes before the batch ships", "yeses before the "
                                                                "batch ships"),
          "merge": ("a pull request only you merge", "pull requests only you merge"),
          "permission": ("a permission question (/flotilla:permit)", "permission questions (/flotilla:permit)"),
          "rig": ("a rig request", "rig requests"),
          "window": ("a session waiting in its own window", "sessions waiting in their own windows"),
          "other": ("other", "other"), "put off": ("put off", "put off")}
APPROVABLE = ("accepted", "queued")
RIG_REQUEST = "rig:request:"


@dataclass(frozen=True)
class Entry:
    key: str      # one question across prompts: "row:<id>@<wait's time>", "row:<id>@hold:<hold's time>",
                  # "approve:<id>@<revision>", "merge:<id>", "question:<session>:<at>", "rig:<request id>",
                  # "window:<session name>"
    group: str    # one of GROUPS
    branch: str
    why: str      # one line, safe to show (core.text.visible)
    since: str    # ISO time, "" when unknown
    by: str       # who recorded it, "" when flotilla found it
    urgent: bool
    blocker: bool


def _last(row, move: str) -> dict:
    return next((step for step in reversed(row.history or []) if step.get("move") == move), {})


def _last_wait(row) -> dict:
    return _last(row, "wait")


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
        # a key per question, never per row: a later question on the row is a new entry, never "shown" by the
        # first one's showing (final review, I3)
        return Entry(f"row:{row.id}@hold:{_last(row, 'hold').get('at', '')}", "put off", row.branch,
                     visible(row.held_why or row.note), since, row.held_by,
                     urgent, urgent)
    if _approval_asked(ctx, row, must_approve):
        from flotilla.ledger.batch import revision_of
        # a blocker is what the design says: urgent, or a session with nothing else to do (review of 5f65ad5, M-2)
        return Entry(f"approve:{row.id}@{revision_of(row)}", "approve", row.branch,
                     "the batch waits for your yes to ship", since, "", urgent, urgent)
    if views.who_moves(row, profile, rows) == "the person who merges the PR":
        return Entry(f"merge:{row.id}", "merge", row.branch, f"merge the pull request{f' {row.pr}' if row.pr else ''}",
                     since, "", urgent, urgent)
    if not _waits_on_person(row):
        return None
    step = _last_wait(row)
    by, post = step.get("by", ""), step.get("post", "")
    blocker = urgent or not (by and _busy(by, post, rows, profile, row.id))
    return Entry(f"row:{row.id}@{step.get('at', '')}", BY_POST.get(post, "other"), row.branch,
                 visible(row.note or "no question recorded"),
                 since, by, urgent, blocker)


def _item_entry(item) -> Entry | None:
    from flotilla.watch.fleet import ALL_LIMITED, OWED, PERSON, QUESTION
    if item.kind == ALL_LIMITED:   # the whole fleet stands still on its limit: wait it out or change the model
        # one entry while the same seats stay limited: a seat limited again gives it a new time, not a new question
        return Entry(f"limited:{item.who}", "other", "", visible(item.text), item.since, "", True, True)
    if item.kind == OWED:   # the fleet stands still until the person answers: urgent, and a blocker
        return Entry(f"owed:{item.who}", "question", "", visible(item.text), item.since, "", True, True)
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
    # a post row is the post held, not a move - unless a wait on the person was recorded on it (final review, I2)
    found = [entry for row in rows.values() if row.is_open and (row.state != "reserved" or _waits_on_person(row))
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


def _breakdown(entries) -> str:
    counted = counts(entries)
    parts = [f"{counted[group]} {LABELS[group][0 if counted[group] == 1 else 1]}" for group in GROUPS
             if counted[group]]
    text = ", ".join(parts)
    return f"{counted['urgent']} urgent; {text}" if counted["urgent"] else text


def lent(rows: dict, live) -> list[tuple[str, str, str]]:
    """The seats on a borrowed model that are up, (name, model, task) - one that said it is done too, until it is
    retired: it runs on that model all the same (fleet model design 5.3)."""
    from flotilla.fleet.model import borrowed, recorded
    found = []
    for name in sorted(live or ()):
        seats = [row for row in rows.values() if row.owner == name and borrowed(row)]
        if seats:
            found.append((name, recorded(seats[-1]) or "another model", borrowed(seats[-1])["for"]))
    return found


def lent_text(seats) -> str:
    if not seats:
        return ""
    if len(seats) == 1:
        return f"1 seat on {seats[0][1]} for {visible(seats[0][2])}"
    return f"{len(seats)} seats on borrowed models ({'; '.join(f'{m} for {visible(t)}' for _, m, t in seats)})"


def lent_marks(seats) -> list[Entry]:
    """The seats on a borrowed model as the shown line's record sees them: the line comes again when they change.
    Never entries of the queue: nothing waits on the person for them."""
    return [Entry(f"lent:{name}:{task}", "other", "", "", "", "", False, False) for name, _, task in seats]


def person_line(entries, seats=()) -> str:
    """The line flotilla puts on the person's own screen; "" when nothing waits on them and no seat runs on a
    borrowed model. Such a seat is counted on a line of its own, never as something that waits on them."""
    lent_said = lent_text(seats)
    if not entries:
        return f"flotilla: {lent_said} - the orchestrator closes it once its task is done" if lent_said else ""
    verb = "waits" if len(entries) == 1 else "wait"
    line = f"flotilla: {len(entries)} {verb} on you ({_breakdown(entries)}) - `flotilla inbox` lists them by number"
    return f"{line}; also {lent_said}" if lent_said else line


def model_line(entries) -> str:
    """The orchestrator's own line, on every prompt: the same count, and where the answer comes from."""
    if not entries:
        return ""
    verb = "waits" if len(entries) == 1 else "wait"
    return (f"flotilla - {len(entries)} {verb} on the person ({_breakdown(entries)}). Answer the person's status "
            "questions from `flotilla inbox`, not from memory; never say nothing waits on them while this count is "
            "above zero. Put a wait off only with `flotilla work hold <branch> --until \"the person\" --why ...`.")
