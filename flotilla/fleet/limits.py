"""A seat stopped on a usage limit (fleet model design 5.6).

Claude Code's `StopFailure` hook fires instead of `Stop` when a turn ends on an API error; its payload names the
error type in `error` (`rate_limit` among them) and carries the rendered error text in `last_assistant_message`
(code.claude.com/docs/en/hooks, StopFailure). The limit's name and its reset are not fields of their own: they are
read from that text in the form Claude Code writes it (2.1.325) -
"You've hit your <name>[ · resets <time>][ · progress saved]", where the name is `session limit` or `5-hour limit`
(Claude Code's own "session" window), `weekly limit`, `<Model> limit` (a model's weekly window), or another that
says nothing of its scope. A text in no such form gives a limit of unknown scope and reset: nothing is guessed.

The record lives in the machine's state directory, one per session, until the session's next normal stop clears it
or it expires: at its reset, or - with no reset read - when the window its name gives would have ended (five hours
for a session limit, a week for a weekly or a model's limit), or after an hour for a limit Claude Code did not name:
mostly a short burst of 429s, after which the seat is a quiet seat again, to be messaged (review of 747c754)."""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from flotilla.core.storage import LocalLogStore, StorageCorrupt
from flotilla.fleet import model

DIRECTORY = "fleet"
KEY = "limits"
REASON = "rate_limit"
ACCOUNT = "account"
UNKNOWN = "unknown"
#: The names Claude Code gives the windows that hold every model: the five-hour one and the weekly one.
SESSION_LIMITS = ("session limit", "5-hour limit")
ACCOUNT_LIMITS = SESSION_LIMITS + ("weekly limit",)
SESSION_WINDOW = dt.timedelta(hours=5)
WEEK_WINDOW = dt.timedelta(days=7)
#: How long a limit Claude Code did not name is kept: long enough to say why a seat went quiet, short enough that a
#: burst of 429s does not hide a dropped ball or refuse a spawn for days.
UNNAMED_KEPT = dt.timedelta(hours=1)
#: A reset Claude Code wrote a minute or so before the turn ended is the one lifting now, not tomorrow's.
JUST_PASSED = dt.timedelta(minutes=5)
#: How far past its window a written reset may lie and still be trusted: a week's reset across a change of
#: daylight saving time is a week and an hour away (second review of 747c754).
WINDOW_SLACK = dt.timedelta(hours=1, minutes=5)
#: A session id longer than this is no Claude Code session's.
LONGEST_ID = 200
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
    expires: str = ""       # when the record goes unless a normal stop clears it first, ISO 8601 in UTC


def parse(payload: dict, *, at: dt.datetime) -> Limit | None:
    """The limit a `StopFailure` payload reports, or None when it reports none: no session, or an error other than
    a rate limit (review focus 4)."""
    session = payload.get("session_id")
    if payload.get("error") != REASON or not isinstance(session, str) or not session or len(session) > LONGEST_ID:
        return None
    text = payload.get("last_assistant_message")
    name, when = _read(text if isinstance(text, str) else "")
    # the longest a limit of this name lasts: a session window, else a week - a reset Claude Code wrote for a limit
    # it did not name is still its own (second review of 747c754)
    window = SESSION_WINDOW if name in SESSION_LIMITS else WEEK_WINDOW
    reset = _reset(when, at)
    slack = WINDOW_SLACK if window == WEEK_WINDOW else JUST_PASSED   # a session window crosses no clock change
    if reset is not None and dt.datetime.fromisoformat(reset) > at + window + slack:
        reset = None   # further off than the window it names can last: misread, not trusted (review of 747c754)
    fallback = window if _scope(name) != UNKNOWN else UNNAMED_KEPT
    expires = dt.datetime.fromisoformat(reset) if reset else at + fallback
    return Limit(session, at.isoformat(timespec="seconds"), reset, _scope(name),
                 expires.astimezone(dt.timezone.utc).isoformat(timespec="seconds"))


def _read(text: str) -> tuple[str, str]:
    """The limit's name and its reset as written, both "" when the text is in no form Claude Code writes."""
    found = _OPENING.search(text)
    if found is None:
        return "", ""
    rest = text[found.end():].splitlines()
    parts = [part.strip() for part in (rest[0] if rest else "").split(" · ")]
    name = parts[0].rstrip(".")
    when = next((part[len("resets "):] for part in parts[1:] if part.startswith("resets ")), "")
    return name, when


def _scope(name: str) -> str:
    if name in ACCOUNT_LIMITS:
        return ACCOUNT
    if name.endswith(" limit") and name[:-len(" limit")].lower() in model.FAMILIES:
        return f"model:{name[:-len(' limit')].lower()}"
    return UNKNOWN


def _reset(when: str, at: dt.datetime) -> str | None:
    """The reset Claude Code wrote: a time within a day ("6pm (UTC)"), else a date ("Nov 18, 6pm (UTC)") with the
    year only when it is not this one. A time written a few minutes before `at` is the reset lifting now (the text
    drops the seconds); a reset otherwise not ahead of `at`, or in a zone this machine does not know, is unknown."""
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
            if reset <= local < reset + JUST_PASSED:
                reset = local + dt.timedelta(minutes=1)
            elif reset <= local:
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
    """Keep `limit` as its session's, replacing an older one, and drop every record no longer current. A damaged log
    is written anew: its records are hints, and a damaged one would otherwise stop every later record."""
    with _store(state).transaction(KEY) as tx:
        kept = {found.session_id: found for found in _limits(_records(tx))}
        kept[limit.session_id] = limit
        tx.replace([asdict(found) for found in _current(kept, _moment(limit.at)).values()])


