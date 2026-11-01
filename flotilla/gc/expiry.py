"""The archive's lifetime, gc design section 7. An entry lives a month; three days before it ends the person is asked
once a day whether its media may go. Only media expires - scripts, logs, json, notes, patches and parked commits stay.
Three ignored days keep everything another month, unless the disk runs low, which asks at once. Nothing leaves without
the person's recorded yes, and a yes covers exactly the files shown with it; it is carried out on the day the question
named (`carry_out`, in the daily pass or `flotilla gc apply`).

What the collector keeps for this, in the gc store's `expiry`: `until` (the month moved by keep or by silence),
`asked` (the days the shown set was asked, and its fingerprint) and `answer` (value, the files shown, when, which
question).
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timedelta, timezone

from flotilla.core.text import visible
from flotilla.gc import archive
from flotilla.gc.media import is_media
from flotilla.gc.sweep import size_text
from flotilla.onboard.questions import AnswerError, _q, validate_answer

MONTH_DAYS = 30
AHEAD_DAYS = 3
IGNORED = 3
KEY = "expiry"


def _when(text: str) -> datetime | None:
    try:
        found = datetime.fromisoformat(str(text))
    except ValueError:
        return None
    return found if found.tzinfo else found.replace(tzinfo=timezone.utc)


def _iso(when: datetime) -> str:
    return when.astimezone(timezone.utc).isoformat(timespec="seconds")


def _state(data: dict) -> dict:
    found = data.setdefault(KEY, {})
    return found if isinstance(found, dict) else data.setdefault(KEY, {})


def _end(entry, data: dict) -> datetime | None:
    """When the entry's month ends: a month after it came, or later where keep or silence moved it."""
    came = _when(entry.when)
    if came is None:
        return None
    end = came + timedelta(days=MONTH_DAYS)
    until = _when(_state(data).get("until", "")) if _state(data).get("until") else None
    return max(end, until) if until else end


def _triple(entry, record) -> list:
    return [entry.id, str(record.get("sha256", "")), str(record.get("from", ""))]


def _covered(data: dict) -> set:
    """The files a recorded yes was given for: they are not asked about again."""
    answer = _state(data).get("answer") or {}
    return {tuple(item) for item in answer.get("shown", [])} if answer.get("value") == "yes" else set()


def due(entries, *, now: datetime, low: bool, data: dict) -> list[tuple]:
    """Media records not expired whose month ends within three days (every one, with a low disk), not already covered
    by a recorded yes."""
    covered = _covered(data)
    found = []
    for entry in entries:
        end = _end(entry, data)
        if end is None or (not low and end - now > timedelta(days=AHEAD_DAYS)):
            continue
        for record in entry.files:
            if "expired" in record or not is_media(str(record.get("from", ""))):
                continue
            if tuple(_triple(entry, record)) not in covered:
                found.append((entry, record))
    return found


def fingerprint(found) -> str:
    return hashlib.sha256(json.dumps(sorted(_triple(entry, record) for entry, record in found)).encode()).hexdigest()


def question(found, *, entries, now: datetime, low: bool = False, data: dict | None = None) -> dict | None:
    if not found:
        return None
    size = sum(int(record.get("size", 0)) for _, record in found)
    ids = sorted({entry.id for entry, _ in found})
    ends = [end for end in (_end(entry, data or {}) for entry, _ in found) if end is not None]
    days = max(0, math.ceil((min(ends) - now) / timedelta(days=1))) if ends else 0
    when = "the disk is low, so once you say yes I remove" if low else f"in {days} days I remove"
    parked = len({record.get("head") for entry in entries for record in entry.trees if record.get("head")})
    shown = ", ".join(ids[:3]) + (f" and {len(ids) - 3} more" if len(ids) > 3 else "")
    text = (f"{when} {size_text(size)} of media from the archive: {len(found)} file(s) in {len(ids)} "
            f"entr{'y' if len(ids) == 1 else 'ies'} ({shown}, list: flotilla gc archive); scripts, logs, notes and "
            f"{parked} parked commit(s) stay unless you say")
    return _q(f"{KEY}@{fingerprint(found)[:12]}", "Archive", visible(text), [
        ("yes", "Remove them", "The media files shown leave the archive on the day named; nothing else does."),
        ("keep", "Keep another month", "Everything stays a month more; asked again then.")], free_text=True)


