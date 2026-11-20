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
