"""A seat stopped on a usage limit (fleet model design 5.6): what Claude Code's `StopFailure` hook carries, read into
a record, and the record kept until the seat's next normal stop or the limit's reset.

The payload's fields are documented (code.claude.com/docs/en/hooks, StopFailure): `error` names the error type, and
`last_assistant_message` holds "the rendered error text shown in the conversation". The limit's name and its reset
are not fields: they are read from that text in the form Claude Code 2.1.325 writes it -
"You've hit your <name>[ · resets <time>][ · progress saved]" - and anything else reads as reset unknown, scope
unknown. Nothing is guessed."""

import datetime as dt

import pytest

from flotilla.fleet import limits

UTC = dt.timezone.utc
AT = dt.datetime(2026, 11, 13, 17, 5, tzinfo=UTC)


def payload(text=None, error="rate_limit", **extra):
    body = {"session_id": "seat-1", "hook_event_name": "StopFailure", "error": error, **extra}
    if text is not None:
        body["last_assistant_message"] = text
    return body


def test_rate_limit_payload_records(tmp_path):
    found = limits.parse(payload("You've hit your Fable limit · resets Nov 18, 6pm (UTC)"), at=AT)
    assert found == limits.Limit("seat-1", AT.isoformat(timespec="seconds"), "2026-11-18T18:00:00+00:00",
                                 "model:fable", "2026-11-18T18:00:00+00:00")
    limits.record(tmp_path, found)
    assert limits.current(tmp_path, AT) == {"seat-1": found}


@pytest.mark.parametrize("body", [
    {"session_id": "seat-1", "last_assistant_message": "You've hit your Fable limit · resets Nov 18, 6pm (UTC)"},
    payload("API Error: 529 overloaded", error="overloaded"),
    payload("You've hit your Fable limit · resets Nov 18, 6pm (UTC)", error=""),
    payload("You've hit your Fable limit", error=None),
])
def test_payload_without_reason_records_nothing(body):
    """Review focus 4: no rate-limit reason, no record - the seat stays as the census says."""
    assert limits.parse(body, at=AT) is None


def test_a_payload_without_a_session_records_nothing():
    assert limits.parse({"error": "rate_limit", "last_assistant_message": "You've hit your weekly limit"},
                        at=AT) is None


@pytest.mark.parametrize("text, scope", [
    ("You've hit your Fable limit · resets Nov 18, 6pm (UTC)", "model:fable"),
    ("You've hit your Opus limit · resets Nov 18, 6pm (UTC) · progress saved", "model:opus"),
    ("You've hit your Sonnet limit · resets Nov 18, 6pm (UTC)", "model:sonnet"),
])
def test_model_limit_text_gives_model_scope(text, scope):
    found = limits.parse(payload(text), at=AT)
    assert found.scope == scope and found.reset == "2026-11-18T18:00:00+00:00"


@pytest.mark.parametrize("text", [
    "You've hit your session limit · resets 10pm (UTC)",
    "You've hit your 5-hour limit · resets 10pm (UTC)",
    "You've hit your weekly limit · resets Nov 18, 6pm (UTC)",
])
def test_session_limit_text_gives_account_scope(text):
    assert limits.parse(payload(text), at=AT).scope == "account"


@pytest.mark.parametrize("text", [
    "API Error: Rate limit reached",            # the documented example: a rate limit, nothing more said
    "You've hit your usage limit",              # Claude Code's own fallback when it does not know the window
    "You've hit your usage credit limit · resets Dec 1, 12am (UTC)",
    "You've hit your monthly spend limit.",
    "",
])
def test_a_limit_the_text_does_not_name_reads_unknown(text):
    found = limits.parse(payload(text), at=AT)
    assert found.scope == "unknown"


def test_a_payload_without_text_records_a_limit_of_unknown_scope_and_reset():
    found = limits.parse(payload(), at=AT)
    assert (found.scope, found.reset) == ("unknown", None)


