"""A Bash command line read the way the guards need it: segments, each with its words and its directory.

This is a matcher, not a shell parser, and its ceiling is named (`flotilla.guards.CEILING`) because silence would
read as "nothing to see". A command is recognised at the start of a segment, behind leading assignments, `env`, a
short list of wrappers and shell keywords. Heredoc bodies are data and are dropped. The line is split twice,
plainly and respecting quotes, and a segment either split finds counts: missing a door costs more than asking once
too often. A plain fragment whose quotes do not balance is a piece of a quoted string and is not read as a
command. `cd <literal>` is followed; a directory named through a variable is unknown (None).
"""

from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path

#: The plain split cuts at every `&`, `2>&1` included: it cannot tell an escaped `\>` from a redirection, and an extra
#: cut only adds a segment. The quote-aware split below reads `2>&1` whole; the guards read both.
SEPARATORS = re.compile(r"&&|\|\||[;|&\n]")
ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
WRAPPERS = frozenset({"sudo", "command", "nice", "nohup", "time", "exec", "env",
                      "if", "then", "else", "elif", "do", "while", "until", "!", "{", "("})
GIT_VALUE_OPTIONS = frozenset({"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"})


def into(base: Path | None, target: str | None) -> Path | None:
    """Where `cd target` (or `git -C target`) leads from `base`; None when it cannot be said."""
    if not target or "$" in target or "`" in target or target == "-":
        return None
    path = Path(os.path.expanduser(target))
    if not path.is_absolute():
        if base is None:
            return None
        path = base / path
    path = path.resolve()
    return path if path.is_dir() else None


@dataclass(frozen=True)
class Segment:
    text: str
    words: tuple[str, ...]
    assignments: dict = field(default_factory=dict, compare=False, hash=False)
    cwd: Path | None = None

    @property
    def program(self) -> str:
        return os.path.basename(self.words[0]) if self.words else ""

    def git(self):
        """(verb, arguments, directory) when this segment runs git; the directory is None when it cannot be named."""
        if self.program != "git":
            return None
        words, i, directory = self.words, 1, self.cwd
        if self.assignments.get("GIT_DIR") or self.assignments.get("GIT_WORK_TREE"):
            directory = None   # git is pointed at another repository than the directory it runs in
        while i < len(words):
            word = words[i]
            if word in GIT_VALUE_OPTIONS:
                if word == "-C":
                    directory = into(directory, words[i + 1] if i + 1 < len(words) else None)
                elif word in ("--git-dir", "--work-tree"):
                    directory = None   # the tree is not the directory the command runs in: not ours to guess
                i += 2
            elif word.startswith(("--git-dir=", "--work-tree=")):
                directory = None
                i += 1
            elif word.startswith("-"):
                i += 1
            else:
                return word, list(words[i + 1:]), directory
        return None


def _word(line: str, i: int) -> tuple[str, bool, int]:
    """The shell word starting at `i`, as bash reads it: quotes and backslashes taken out, whether any part was
    quoted, and where it ends (a blank or a metacharacter outside quotes)."""
    out, quoted, n = [], False, len(line)
    while i < n and line[i] not in " \t;&|<>()":
        ch = line[i]
        if ch == "\\" and i + 1 < n:
            out.append(line[i + 1])
            quoted, i = True, i + 2
        elif ch in "'\"":
            close = line.find(ch, i + 1)
            close = n if close < 0 else close
            out.append(line[i + 1:close])
            quoted, i = True, close + 1
        else:
            out.append(ch)
            i += 1
    return "".join(out), quoted, i


def heredoc(line: str) -> tuple[str, bool] | None:
    """The first heredoc this line opens, as bash reads it: its delimiter with quotes and backslashes taken out, and
    whether any part of it was quoted (a quoted delimiter keeps the body as data). `<<` counts only outside quotes,
    comments and arithmetic, and `<<<` (a here-string) never; None when the line opens none."""
    state, depth, i, n = "plain", 0, 0, len(line)
    while i < n:
        ch = line[i]
        if state == "single":
            state = "plain" if ch == "'" else state
        elif state == "ansi":
            if ch == "\\":
                i += 1
            elif ch == "'":
                state = "plain"
        elif state == "double":
            if ch == "\\":
                i += 1
            elif ch == '"':
                state = "plain"
        elif ch == "\\":
            i += 1
        elif ch == "'":
            state = "single"
        elif line.startswith("$'", i):
            state, i = "ansi", i + 1
        elif ch == '"':
            state = "double"
        elif ch == "#" and (i == 0 or line[i - 1] in " \t;&|()"):
            return None                    # a comment to the end of the line
        elif line.startswith("$((", i) or (line.startswith("((", i) and depth == 0 and line[:i].strip() in ("", "!")):
            depth += 1
            i += 2 if line[i] == "$" else 1
        elif line.startswith("))", i) and depth:
            depth -= 1
            i += 1
        elif depth == 0 and line.startswith("<<", i) and not line.startswith("<<<", i):
            j = i + 2 + (1 if line.startswith("<<-", i) else 0)
            while j < n and line[j] in " \t":
                j += 1
            label, quoted, _ = _word(line, j)
            return (label, quoted) if label else None
        elif depth == 0 and line.startswith("<<<", i):
            i += 2
        i += 1
    return None


