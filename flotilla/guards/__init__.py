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
hides from the push guard, the git pre-push hook catches: it asks git what is pushed."""


@dataclass(frozen=True)
class Finding:
    guard: str
    refuse: bool     # False: the command runs, and the session is told
    text: str
