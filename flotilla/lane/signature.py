"""Which runs are runs of the same command (lane admission design, section 2): a ladder of signatures from exact to
coarse, so an estimate comes from the most exact step with history. Seat trees differ per seat; hashes, ports and
temporary files differ per run; flags that set parallelism change what a run takes and stay."""

from __future__ import annotations

import re
import shlex
from pathlib import PurePosixPath

from flotilla.lane.machine import INTERPRETER

SHELLS = {"sh", "bash", "zsh", "dash"}
WRAPPERS = {"npx", "pnpm", "yarn", "bunx", "uvx"}
HEX = re.compile(r"\b[0-9a-f]{8,}\b")
NUMBER = re.compile(r"\d{2,}")
REDIRECT = re.compile(r"^(?:\d?>>?|\d?<|&>|\d>&\d)(.*)$")
WORD = re.compile(r"[A-Za-z][\w-]*")
PARALLEL = re.compile(r"^--?(?:maxWorkers|max-workers|workers|numprocesses|jobs|j|n|w|parallel)(?:=.*)?$")


def _words(command: list[str]) -> list[str]:
    """The command as words, read through `sh -c "cd X && ..."` wrappers."""
    words = list(command)
    if len(words) >= 3 and PurePosixPath(words[0]).name in SHELLS and words[1] == "-c":
        try:
            words = shlex.split(words[2])
        except ValueError:
            return words
        while len(words) >= 3 and words[0] == "cd" and words[2] == "&&":
            words = words[3:]
    return words


def _place_tree(word: str, tree: str | None) -> str:
    if not tree:
        return word
    root = tree.rstrip("/")
    if word == root:
        return "<tree>"
    if word.startswith(root + "/"):
        return "<tree>" + word[len(root):]
    return word


def _normalise(words: list[str]) -> list[str]:
    out, skip = [], False
    for word in words:
        if skip:
            skip = False
            continue
        redirect = REDIRECT.match(word)
        if redirect:
            skip = not redirect.group(1)   # `>` alone takes the next word as its target
            continue
        if word.startswith("/tmp/") or word.startswith("/var/tmp/"):
            out.append("<tmp>")
            continue
        word = HEX.sub("<hex>", word)
        out.append("<n>" if word.isdigit() else NUMBER.sub("<n>", word) if "/" in word or ":" in word or "=" in word
                   else word)
    return out


def _program(words: list[str]) -> str:
    rest = list(words)
    while rest and PurePosixPath(rest[0]).name in WRAPPERS:
        rest = rest[1:]
    if len(rest) >= 2 and rest[0] in ("uv", "npm", "pnpm", "yarn") and rest[1] in ("run", "exec"):
        rest = rest[2:]
    if not rest:
        return ""
    name = PurePosixPath(rest[0]).name
    tail = rest[1:]
    if INTERPRETER.match(name):
        if "-m" in tail and tail.index("-m") + 1 < len(tail):
            at = tail.index("-m") + 1
            name, tail = tail[at], tail[at + 1:]
        else:
            script = next((w for w in tail if not w.startswith("-")), None)
            if script is not None:
                at = tail.index(script)
                name, tail = f"{name} {PurePosixPath(script).name}", tail[at + 1:]
    sub = None
    for i, word in enumerate(tail):
        if WORD.fullmatch(word) and not (i and tail[i - 1].startswith("-") and "=" not in tail[i - 1]):
            sub = word   # a subcommand, never the value of the flag before it (`--port 5091`)
            break
    flags = [w for w in tail if PARALLEL.match(w)]
    return " ".join([name, *([sub] if sub else []), *flags])


def ladder(command: list[str], *, tree: str | None, project: str) -> list[str]:
    words = [_place_tree(word, tree) for word in _words(command)]
    steps = [f"exact:{' '.join(words)}", f"norm:{' '.join(_normalise(words))}", f"prog:{_program(words)}",
             f"project:{project}"]
    return list(dict.fromkeys(step for step in steps if not step.endswith(":")))


def receipt_ladder(purpose: str, tiers: list[str], *, project: str) -> list[str]:
    return [f"receipt:{project}:{purpose}:{'+'.join(sorted(tiers))}", f"receipt:{project}:{purpose}",
            f"project:{project}"]


def tier_signature(name: str, *, project: str) -> str:
    return f"tier:{project}:{name}"