def raised(data: dict, found, *, now: datetime, low: bool) -> bool:
    """Whether to ask now; once a day per shown set. Three days asked with no answer keep the archive a month more,
    unless the disk is low."""
    if not found:
        return False
    state = _state(data)
    shown = fingerprint(found)
    if state.get("asked_for") != shown:
        state["asked_for"], state["asked"] = shown, []
    asked, today = state.setdefault("asked", []), now.date().isoformat()
    if low:
        if today not in asked:
            asked.append(today)
        return True
    if today in asked:
        return True
    if len(asked) >= IGNORED:
        state["until"] = _iso(now + timedelta(days=MONTH_DAYS))
        state["asked"] = []
        return False
    asked.append(today)
    return True


def record(data: dict, found, qid: str, values: list[str], *, now: datetime, low: bool = False) -> str:
    """The person's answer to the expiry question, bound to the files it showed."""
    asked = question(found, entries=[], now=now, low=low)
    if asked is None or asked["id"] != qid:
        raise AnswerError(f"`{visible(qid)}` is not the archive question that applies now; run `flotilla gc next`")
    value = validate_answer(asked, values)
    if value not in ("yes", "keep"):
        raise AnswerError("the archive question takes yes or keep")
    _state(data)["answer"] = {"value": value, "seen": fingerprint(found), "id": qid, "at": _iso(now),
                              "shown": sorted(_triple(entry, record) for entry, record in found)}
    return f"recorded {KEY}: {value}"


def carry_out(ledger, data: dict, *, now: datetime, low: bool = False) -> list[str]:
    """Carry out a recorded answer: keep moves the month; yes removes each shown media file once its month ended (at
    once with a low disk), and deletes its stored object when no record still needing it points at it."""
    state = _state(data)
    answer = state.get("answer") or {}
    if answer.get("value") == "keep":
        state["until"] = _iso(now + timedelta(days=MONTH_DAYS))
        state["asked"] = []
        state.pop("answer")
        return ["the archive's media stays another month, as you said"]
    if answer.get("value") != "yes":
        return []
    listed = [list(item) for item in answer.get("shown", []) if isinstance(item, list)]
    seen = hashlib.sha256(json.dumps(sorted(listed)).encode()).hexdigest()
    if seen != answer.get("seen") or answer.get("id") != f"{KEY}@{seen[:12]}":
        state.pop("answer", None)   # a record that does not hold together is no answer (security review of 96debba)
        return []
    shown = {tuple(item) for item in listed}
    root = archive.root_of(ledger)
    known = archive.entries(ledger)
    approved = f"{answer.get('id', KEY)} at {answer.get('at', '')}"
    removed, size, left, touched = [], 0, 0, []
    for entry in known:
        end = _end(entry, data)
        changed = False
        for item in entry.files:
            if tuple(_triple(entry, item)) not in shown or "expired" in item or not is_media(str(item.get("from"))):
                continue   # only media ever expires, whatever a record of the answer says
            if not low and (end is None or end > now):
                left += 1
                continue
            item["expired"] = {"at": _iso(now), "approved_by": approved}
            removed.append(item)
            size += int(item.get("size", 0))
            changed = True
        if changed:
            touched.append(entry)
    if not removed:
        return []
    for entry in touched:
        archive._save(root, entry)
    with open(root / "MANIFEST", "a", encoding="utf-8") as manifest:
        for item in removed:
            manifest.write(f"{item['sha256']}  expired  <-  {visible(str(item['from']))}\n")
    needed = {item.get("sha256") for entry in known for item in entry.files if "expired" not in item}
    for digest in {item["sha256"] for item in removed} - needed:
        if archive.DIGEST.fullmatch(str(digest)):
            try:
                archive._object(root, digest).unlink()
            except FileNotFoundError:
                pass
    if not left:
        state.pop("answer", None)
        state["asked"] = []
    return [f"removed {size_text(size)} of media from the archive ({len(removed)} file(s)), approved by the "
            f"person's answer of {str(answer.get('at', ''))[:10]}"]
