"""The window a seat compacts at (seat compaction design, section 1).

A seat's context is re-read on every request; Claude Code compacts it only near the window it is given, about 967k
tokens on a 1M model by default. Each post names its own window (`compact_window` in its frontmatter); a post that
does not name one gets 400k for the orchestrator, whose memory holds the fleet together, and 200k for the rest. The
window rides into the seat's `--settings` as `autoCompactWindow`, which Claude Code caps at the model's own window.
"""

from __future__ import annotations

#: Below this, ordinary tool output in a seat risks Claude Code's thrash breaker: a context that refills to the
#: window within three turns of a compaction, three times in a row, ends the turn with an error (measured at 100k on
#: 2.1.329, where compaction started ~25-30k below the window). Claude Code's own floor is 100,000.
FLOOR = 150_000
DEFAULT = 200_000
DEFAULTS = {"orchestrator": 400_000}
OFF = "off"


def parsed(value) -> int | str:
    """A frontmatter value as a window: a whole number at or above the floor, or `off`; anything else raises
    ValueError with the reason."""
    if value == OFF:
        return OFF
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"must be a whole number of tokens, such as 200000, or `{OFF}` for Claude Code's default")
    if value < FLOOR:
        raise ValueError(f"must be at least {FLOOR}: below it ordinary tool output can make Claude Code's compaction "
                         "thrash and stop the seat, or `off` for Claude Code's default")
    return value


def window_for(post) -> int | None:
    """The window a seat of this post compacts at; None for `off` (no setting passed)."""
    written = getattr(post, "compact_window", "")
    if written == "":
        return DEFAULTS.get(post.name, DEFAULT)
    value = parsed(written)
    return None if value == OFF else value


def shown(window: int | None) -> str:
    if window is None:
        return "compacts at Claude Code's default"
    return f"compacts near {window // 1000}k"


#: What `recorded` gives for a seat whose reserve event names no window: raised by a flotilla before 0.34.
UNKNOWN = "unknown"


def with_window(settings_json: str, window: int | None) -> str:
    """The seat's `--settings` JSON with `autoCompactWindow` added; every other key kept; unchanged for None."""
    if window is None:
        return settings_json
    import json
    data = json.loads(settings_json) if settings_json else {}
    data["autoCompactWindow"] = window
    return json.dumps(data)


def of_command(command: list[str]) -> str:
    """The window a launch command carries, as a dry run says it (`--settings` itself is hidden there)."""
    import json
    if "--settings" not in command:
        return shown(None)
    value = json.loads(command[command.index("--settings") + 1]).get("autoCompactWindow")
    return shown(value if isinstance(value, int) else None)


def evidence(window: int | None) -> int | str:
    """The window as the seat's reserve event records it."""
    return OFF if window is None else window


def recorded(row) -> int | None | str:
    """The window a seat's post row says it was raised with: a number, None for `off`, UNKNOWN when its reserve
    event names none (raised before 0.34, so on Claude Code's default until raised again)."""
    for event in row.history:
        if event.get("move") == "reserve":
            value = (event.get("evidence") or {}).get("compact_window")
            if value == OFF:
                return None
            if isinstance(value, int) and not isinstance(value, bool):
                return value
            return UNKNOWN
    return UNKNOWN


def fleet_note(window: int | None | str) -> str:
    if window == UNKNOWN:
        return "raised before compaction windows; retire and raise it again to apply its post's"
    return shown(window)
