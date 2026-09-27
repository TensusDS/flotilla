"""The line-number edit guard: `sed -i` addressed by line number is refused (spec, section 10).

A line number is right only for the file as it was when it was counted. After any edit above it the same number
names the neighbour, and an in-place edit lands there silently: nothing fails, the wrong line changes. Addressing
the line by its text (`/pattern/`) either finds it or changes nothing.

Seen: `sed` and `gsed` with `-i`, `-i.bak`, `-i ''`, `--in-place`, clustered flags (`-Ei`), scripts given as `-e`,
`--expression` or the first operand; an address is a number followed by a command, a range, a step or `!`. Not
seen: a script read with `-f`, `perl -i`, `awk -i inplace`, `ed`, and the shell's ceiling
(`flotilla.guards.CEILING`).
"""

from __future__ import annotations

import re

from flotilla.guards import Finding

GUARD = "line_edit"
PROGRAMS = ("sed", "gsed")
COMMANDS = re.compile(r"[;\n]")
ADDRESS = re.compile(r"^\d+(?:[,~!]|\s*[A-Za-z{=]|\s*$)")


def numeric_address(script: str) -> bool:
    return any(ADDRESS.match(command.lstrip(" \t{")) for command in COMMANDS.split(script))


def _scripts(words: list[str]) -> tuple[bool, list[str]]:
    in_place, scripts, operands, i = False, [], [], 1
    while i < len(words):
        word = words[i]
        if word == "--":
            operands += words[i + 1:]
            break
        if word in ("-e", "--expression"):
            scripts.append(words[i + 1] if i + 1 < len(words) else "")
            i += 2
            continue
        if word.startswith("--expression="):
            scripts.append(word.split("=", 1)[1])
        elif word in ("-f", "--file"):
            i += 1
        elif word == "--in-place" or word.startswith("--in-place="):
            in_place = True
        elif word.startswith("--"):
            pass
        elif word.startswith("-") and len(word) > 1:
            letters = word[1:]
            for k, ch in enumerate(letters):
                if ch == "i":
                    in_place = True
                    if k == len(letters) - 1 and i + 1 < len(words) and words[i + 1] == "":
                        i += 1          # BSD sed: `-i ''` names an empty backup suffix
                    break               # the rest of the cluster is the backup suffix
                if ch == "e":
                    if letters[k + 1:]:
                        scripts.append(letters[k + 1:])
                    else:
                        scripts.append(words[i + 1] if i + 1 < len(words) else "")
                        i += 1
                    break
                if ch == "f":
                    if not letters[k + 1:]:
                        i += 1
                    break
        else:
            operands.append(word)
        i += 1
    if not scripts and operands:
        scripts.append(operands[0])
    return in_place, scripts


def check(segment) -> Finding | None:
    if segment.program not in PROGRAMS:
        return None
    in_place, scripts = _scripts(list(segment.words))
    hits = [script for script in scripts if numeric_address(script)] if in_place else []
    if not hits:
        return None
    return Finding(GUARD, True, f"flotilla line-number guard: `{segment.text}` edits a file in place by line number "
                                f"({hits[0]!r}). A line number goes stale after any edit above it, and the change "
                                "lands on the neighbour without an error. Address the line by its text "
                                "(`/pattern/`), or use the Edit tool.")
