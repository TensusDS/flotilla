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


#: What bash expands inside a word, so the word the guard reads is not the word that runs: braces, ANSI-C and locale
#: quoting, extglob (third review of 0.6.10, I1). A line ending in a backslash continues the word on the next line.
EXPANDS = ("{", "$'", '$"', "@(", "+(", "!(", "?(", "*(")


#: `flotilla rig` moves that turn rented machines on or off, spend money, or widen what runs (rig design, section 4).
RIG_MOVES = ("enable", "disable", "open", "close", "allow-image")


def _reads(words: list[str]) -> list[tuple[str, str]]:
    """For each flotilla in the segment, the command and its move: the first two words after it that are not
    options, which is where argparse reads them. Reading only there keeps an ordinary `ls * *` further along from
    looking like a move, and a decoy before a second flotilla from hiding the real one (review of 0.6.10)."""
    found = []
    for at, word in enumerate(words):
        if _runs_flotilla(word, words[at - 1] if at else ""):
            plain = [item for item in words[at + 1:] if not item.startswith("-")][:2]
            found.append(tuple(plain + [""] * (2 - len(plain))))
    return found


def _is(word: str, name: str) -> bool:
    """The word is `name`. A glob that bash might expand to it never reaches here: `_opaque` refuses any glob in a
    command or move first, because fnmatch is not bash and the guard does not guess."""
    return word == name


def _opaque(word: str) -> bool:
    """A word whose meaning bash decides at run time: a variable, a substitution, or a glob - fnmatch is not bash
    (`[^x]` negates in bash and is a literal caret to fnmatch), so the guard never guesses what a glob becomes."""
    return any(sign in word for sign in "$`*?[")


def _may_be(word: str, name: str) -> bool:
    """The word is `name`, or a glob bash might expand to it. Only for a program word the guard cannot read: there it
    asks whether a command it guards is in reach. fnmatch is not bash, so any bracket counts; a variable does not,
    or every `$PYTHON $SCRIPT` would be refused (a variable program is a stated limit, README "Guards")."""
    if word == name:
        return True
    if "$" in word or "`" in word or not _opaque(word):
        return False
    return "[" in word or fnmatch.fnmatchcase(name, word)


def _rig_move(words: list[str]) -> str:
    for command, move in _reads(words):
        if _is(command, "rig") and any(_is(move, name) for name in RIG_MOVES):
            return move
    return ""


def _moves(words: list[str]) -> list[str]:
    return [("approve" if _is(move, "approve") else move) for command, move in _reads(words)
            if _is(command, "work") and move]


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
    named = any(_may_be(word, "rig") or _may_be(word, "work") for word in words[1:])
    if (words and _opaque(words[0]) and named) or any(
            _opaque(command) or ((_is(command, "rig") or _is(command, "work")) and _opaque(move))
            for command, move in _reads(words)):
        return Finding(GUARD, True, "flotilla: the program, command or move here is a variable or a substitution, so "
                                    "what it runs cannot be read before it runs - and some flotilla moves are the "
                                    "person's own. Write the command plainly.")
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
