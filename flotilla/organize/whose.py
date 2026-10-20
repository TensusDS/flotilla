"""Whose work it is (spec, section 3): organize takes in only the person's own, and Claude sessions working for the
person count as the person. Everything else - another person's work, or work carrying another AI's signs - is shown
and never touched unless the person names it.

The person is their git email and their GitHub login. Another AI's signs count only where the work itself adds or
changes them - the branch's own range, or a tree's untracked and modified files - never because trunk already
carries them. `AGENTS.md` alone is no sign: Claude Code reads it as project instructions where a project has no
`CLAUDE.md`, so it may be the person's own instructions for Claude.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

CLAUDE_SIGNS = ("Co-Authored-By: Claude", "Claude-Session:", "Generated with Claude Code", "Generated with [Claude Code]")
OTHER_AI_PATHS = (".agents/", ".codex/")
OTHER_AI_NAME = re.compile(r"(^|/)CODEX-[^/]*$")
OTHER_AI_TRAILER = re.compile(r"^co-authored-by:.*\b(codex|openai|gemini|cursor|copilot|aider|devin)\b",
                              re.IGNORECASE | re.MULTILINE)


@dataclass(frozen=True)
class Person:
    email: str
    login: str


def _git(root: Path, *args: str, run) -> str | None:
    done = run(["git", "-C", str(root), *args], capture_output=True, text=True, check=False)
    return done.stdout if done.returncode == 0 else None


def person_of(root: Path, login: str, run=subprocess.run) -> Person:
    return Person(email=(_git(root, "config", "user.email", run=run) or "").strip().lower(), login=login)


def pull_is_mine(pull, person: Person) -> bool:
    return bool(person.login) and pull.author.lower() == person.login.lower() and not pull.fork


def range_authors(root: Path, base: str, tip: str, run=subprocess.run) -> set[str]:
    """The author emails of the commits in base..tip, lowercased."""
    said = _git(root, "log", "--format=%ae", f"{base}..{tip}", run=run)
    return {line.strip().lower() for line in (said or "").splitlines() if line.strip()}


def is_path_sign(path: str) -> bool:
    return path.startswith(OTHER_AI_PATHS) or any(f"/{part}" in f"/{path}" for part in OTHER_AI_PATHS) or \
        bool(OTHER_AI_NAME.search(path))


def other_ai_signs(root: Path, base: str, tip: str, run=subprocess.run) -> list[str]:
    """Another AI's signs that base..tip adds or changes: its files, and other tools' co-author trailers."""
    found = [path for path in (_git(root, "diff", "--name-only", base, tip, run=run) or "").splitlines()
             if path and is_path_sign(path)]
    messages = _git(root, "log", "--format=%B", f"{base}..{tip}", run=run) or ""
    found += sorted({match.group(0).strip() for match in OTHER_AI_TRAILER.finditer(messages)})
    return found


def range_is_mine(root: Path, base: str, tip: str, person: Person, run=subprocess.run) -> tuple[bool, str]:
    """(mine, why not): every commit authored by the person, and no other AI's sign in the range."""
    authors = range_authors(root, base, tip, run=run)
    if not person.email:
        return False, "your git email is not set, so whose commits these are cannot be told"
    others = sorted(authors - {person.email})
    if others:
        return False, f"commits by {', '.join(others[:3])}"
    signs = other_ai_signs(root, base, tip, run=run)
    if signs:
        return False, f"another AI's signs: {', '.join(signs[:3])}"
    return True, ""
