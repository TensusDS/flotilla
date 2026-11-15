"""A seat stopped on a usage limit (fleet model design 5.6).

Claude Code's `StopFailure` hook fires instead of `Stop` when a turn ends on an API error; its payload names the
error type in `error` (`rate_limit` among them) and carries the rendered error text in `last_assistant_message`
(code.claude.com/docs/en/hooks, StopFailure). The limit's name and its reset are not fields of their own: they are
read from that text in the form Claude Code writes it (2.1.325) -
"You've hit your <name>[ · resets <time>][ · progress saved]", where the name is `session limit` or `5-hour limit`
(Claude Code's own "session" window), `weekly limit`, `<Model> limit` (a model's weekly window), or another that
says nothing of its scope. A text in no such form gives a limit of unknown scope and reset: nothing is guessed.

The record lives in the machine's state directory, one per session, until the session's next normal stop clears it,
its reset passes, or - with no reset read - a week goes by: no window Claude Code names is longer, and a seat
retired while limited never stops again."""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from flotilla.core.storage import LocalLogStore
from flotilla.fleet import model

DIRECTORY = "fleet"
KEY = "limits"
REASON = "rate_limit"
#: How long a limit with no reset read is kept: Claude Code's longest window is a week.
UNKNOWN_RESET_KEPT = dt.timedelta(days=7)
ACCOUNT = "account"
UNKNOWN = "unknown"
#: The names Claude Code gives the windows that hold every model: the five-hour one and the weekly one.
ACCOUNT_LIMITS = ("session limit", "5-hour limit", "weekly limit")
_OPENING = re.compile(r"You[’']ve hit your ")
_RESET = re.compile(r"^(?:(?P<month>[A-Z][a-z]{2}) (?P<day>\d{1,2}), (?:(?P<year>\d{4}), )?)?"
                    r"(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?(?P<half>am|pm) \((?P<zone>[^()]+)\)$")
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


@dataclass(frozen=True)
class Limit:
    session_id: str
    at: str                 # when the turn ended on it, ISO 8601 with its offset
    reset: str | None       # when the limit lifts, ISO 8601 in UTC; None when the text did not say or could not be read
    scope: str              # "model:<family>", "account" or "unknown"


def parse(payload: dict, *, at: dt.datetime) -> Limit | None:
    """The limit a `StopFailure` payload reports, or None when it reports none: no session, or an error other than
    a rate limit (review focus 4)."""
    session = payload.get("session_id")
    if payload.get("error") != REASON or not isinstance(session, str) or not session:
        return None
    text = payload.get("last_assistant_message")
    name, reset = _read(text if isinstance(text, str) else "", at)
    return Limit(session, at.isoformat(timespec="seconds"), reset, _scope(name))


def _read(text: str, at: dt.datetime) -> tuple[str, str | None]:
    found = _OPENING.search(text)
    if found is None:
        return "", None
    parts = [part.strip() for part in text[found.end():].splitlines()[0].split(" · ")]
    name = parts[0].rstrip(".")
    when = next((part[len("resets "):] for part in parts[1:] if part.startswith("resets ")), "")
    return name, _reset(when, at)


def _scope(name: str) -> str:
    if name in ACCOUNT_LIMITS:
        return ACCOUNT
    if name.endswith(" limit") and name[:-len(" limit")].lower() in model.FAMILIES:
        return f"model:{name[:-len(' limit')].lower()}"
    return UNKNOWN


def _reset(when: str, at: dt.datetime) -> str | None:
    """The reset Claude Code wrote: a time within a day ("6pm (UTC)"), else a date ("Nov 18, 6pm (UTC)") with the
    year only when it is not this one. A reset that is not ahead of `at`, or in a zone this machine does not know,
    is unknown."""
    found = _RESET.match(when.strip())
    if found is None:
        return None
    zone = _zone(found["zone"])
    hour, minute = int(found["hour"]), int(found["minute"] or 0)
    if zone is None or not 1 <= hour <= 12 or minute > 59:
        return None
    hour = hour % 12 + (12 if found["half"] == "pm" else 0)
    local = at.astimezone(zone)
    try:
        if found["month"] is None:
            reset = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if reset <= local:
                reset += dt.timedelta(days=1)
        else:
            if found["month"] not in _MONTHS:
                return None
            year = int(found["year"]) if found["year"] else local.year
            reset = dt.datetime(year, _MONTHS.index(found["month"]) + 1, int(found["day"]), hour, minute,
                                tzinfo=zone)
    except ValueError:   # Feb 30
        return None
    if reset <= local:
        return None
    return reset.astimezone(dt.timezone.utc).isoformat(timespec="seconds")


def _zone(name: str) -> dt.tzinfo | None:
    if name == "UTC":
        return dt.timezone.utc
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:  # noqa: BLE001 - ZoneInfoNotFoundError, a malformed key, no tz database: unknown, not guessed
        return None


def record(state: Path, limit: Limit) -> None:
    """Keep `limit` as its session's, replacing an older one, and drop every record no longer current."""
    with _store(state).transaction(KEY) as tx:
        kept = {found.session_id: found for found in _limits(tx.read().records)}
        kept[limit.session_id] = limit
        tx.replace([asdict(found) for found in _current(kept, _moment(limit.at)).values()])


def clear(state: Path, session_id: str) -> None:
    """The session's turn ended normally: it is not limited. Writes nothing when it held no record."""
    store = _store(state)
    if not any(found.session_id == session_id for found in _limits(store.read(KEY).records)):
        return
    with store.transaction(KEY) as tx:
        kept = [found for found in _limits(tx.read().records) if found.session_id != session_id]
        tx.replace([asdict(found) for found in kept])


def current(state: Path, now: dt.datetime) -> dict[str, Limit]:
    """Every session's limit still in force at `now`."""
    return _current({found.session_id: found for found in _limits(_store(state).read(KEY).records)}, now)


def _current(limits: dict[str, Limit], now: dt.datetime | None) -> dict[str, Limit]:
    if now is None:
        return limits
    kept = {}
    for session, found in limits.items():
        reset, at = _moment(found.reset), _moment(found.at)
        if reset is not None and reset > now or reset is None and at is not None and at + UNKNOWN_RESET_KEPT > now:
            kept[session] = found
    return kept


def _limits(records: list[dict]) -> list[Limit]:
    found = []
    for item in records:
        if not isinstance(item, dict):
            continue
        session, at, reset, scope = (item.get(key) for key in ("session_id", "at", "reset", "scope"))
        if (isinstance(session, str) and session and isinstance(at, str) and isinstance(scope, str)
                and (reset is None or isinstance(reset, str))):
            found.append(Limit(session, at, reset, scope))
    return found


def _moment(value: str | None) -> dt.datetime | None:
    try:
        found = dt.datetime.fromisoformat(value) if value else None
    except ValueError:
        return None
    return found if found is None or found.tzinfo is not None else None


def _store(state: Path) -> LocalLogStore:
    return LocalLogStore(Path(state) / DIRECTORY)