def without_heredoc_bodies(command: str) -> str:
    """The command without heredoc bodies; a body whose terminator is missing is kept (a door must not hide)."""
    if "<<" not in command:
        return command
    lines, kept, i = command.split("\n"), [], 0
    while i < len(lines):
        kept.append(lines[i])
        found = heredoc(lines[i])
        if found:
            label = found[0]
            end = next((j for j in range(i + 1, len(lines)) if lines[j].strip() == label), None)
            if end is not None:
                i = end
        i += 1
    return "\n".join(kept)


def _next_is(command: str, at: int, char: str) -> bool:
    return at + 1 < len(command) and command[at + 1] == char


def _split_outside_quotes(command: str) -> list[str]:
    """Cut at separators outside quotes. An `&` inside a redirection (`2>&1`, `>&2`, `<&3`, `&>file`) joins, unless the
    `<`/`>` before it was escaped or quoted, which makes it a plain character (final review of 0.9.0, C1)."""
    parts, current, quote, escaped, angle = [], [], None, False, False
    for at, ch in enumerate(command):
        if quote:
            current.append(ch)
            if ch == quote:
                quote = None
            angle = False
            continue
        if escaped:
            current.append(ch)
            escaped, angle = False, False
            continue
        if ch == "\\":
            current.append(ch)
            escaped = True
            continue
        if ch in "\"'":
            quote = ch
            current.append(ch)
        elif ch in ";|\n" or (ch == "&" and not angle and not _next_is(command, at, ">")):
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
        angle = ch in "<>"
    parts.append("".join(current))
    return parts


def _words(text: str) -> tuple[list[str], bool]:
    """The words, and whether the quotes balanced; an unbalanced fragment is read plainly rather than not at all."""
    lexer = shlex.shlex(text, posix=True)
    lexer.whitespace_split = True
    lexer.commenters = "#"
    try:
        words = list(lexer)
    except ValueError:
        return text.split(), False
    if words and words[0][:1] in "({" and len(words[0]) > 1:
        words[0] = words[0][1:]             # a subshell or group written without a space: `(git push ...)`
    if words and words[-1][-1:] in ")}" and len(words[-1]) > 1:
        words[-1] = words[-1][:-1]
    return words, True


def _peel(words: list[str]) -> tuple[dict, list[str]]:
    assignments, i = {}, 0
    while i < len(words):
        word = words[i]
        if ASSIGNMENT.match(word):
            name, _, value = word.partition("=")
            assignments[name] = value
        elif word not in WRAPPERS:
            break
        i += 1
    return assignments, words[i:]


def segments(command: str, cwd, *, bodies: bool = False) -> list[Segment]:
    """The command's segments. Heredoc bodies are taken out unless `bodies`: the person guard reads every line, since
    a line taken for a body that bash runs would hide a person's move, and a refused body line costs only a retry."""
    if not bodies:
        command = without_heredoc_bodies(command)
    found: list[Segment] = []
    for plain, parts in ((True, SEPARATORS.split(command)), (False, _split_outside_quotes(command))):
        here = Path(cwd).resolve() if cwd is not None else None
        for part in parts:
            text = part.strip()
            if not text:
                continue
            words, balanced = _words(text)
            if plain and not balanced:
                continue   # a piece cut out of a quoted string: the quote-aware split reads that string whole
            assignments, words = _peel(words)
            if words and os.path.basename(words[0]) == "cd":
                target = next((word for word in words[1:] if not word.startswith("-")), None)
                here = into(here, target) if target is not None else Path.home()
                continue
            segment = Segment(text, tuple(words), assignments, here)
            if segment not in found:
                found.append(segment)
    return found
