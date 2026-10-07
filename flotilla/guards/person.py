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
#: `flotilla rig` moves that turn rented machines on or off, or spend money (rig design, section 4).
RIG_MOVES = ("enable", "disable", "open", "close")


def _rig_move(words: list[str]) -> str:
    """The first word after `rig` of a flotilla in the segment, when it is (or bash may expand to) a person's move."""
    for at, word in enumerate(words):
        if not _runs_flotilla(word, words[at - 1] if at else ""):
            continue
        rest = words[at + 1:]
        if "rig" in rest:   # options and `--` before the move are skipped: argparse reads past them too
            after = [item for item in rest[rest.index("rig") + 1:] if not item.startswith("-")]
            if after and (after[0] in RIG_MOVES or any(fnmatch.fnmatchcase(move, after[0]) for move in RIG_MOVES)):
                return after[0]
    return ""



def _runs_flotilla(word: str, previous: str) -> bool:
    return os.path.basename(word) == "flotilla" or (previous == "-m" and word.split(".")[0] == "flotilla")


#: What bash expands inside a word, so the word the guard reads is not the word that runs: braces, ANSI-C and locale
#: quoting, extglob (third review of 0.6.10, I1). A line ending in a backslash continues the word on the next line.
EXPANDS = ("{", "$'", '$"', "@(", "+(", "!(", "?(", "*(")


def _moves(words: list[str]) -> list[str]:
    """Every move a `flotilla ... work <move>` in the segment may name: each flotilla occurrence and each `work` after
    it, since a decoy (`env -C <dir named flotilla> -u work -u show <cli> work approve`) put a harmless move first."""
    found = []
    for at, word in enumerate(words):
        if not _runs_flotilla(word, words[at - 1] if at else ""):
            continue
        rest = words[at + 1:]
        for place, item in enumerate(rest):
            if item != "work":
                continue
            for candidate in rest[place + 1:]:
                if candidate in MOVES:
                    found.append(candidate)
                    break
                if fnmatch.fnmatchcase("approve", candidate):   # a glob bash may expand to it (review of 0.6.10)
                    found.append("approve")
                    break
    return found


def _move(words: list[str]) -> str:
    """The move of a `flotilla ... work <move>` in the segment - `approve` if any reading names it - or ""."""
    moves = _moves(words)
    return "approve" if "approve" in moves else (moves[0] if moves else "")


def _runs_any_flotilla(words: list[str]) -> bool:
    return any(_runs_flotilla(word, words[at - 1] if at else "") for at, word in enumerate(words))


def check(segment) -> Finding | None:
    words = list(segment.words)
    text = segment.text
    if _runs_any_flotilla(words) and (any(sign in text for sign in EXPANDS) or text.rstrip().endswith("\\")):
        return Finding(GUARD, True, "flotilla: this flotilla command holds what bash expands or continues "
                                    "(braces, $'...', $\"...\", an extglob, a trailing backslash), so the move it "
                                    "runs cannot be read before it runs - and `approve` is the person's own move. "
                                    "Write the command plainly.")
    rig_move = _rig_move(words)
    if rig_move:
        return Finding(GUARD, True, f"flotilla: `rig {rig_move}` is the person's own move - it turns rented machines "
                                    "on or off, or spends money - and a Claude tool call is never the person's. Show "
                                    "the person the command; they type it with `!` in front in their own Claude Code "
                                    "session, or run it in a terminal.")
    if _move(words) != "approve":
        return None
    return Finding(GUARD, True, "flotilla: approving work for trunk is the person's own move, and a Claude tool call "
                                "is never the person's. Show the person the command; they type it with `!` in front "
                                "in their own Claude Code session, or run it in a terminal.")
