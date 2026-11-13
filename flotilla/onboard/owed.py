"""Decisions an update owes the person (fleet model design 5.2).

When a release changes what a setting means, the fleet does not guess the person's answer: the orchestrator puts
the question to them at its start, `flotilla doctor` names it, and `spawn` raises nothing while a decision that
blocks it is open. The answer is always the person's own move. The fleet's model is the first such decision: since
0.32 a fleet no longer inherits the model Claude Code last saved.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Owed:
    id: str
    blocks_spawn: bool
    question: str   # what changed and why, in plain words, ending with what the person decides
    answer: str     # the line the person runs to answer


def settings_model(env=os.environ) -> str:
    """The model Claude Code's user settings name as its default for new sessions, read and never written; "" when
    none or unreadable."""
    from flotilla.core.claude_state import config_dir
    try:
        data = json.loads((config_dir(env) / "settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    value = data.get("model") if isinstance(data, dict) else None
    return value.strip() if isinstance(value, str) else ""


def owed(profile: dict, *, state: Path, main: Path, settings_model: str = "") -> list[Owed]:
    from flotilla.fleet import model
    found = []
    if model.default(profile, state=state, main=main) == ("", ""):
        seen = (f" Claude Code's settings name `{settings_model}` now; that is not preselected - it may be a model "
                "switched for one task." if settings_model else "")
        found.append(Owed(
            "fleet-model", True,
            "flotilla no longer lets the fleet inherit the model Claude Code last saved: a fleet once ran for hours "
            "on a model switched with /model for one task, until that model's limit stopped every seat. Which "
            f"model should this project's fleet run on by default?{seen} Until it is named, no seat is raised.",
            model.ANSWER))
    return found
