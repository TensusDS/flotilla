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


MAX_TEXT = 2048   # a command's text in the journal and in its signatures: folded by every session, so bounded
SEPARATORS = {"&&", "||", ";", "|", "&"}
SETUP = {"cd", "set", "export", "source", ".", "true", ":"}
TRIVIAL = {"echo", "printf", "tail", "head", "tee", "grep", "wc", "cat", "sort", "sleep", "date", "test", "["}
REDIRECT_OP = re.compile(r"[<>]+&?|&>>?")
PREFIXES = {"timeout", "env", "nice", "ionice", "time", "nohup", "stdbuf", "xvfb-run"}
VALUED = {"--with", "--project", "--directory", "--python", "--extra", "--group", "--package", "-p", "-n", "-s", "-k",
          "--signal", "-o", "-e"}
ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def _split_shell(text: str) -> list[str]:
    """Words of a shell string; `;` `&&` `||` `|` `&` and redirections come out as words of their own."""
    try:
        lexer = shlex.shlex(text[:MAX_TEXT], posix=True, punctuation_chars=";&|<>")
        lexer.whitespace_split = True
        return list(lexer)
    except ValueError:
        return text[:MAX_TEXT].split()


def _unwrap(words: list[str]) -> list[str]:
    """Strip what runs a command rather than being it: `timeout 600`, `env A=b`, `nice -n 5`, `A=b`, and read
    through `sh -c "..."` - as many layers as there are."""
    rest = list(words)
    while rest:
        name = PurePosixPath(rest[0]).name
        if ASSIGN.match(rest[0]):
            rest = rest[1:]
        elif name in PREFIXES:
            rest = rest[1:]
            while rest and (rest[0].startswith("-") or ASSIGN.match(rest[0]) or re.fullmatch(r"\d+[smhd]?", rest[0])):
                takes = rest[0] in VALUED and len(rest) > 1
                rest = rest[2:] if takes else rest[1:]
        elif name in SHELLS and len(rest) >= 3 and rest[1] == "-c":
            rest = _split_shell(rest[2])
        else:
            break
    return rest


def _without_redirects(words: list[str]) -> list[str]:
    """`> out.log`, `2>&1`, `< in`: where output goes changes from run to run, never what the run takes."""
    out, skip = [], False
    for word in words:
        if skip:
            skip = False
            continue
        if REDIRECT_OP.fullmatch(word):
            if out and out[-1].isdigit():
                out.pop()   # the descriptor of `2>`
            skip = True     # the target
            continue
        attached = REDIRECT.match(word)
        if attached:        # `>file`, `2>&1` as one word (an argument list, not a shell string)
            skip = not attached.group(1)
            continue
        out.append(word)
    return out


def _commands(words: list[str]) -> list[tuple[str, list[str]]]:
    """(the separator before it, its words) for each command of a chain, wrappers and redirections stripped and
    setup steps (`cd X`, `set -o pipefail`, `export A=b`) left out."""
    out, current, before = [], [], ""
    for word in _without_redirects(words) + [";"]:
        if word in SEPARATORS:
            current = _unwrap(current)
            if current and PurePosixPath(current[0]).name not in SETUP:
                out.append((before if out else "", current))
            current, before = [], word
        else:
            current.append(word)
    return out


def _words(command: list[str]) -> list[str]:
    """The command as words: wrappers, redirections and setup steps stripped, the chain kept with its separators."""
    words: list[str] = []
    for before, segment in _commands(_unwrap(list(command))):
        words += ([before] if before else []) + segment
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
        if PARALLEL.match(word) or (out and PARALLEL.match(out[-1]) and "=" not in out[-1]):
            out.append(word)   # parallelism changes what a run takes: its value stays (`-n 4`, `--maxWorkers=16`)
            continue
        word = HEX.sub("<hex>", word)
        out.append("<n>" if word.isdigit() else NUMBER.sub("<n>", word) if "/" in word or ":" in word or "=" in word
                   else word)
    return out


def _program(words: list[str]) -> str:
    """The program the chain exists for: of each command the first stage of its pipeline (`node shoot.mjs | tail`
    is a screenshot job), trivial ones (`echo ok`, `tail`) left out, and of those the last (`npx tsc && npx vitest
    run` is a test run) - with its subcommand and parallelism flags."""
    chain: list[list[str]] = [[]]
    for word in words:
        if word in SEPARATORS:
            chain.append([]) if word != "|" else chain.append(None)
        elif chain[-1] is not None:
            chain[-1].append(word)
        # words after `|` belong to a filter stage: not the program
    stages = [_unwrap(c) for c in chain if c]
    stages = [c for c in stages if c and PurePosixPath(c[0]).name not in TRIVIAL | SETUP] or [c for c in stages if c]
    rest = stages[-1] if stages else []
    while rest and PurePosixPath(rest[0]).name in WRAPPERS:
        rest = rest[1:]
    if len(rest) >= 3 and rest[0] in ("npm", "pnpm", "yarn") and rest[1] == "run":
        return f"{rest[0]} run {rest[2]}"
    if len(rest) >= 2 and rest[0] in ("uv", "npm", "pnpm", "yarn") and rest[1] in ("run", "exec"):
        rest = rest[2:]
        while rest and rest[0].startswith("-"):   # `uv run --with pytest python ...`
            rest = rest[2:] if rest[0] in VALUED and len(rest) > 1 else rest[1:]
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
    flags = []
    for i, word in enumerate(tail):
        if PARALLEL.match(word):
            flags.append(word if "=" in word or i + 1 >= len(tail) else f"{word} {tail[i + 1]}")
    return " ".join([name, *([sub] if sub else []), *flags])


def ladder(command: list[str], *, tree: str | None, project: str) -> list[str]:
    words = [_place_tree(word, tree) for word in _words(command)]
    program = _program(words)
    steps = [f"exact:{project}:{' '.join(words)}"[:MAX_TEXT], f"norm:{project}:{' '.join(_normalise(words))}"[:MAX_TEXT],
             f"prog:{project}:{program}" if program else "", f"project:{project}"]
    return list(dict.fromkeys(step for step in steps if step and not step.endswith(":")))


def receipt_ladder(purpose: str, tiers: list[str], *, project: str) -> list[str]:
    return [f"receipt:{project}:{purpose}:{'+'.join(sorted(tiers))}", f"receipt:{project}:{purpose}",
            f"project:{project}"]


def tier_signature(name: str, *, project: str) -> str:
    return f"tier:{project}:{name}"
