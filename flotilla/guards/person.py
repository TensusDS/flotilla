"""The person's own move is refused to Claude's tool calls: `flotilla work approve`.

Where a person authorizes merges, their approval is what lets work reach trunk (decision 183), and the person check
(`caller.person_refusal`) accepts a terminal or an interactive session. When the person's own session leads the
fleet, that session's model passes the check: it runs in an interactive session. What tells the person from the
model there is the door: a Bash tool call fires this hook, a command the person types with `!` does not (measured on
Claude Code 2.1.287). So a tool call that approves is refused, always, with no override and whatever guards the
profile turns on: a session cannot waive what only a person gives.

Seen: the flotilla CLI by any path (`.../scripts/flotilla`, `flotilla`), with the words `work` and then `approve`.
Not seen: what the shell's ceiling hides (`flotilla.guards.CEILING`) - such a call still meets Claude Code's
permission prompt in the person's session.
"""

from __future__ import annotations

import os

from flotilla.guards import Finding

GUARD = "person"


def check(segment) -> Finding | None:
    words = list(segment.words)
    if not words or os.path.basename(words[0]) != "flotilla" or "work" not in words:
        return None
    if "approve" not in words[words.index("work") + 1:]:
        return None
    return Finding(GUARD, True, "flotilla: approving work for trunk is the person's own move, and a Claude tool call "
                                "is never the person's. Show the person the command; they type it with `!` in front "
                                f"in their own Claude Code session (`! {segment.text.strip()}`), or run it in a "
                                "terminal.")
