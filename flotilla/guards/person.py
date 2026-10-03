"""The person's own move is refused to Claude's tool calls: `flotilla work approve`.

Where a person authorizes merges, their approval is what lets work reach trunk (decision 183), and the person check
(`caller.person_refusal`) accepts a terminal or an interactive session. When the person's own session leads the
fleet, that session's model passes the check: it runs in an interactive session. What tells the person from the
model there is the door: a Bash tool call fires this hook, a command the person types with `!` does not (measured on
Claude Code 2.1.287). So a tool call that approves is refused, always, with no override and whatever guards the
profile turns on: a session cannot waive what only a person gives.

Seen: the flotilla CLI anywhere in the segment - by any path, behind an interpreter or a wrapper (`python3 .../flotilla`,
`timeout 60 .../flotilla`), or as the module (`-m flotilla.cli`) - whose move, the first word after `work` that names
one, is `approve`. Quotes and backslashes inside the word are read as the shell reads them. Not seen: what the shell's
ceiling hides (`flotilla.guards.CEILING`) and Python handed the code as text (`python3 -c ...`) - such a call still
meets Claude Code's permission prompt in the person's session.
"""

from __future__ import annotations

import fnmatch
import os

from flotilla.guards import Finding

GUARD = "person"
#: Every move `flotilla work` takes; a test holds it equal to the CLI's own list.
MOVES = ("accept", "adopt", "approve", "assign", "broke", "claim", "close", "fix", "hand", "hold", "inbatch", "land",
         "moved", "offledger", "queue", "reconcile", "recuse", "release", "reserve", "return", "ship", "show", "take", "unbroke",
         "unhold", "urgent", "vouch", "wait", "walkable", "walked")


def _runs_flotilla(word: str, previous: str) -> bool:
    return os.path.basename(word) == "flotilla" or (previous == "-m" and word.split(".")[0] == "flotilla")


def _move(words: list[str]) -> str:
    """The move of the first `flotilla ... work <move>` in the segment, or ""."""
    for at, word in enumerate(words):
        if not _runs_flotilla(word, words[at - 1] if at else ""):
            continue
        rest = words[at + 1:]
        if "work" in rest:
            for word in rest[rest.index("work") + 1:]:
                if word in MOVES:
                    return word
                if fnmatch.fnmatchcase("approve", word):   # a glob bash may expand to it (review of 0.6.10, C2)
                    return "approve"
    return ""


def check(segment) -> Finding | None:
    if _move(list(segment.words)) != "approve":
        return None
    return Finding(GUARD, True, "flotilla: approving work for trunk is the person's own move, and a Claude tool call "
                                "is never the person's. Show the person the command; they type it with `!` in front "
                                "in their own Claude Code session, or run it in a terminal.")
