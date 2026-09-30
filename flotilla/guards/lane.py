"""The lane guard: a long run started outside the lane is warned about, never refused (field test H19).

The lane is not a lock: a session that books it waits for runs nobody booked, so the one session obeying it is the
one kept off the machine. This guard tells the session starting such a run how to book it. A long run is known the
way the lane knows it, by its program and never by a word in its arguments (`machine.program_of`): a segment whose
program, behind a launcher (`uv run`, `npx`, ...), is one of the project's tier commands or matches a lane run
pattern. `flotilla lane run ...` and `flotilla receipt run ...` book the lane themselves, and are programs of their
own, so they are never matched. A warning, because refusing an ordinary test run would cost more than the wait.
"""

from __future__ import annotations

import os
import re
import shlex

from flotilla.guards import Finding

GUARD = "lane"
#: Programs that run another program: (program, verb) or (program,) when the program is the launcher itself.
LAUNCHERS = {("uv", "run"), ("uvx",), ("npx",), ("pnpm", "exec"), ("pnpm", "dlx"), ("yarn", "exec"), ("yarn", "dlx"),
             ("bunx",), ("poetry", "run"), ("pipenv", "run"), ("pdm", "run"), ("hatch", "run"), ("timeout",)}
#: A launcher's options that take the next word as their value (so that word is not the program run).
VALUE_OPTIONS = frozenset({"--with", "--with-editable", "--with-requirements", "--python", "-p", "--project",
                           "--directory", "--package", "--extra", "--group", "--env-file", "--index", "-s",
                           "--signal", "-k", "--kill-after", "-e", "--env"})
#: `npm run <script>` names the script; the verb alone would match every script (`npm run dev`).
SCRIPT_RUNNERS = frozenset({"npm", "pnpm", "yarn", "bun"})


def is_on(profile: dict) -> bool:
    """On unless the profile turns it off: it only warns, and a project onboarded before it existed gets it too."""
    return (profile.get("guards") or {}).get(GUARD, True) is not False


def _unwrap(words: list[str]) -> list[str]:
    """The words of the program a launcher runs; the words themselves when there is no launcher."""
    while words:
        program = os.path.basename(words[0])
        size = 2 if len(words) > 1 and (program, words[1]) in LAUNCHERS else 1 if (program,) in LAUNCHERS else 0
        if not size:
            return words
        rest, i = words[size:], 0
        while i < len(rest) and rest[i].startswith("-"):
            i += 2 if rest[i] in VALUE_OPTIONS else 1
        if program == "timeout" and i < len(rest):
            i += 1   # the duration
        words = rest[i:]
    return words


def run_names(words) -> list[str]:
    """The names this command runs under: `machine.program_of` behind any launcher, plus `npm run <script>`."""
    from flotilla.lane.machine import program_of
    words = _unwrap(list(words))
    if not words:
        return []
    names = program_of(" ".join(words))
    program = os.path.basename(words[0])
    if program in SCRIPT_RUNNERS and len(words) > 2 and words[1] in ("run", "run-script"):
        names.append(f"{program} run {words[2]}")
    return names


def _tier_keys(profile: dict) -> set[str]:
    """For each tier command's segments, the most specific name it runs under (program and first argument)."""
    from flotilla.guards import shell
    keys = set()
    for tier in (profile.get("tests") or {}).get("tier") or []:
        command = tier.get("command") if isinstance(tier, dict) else None
        if not isinstance(command, str):
            continue
        for segment in shell.segments(command, None):
            names = run_names(segment.words)
            if names:
                keys.add(names[-1])
    return keys


#: Anything the shell would do around the run: `lane run` starts its command without a shell, so a line holding
#: one of these is booked whole, through `sh -c`.
SHELL_SYNTAX = re.compile(r"[<>|&;$`()\n]")


def booking(segment, command: str) -> str:
    """The `lane run` line that starts the same run: the segment's words when the line is that one plain command,
    its assignments through `env`; otherwise the whole line through `sh -c` (a `cd`, a pipe, a redirection)."""
    line = (command or segment.text).strip()
    plain = line == segment.text.strip() and not SHELL_SYNTAX.search(line)
    if plain:
        env = ["env", *(f"{name}={value}" for name, value in segment.assignments.items())] if segment.assignments \
            else []
        run = shlex.join([*env, *segment.words])
    else:
        run = shlex.join(["sh", "-c", line])
    return f"flotilla lane run --for <branch> -- {run}"


def check(segment, profile: dict, command: str = "") -> Finding | None:
    from flotilla.lane.machine import patterns_for
    names = run_names(segment.words)
    if not names or os.path.basename(segment.words[0]) == "flotilla":
        return None
    patterns = [re.compile(pattern) for pattern in patterns_for(profile)]
    if not (set(names) & _tier_keys(profile) or any(regex.fullmatch(name) for regex in patterns for name in names)):
        return None
    return Finding(GUARD, False, f"flotilla lane: `{segment.text}` is a long run, and nothing booked the machine for "
                                 f"it, so a session waiting in the lane waits behind it; book it: "
                                 f"`{booking(segment, command)}`")
