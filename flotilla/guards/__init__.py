"""Guards: checks that run before a Bash command (spec, section 10), and the git hooks behind them."""

from __future__ import annotations

import re
from dataclasses import dataclass

CEILING = """\
What the guards cannot see. A guard reads the command line with a matcher, not a shell parser. It finds a command
at the start of a segment split at && || ; | & and newlines, behind NAME=value assignments, env, sudo, command,
nice, nohup, time, exec and shell keywords. It does not see commands inside $( ), backticks, eval, sh -c '...',
xargs, find -exec, functions, aliases or scripts (./ship.sh), nor wrappers with options (sudo -u x git ...). A
directory named through a variable (cd $D) is unknown: the revert guard then only warns, the push guard refuses.
The line-number guard does not open sed -f scripts and does not know perl -i, awk -i inplace or ed. What the text
hides from the push guard, the git pre-push hook catches: it asks git what is pushed. A program named through a
variable (`$F rig open`) is not seen at all when nothing else in the line names flotilla. Code handed to an
interpreter (`bash -c`, `python3 -c`, a pipe into one, a heredoc or a here-string) is read only for the person's own
moves: one spelled there is refused, flotilla run from a command line's code asks the person; a script the session
writes and then runs is not read, and neither is `eval`."""


@dataclass(frozen=True)
class Finding:
    guard: str
    refuse: bool     # False: the command runs, and the session is told
    text: str
    ask: bool = False   # the person decides in Claude Code's confirmation (a PreToolUse `ask`; asks in auto mode too)


PUSH_WORDS = ("push", "gh")
#: Beside `flotilla` in a line, the words a move only the person makes is spelled with: `work approve`, every
#: `rig` move, `attention`. A text check, wider than the moves themselves - it decides only what is asked of the
#: person when no guard could read the line.
PERSON_WORDS = ("approve", "rig", "attention")

#: How a push the guards could not judge is let through knowingly. Never by the command's own text: judged, an
#: override waives a missing receipt alone, and here nothing was judged - a word a session can type would waive the
#: person's approval with it.
KNOWINGLY = ("the person runs the command themselves - with `!` in front, or in a terminal - or sets "
             'FLOTILLA_GATE_OVERRIDE="<why>" in the environment the session runs in. Where flotilla\'s pre-push hook '
             "is installed, it judges a `git push` again")
PERSONS_UNREAD = ("this line may hold a move only the person makes (`work approve`, a `rig` move, `attention`) "
                  "and it could not be read, so the person decides whether it runs")


_PERSON = re.compile(r"\b(?:%s)\b" % "|".join(PERSON_WORDS))
_QUOTING = {ord(sign): None for sign in "'\"\\"}


def unquoted(command: str) -> str:
    """The command's text as its words would read: quotes, backslashes and line continuations taken out, so a word
    split by a pair of quotes or a backslash is found by a text check. No parse - this is what is read when a parse
    failed, or before one is made."""
    return command.replace("\\\n", "").translate(_QUOTING)


def may_be_persons(command: str) -> bool:
    """One of the person's words stands in the line: as a whole word beside flotilla, or anywhere in a line the
    shell builds at run time (a variable, a substitution) - the program could be flotilla, and the word any part."""
    text = unquoted(command)
    if "$" in text or "`" in text:
        # where a word begins is bash's to say once it expands something (`$A'r'ig` is `rig` when A is empty):
        # the word anywhere in the text is enough
        return any(word in text for word in PERSON_WORDS)
    return "flotilla" in text and _PERSON.search(text) is not None


def on_failure(command: str, err: Exception, env) -> Finding:
    """What the guards say when they could not judge: decided by reversibility, so a command that may push is
    refused, unless the person overrode the gate knowingly, in the environment; a line that may hold the person's
    own move is put to the person; anything else runs, and the session is told."""
    may_push = any(word in unquoted(command) for word in PUSH_WORDS)
    refuse = may_push and not env.get("FLOTILLA_GATE_OVERRIDE", "").strip()
    ask = not refuse and may_be_persons(command)
    said = (f"a command that may push is refused on failure. Knowingly: {KNOWINGLY}" if refuse
            else PERSONS_UNREAD if ask else "the command runs unchecked")
    return Finding("guards", refuse, f"flotilla guards failed ({err}); {said}", ask=ask)


def person_unjudged(command: str, err: Exception) -> Finding:
    """The person guard alone failed: the other guards still judge the line, and the person decides a line that may
    hold their own move - it is never let through unread."""
    if may_be_persons(command):
        return Finding("person", False, f"flotilla person guard failed ({err}); {PERSONS_UNREAD}", ask=True)
    return Finding("person", False, f"flotilla person guard failed and let the command through: {err}")
