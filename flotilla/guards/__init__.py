"""Guards: checks that run before a Bash command (spec, section 10), and the git hooks behind them."""

from __future__ import annotations

from dataclasses import dataclass

CEILING = """\
What the guards cannot see. A guard reads the command line with a matcher, not a shell parser. It finds a command
at the start of a segment split at && || ; | & and newlines, behind NAME=value assignments, env, sudo, command,
nice, nohup, time, exec and shell keywords. It does not see commands inside $( ), backticks, eval, sh -c '...',
xargs, find -exec, functions, aliases or scripts (./ship.sh), nor wrappers with options (sudo -u x git ...). A
directory named through a variable (cd $D) is unknown: the revert guard then only warns, the push guard refuses.
The line-number guard does not open sed -f scripts and does not know perl -i, awk -i inplace or ed. What the text
hides from the push guard, the git pre-push hook catches: it asks git what is pushed. A program named through a
variable (`$F rig open`) is not seen at all when nothing else in the line names flotilla."""


@dataclass(frozen=True)
class Finding:
    guard: str
    refuse: bool     # False: the command runs, and the session is told
    text: str


PUSH_WORDS = ("push", "gh")


def on_failure(command: str, err: Exception, env) -> Finding:
    """What the guards say when they could not judge: decided by reversibility, so a command that may push is
    refused, unless the person overrode the gate knowingly; anything else runs, and the session is told."""
    may_push = any(word in command for word in PUSH_WORDS)
    knowingly = "FLOTILLA_GATE_OVERRIDE=" in command or bool(env.get("FLOTILLA_GATE_OVERRIDE", "").strip())
    return Finding("guards", may_push and not knowingly,
                   f"flotilla guards failed ({err}); "
                   + ('a command that may push is refused on failure. Knowingly: FLOTILLA_GATE_OVERRIDE="<why>"'
                      if may_push and not knowingly else "the command runs unchecked"))