@pytest.mark.parametrize("text, reset", [
    ("You've hit your session limit · resets 10pm (UTC)", "2026-11-13T22:00:00+00:00"),       # later today
    ("You've hit your weekly limit · resets 4:30pm (UTC)", "2026-11-14T16:30:00+00:00"),      # past today: tomorrow
    ("You've hit your weekly limit · resets 12am (UTC)", "2026-11-14T00:00:00+00:00"),
    ("You've hit your weekly limit · resets 12pm (UTC)", "2026-11-14T12:00:00+00:00"),
    ("You've hit your weekly limit · resets Nov 18, 6:15pm (Europe/Berlin)", "2026-11-18T17:15:00+00:00"),
    ("You've hit your session limit · resets 5:02pm (UTC)", "2026-11-13T17:06:00+00:00"),   # as it lifts
])
def test_the_reset_is_read_in_the_zone_it_names(text, reset):
    assert limits.parse(payload(text), at=AT).reset == reset


def test_a_reset_in_another_year_names_it():
    at = dt.datetime(2026, 12, 30, 10, 0, tzinfo=UTC)
    found = limits.parse(payload("You've hit your weekly limit · resets Jan 2, 2027, 9am (UTC)"), at=at)
    assert found.reset == "2027-01-02T09:00:00+00:00"


@pytest.mark.parametrize("text", [
    "You've hit your session limit · resets 11pm (UTC)",              # six hours off: no session window lasts so
    "You've hit your weekly limit · resets Nov 18, 2099, 6pm (UTC)",  # nor any week
    "You've hit your Fable limit · resets Dec 1, 6pm (UTC)",
])
def test_a_reset_beyond_the_window_it_names_is_not_trusted(text):
    found = limits.parse(payload(text), at=AT)
    assert found.reset is None and found.scope != "unknown"


@pytest.mark.parametrize("text", [
    "You've hit your weekly limit · resets Nov 18, 6pm (Mars/Olympus)",   # a zone this machine does not know
    "You've hit your weekly limit · resets Feb 30, 6pm (UTC)",
    "You've hit your weekly limit · resets soon",
    "You've hit your weekly limit · resets 25pm (UTC)",
    # Claude Code names the year only when it is not this one: a date without one that has passed is no reset
    "You've hit your weekly limit · resets Jan 2, 9am (UTC)",
])
def test_a_reset_that_cannot_be_read_is_unknown_not_guessed(text):
    found = limits.parse(payload(text), at=AT)
    assert found.reset is None and found.scope == "account"


def test_stop_clears(tmp_path):
    limits.record(tmp_path, limits.parse(payload("You've hit your Fable limit · resets Nov 18, 6pm (UTC)"), at=AT))
    other = limits.parse(payload("You've hit your weekly limit", session_id="seat-2"), at=AT)
    limits.record(tmp_path, other)
    limits.clear(tmp_path, "seat-1")
    assert limits.current(tmp_path, AT) == {"seat-2": other}


def test_clearing_a_session_with_no_record_writes_nothing(tmp_path):
    limits.clear(tmp_path, "seat-1")
    assert not any(tmp_path.rglob("*.jsonl"))


def test_a_newer_limit_replaces_the_older_one(tmp_path):
    limits.record(tmp_path, limits.parse(payload("You've hit your session limit · resets 10pm (UTC)"), at=AT))
    later = AT + dt.timedelta(hours=6)
    newer = limits.parse(payload("You've hit your Fable limit · resets Nov 18, 6pm (UTC)"), at=later)
    limits.record(tmp_path, newer)
    assert limits.current(tmp_path, later) == {"seat-1": newer}


def test_a_limit_past_its_reset_is_dropped(tmp_path):
    found = limits.parse(payload("You've hit your session limit · resets 10pm (UTC)"), at=AT)
    limits.record(tmp_path, found)
    assert limits.current(tmp_path, dt.datetime(2026, 11, 13, 21, 59, tzinfo=UTC)) == {"seat-1": found}
    assert limits.current(tmp_path, dt.datetime(2026, 11, 13, 22, 0, tzinfo=UTC)) == {}