def clear(state: Path, session_id: str) -> None:
    """The session's turn ended normally: it is not limited. Writes nothing when it held no record."""
    store = _store(state)
    try:
        if not any(found.session_id == session_id for found in _limits(store.read(KEY).records)):
            return
    except StorageCorrupt:
        pass   # written anew below
    with store.transaction(KEY) as tx:
        kept = [found for found in _limits(_records(tx)) if found.session_id != session_id]
        tx.replace([asdict(found) for found in kept])


def current(state: Path, now: dt.datetime) -> dict[str, Limit]:
    """Every session's limit still in force at `now`; none from a damaged log, which the next record writes anew."""
    try:
        records = _store(state).read(KEY).records
    except StorageCorrupt:
        return {}
    return _current({found.session_id: found for found in _limits(records)}, now)


def of_sessions(state: Path, sessions, now: dt.datetime) -> dict[str, Limit]:
    """The limits of the census sessions still in force, by session name. A session no census lists is left out: a
    seat retired while limited says nothing of the seats that run now; so is one the census sees working - its
    limit was lifted, and its turn's end will clear the record (review of 747c754)."""
    found = current(state, now)
    return {session.name: found[session.session_id] for session in sessions or ()
            if session.name and session.session_id in found and getattr(session, "state", "") != "working"}


def label(limit: Limit) -> str:
    """`limited until 2026-11-18 18:00 UTC (the fable model's limit)`, or `limited, reset unknown (...)`."""
    when = _moment(limit.reset)
    head = f"limited until {when:%Y-%m-%d %H:%M} UTC" if when is not None else "limited, reset unknown"
    return f"{head} ({whose(limit)})"


def refusal(limited: dict[str, Limit], families: dict[str, str]) -> tuple[str, list[str]]:
    """Whether raising seats of these posts (post -> the model family each would run on) is refused because a live
    seat stopped on a limit they would stop on at once (fleet model design 5.6): the account's limit refuses every
    model, a model's limit that model; a limit of unknown scope only warns. Each refusal names what it waits for and
    the line that frees it."""
    warnings = []
    for name, found in sorted(limited.items()):
        when = f"it lifts at {label(found).split(' (')[0].removeprefix('limited until ')}" if found.reset \
            else "its reset could not be read"
        sooner = (f"the record goes sooner once {name} ends a turn normally - message it after the limit is "
                  "raised or bought; `--anyway` raises the seat all the same")
        if found.scope == ACCOUNT:
            return (f"{name} stopped on {whose(found)}: a seat raised now would stop at once on any model; {when}, "
                    f"and {sooner}"), warnings
        if found.scope.startswith("model:"):
            family = found.scope[len("model:"):]
            posts = sorted(post for post, its in families.items() if its == family)
            if posts:
                return (f"{name} stopped on {whose(found)}, and {', '.join(posts)} would run on {family}: a seat "
                        f"raised now would stop at once; {when}, or run the post on another model - the `model` of "
                        f"its post file, or `! flotilla fleet model <name>` for the fleet's default; {sooner}"), \
                    warnings
            continue
        warnings.append(f"{name} stopped on {whose(found)} ({label(found)}): a seat raised now may stop at once")
    return "", warnings


def for_raise(state: Path, sessions, families: dict[str, str], now: dt.datetime) -> tuple[str, list[str]]:
    """`refusal` over the census sessions' limits; a record that cannot be read refuses nothing, and says so."""
    try:
        found = of_sessions(state, sessions, now)
    except Exception as err:  # noqa: BLE001 - a hint that cannot be read is no reason to raise nothing
        return "", [f"the usage limits seats stopped on could not be read ({err})"]
    return refusal(found, families)


def whose(limit: Limit) -> str:
    if limit.scope.startswith("model:"):
        return f"the {limit.scope[len('model:'):]} model's limit"
    if limit.scope == ACCOUNT:
        return "the account's session or weekly limit, every model"
    return "a rate limit Claude Code did not name"


def _current(limits: dict[str, Limit], now: dt.datetime | None) -> dict[str, Limit]:
    if now is None:
        return limits
    kept = {}
    for session, found in limits.items():
        expires = _moment(found.expires) or _moment(found.reset)
        if expires is None:   # a record without one: kept as long as a limit Claude Code did not name
            at = _moment(found.at)
            expires = at + UNNAMED_KEPT if at is not None else None
        if expires is not None and expires > now:
            kept[session] = found
    return kept


def _limits(records: list[dict]) -> list[Limit]:
    found = []
    for item in records:
        if not isinstance(item, dict):
            continue
        session, at, reset, scope, expires = (item.get(key) for key in ("session_id", "at", "reset", "scope",
                                                                         "expires"))
        if (isinstance(session, str) and 0 < len(session) <= LONGEST_ID and isinstance(at, str) and _known(scope)
                and (reset is None or isinstance(reset, str)) and isinstance(expires, (str, type(None)))):
            found.append(Limit(session, at, reset, scope, expires or ""))
    return found


def _known(scope) -> bool:
    """A scope this module writes: one read from a hand-edited file is shown to the person, so nothing else is."""
    return scope in (ACCOUNT, UNKNOWN) or (isinstance(scope, str) and scope.startswith("model:")
                                           and scope[len("model:"):] in model.FAMILIES)


def _records(tx) -> list:
    try:
        return tx.read().records
    except StorageCorrupt:
        return []


def _moment(value: str | None) -> dt.datetime | None:
    try:
        found = dt.datetime.fromisoformat(value) if value else None
    except ValueError:
        return None
    return found if found is None or found.tzinfo is not None else None


def _store(state: Path) -> LocalLogStore:
    return LocalLogStore(Path(state) / DIRECTORY)
