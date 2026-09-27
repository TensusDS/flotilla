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

SEPARATORS = re.compile(r"&&|\|\||[;|&\n]")
ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
WRAPPERS = frozenset({"sudo", "command", "nice", "nohup", "time", "exec", "env",
                      "if", "then", "else", "elif", "do", "while", "until", "!", "{", "("})
HEREDOC = re.compile(r"""<<-?\s*(?:'([^']+)'|"([^"]+)"|([A-Za-z_][A-Za-z0-9_]*))""")
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
        while i < len(words):
            word = words[i]
            if word in GIT_VALUE_OPTIONS:
                if word == "-C":
                    directory = into(directory, words[i + 1] if i + 1 < len(words) else None)
                i += 2
            elif word.startswith("-"):
                i += 1
            else:
                return word, list(words[i + 1:]), directory
        return None


def without_heredoc_bodies(command: str) -> str:
    """The command without heredoc bodies; a body whose terminator is missing is kept (a door must not hide)."""
    if "<<" not in command:
        return command
    lines, kept, i = command.split("\n"), [], 0
    while i < len(lines):
        kept.append(lines[i])
        found = HEREDOC.search(lines[i])
        if found:
            label = next(group for group in found.groups() if group)
            end = next((j for j in range(i + 1, len(lines)) if lines[j].strip() == label), None)
            if end is not None:
                i = end
        i += 1
    return "\n".join(kept)


def _split_outside_quotes(command: str) -> list[str]:
    parts, current, quote = [], [], None
    for ch in command:
        if quote:
            current.append(ch)
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
            current.append(ch)
        elif ch in ";|&\n":
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
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


def segments(command: str, cwd) -> list[Segment]:
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