@pytest.mark.parametrize("text, kept", [
    ("You've hit your session limit", dt.timedelta(hours=5)),
    ("You've hit your 5-hour limit · progress saved", dt.timedelta(hours=5)),
    ("You've hit your weekly limit", dt.timedelta(days=7)),
    ("You've hit your Opus limit", dt.timedelta(days=7)),
    ("API Error: Rate limit reached", dt.timedelta(hours=1)),   # mostly a burst of 429s: a quiet seat again soon
])
def test_a_limit_with_no_reset_is_kept_as_long_as_its_window(tmp_path, text, kept):
    """Review of 747c754: a week for every limit kept a session limit's refusal, or a 429's mark, for days."""
    found = limits.parse(payload(text), at=AT)
    limits.record(tmp_path, found)
    assert limits.current(tmp_path, AT + kept - dt.timedelta(minutes=1)) == {"seat-1": found}
    assert limits.current(tmp_path, AT + kept) == {}


def test_a_text_that_ends_after_its_opening_is_a_limit_of_unknown_scope():
    found = limits.parse(payload("You've hit your "), at=AT)
    assert (found.scope, found.reset) == ("unknown", None)


def test_a_session_id_no_session_has_records_nothing():
    assert limits.parse(payload("You've hit your weekly limit", session_id="x" * 201), at=AT) is None


def test_the_census_decides_whose_limit_is_shown(tmp_path):
    """A seat no census lists - retired while limited - refuses nothing; one the census sees working had its
    limit lifted, and the end of its turn clears the record."""
    from types import SimpleNamespace
    for sid in ("seat-1", "seat-2", "seat-3"):
        limits.record(tmp_path, limits.parse(payload("You've hit your weekly limit", session_id=sid), at=AT))
    sessions = [SimpleNamespace(name="a", session_id="seat-1", state="blocked"),
                SimpleNamespace(name="b", session_id="seat-2", state="working")]
    assert list(limits.of_sessions(tmp_path, sessions, AT)) == ["a"]


def test_a_record_keeps_the_log_short(tmp_path):
    """Each write rewrites the log with only the records still current: it does not grow with every limit."""
    for day in range(30):
        at = AT + dt.timedelta(days=day)
        limits.record(tmp_path, limits.parse(payload("You've hit your session limit · resets 10pm (UTC)",
                                                     session_id=f"seat-{day}"), at=at))
    lines = (tmp_path / limits.DIRECTORY / f"{limits.KEY}.jsonl").read_text().splitlines()
    assert len(lines) == 1


def test_a_foreign_line_or_scope_is_skipped(tmp_path):
    found = limits.parse(payload("You've hit your Fable limit · resets Nov 18, 6pm (UTC)"), at=AT)
    limits.record(tmp_path, found)
    log = tmp_path / limits.DIRECTORY / f"{limits.KEY}.jsonl"
    log.write_text(log.read_text() + '{"session_id": 3}\n{"kind": "other"}\n'
                   '{"session_id": "s", "at": "2026-11-13T17:05:00+00:00", "reset": null, "scope": "\\u001b[31m"}\n')
    assert limits.current(tmp_path, AT) == {"seat-1": found}


def test_a_damaged_log_is_written_anew_by_the_next_record(tmp_path):
    """Review of 747c754: one damaged line stopped every later record and clear, with nothing said."""
    log = tmp_path / limits.DIRECTORY / f"{limits.KEY}.jsonl"
    log.parent.mkdir(parents=True)
    log.write_text("{not json\n")
    assert limits.current(tmp_path, AT) == {}
    found = limits.parse(payload("You've hit your weekly limit"), at=AT)
    limits.record(tmp_path, found)
    assert limits.current(tmp_path, AT) == {"seat-1": found}
    log.write_text("{not json\n")
    limits.clear(tmp_path, "seat-1")
    assert limits.current(tmp_path, AT) == {} and log.read_text() == ""
