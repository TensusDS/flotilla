"""One piece of work organize asks about, and the fingerprint `apply` checks it against.

An item's key names it across runs (`pr:42`, `chain:42-43-44`, `stack:7`, `branch:<name>`, `trunk:<trunk>`,
`onboard:<branch>`, `edits:<tree>`, `cache:<dir>`); its facts are what the person was shown - heads, bases, tips, the
state of a tree - and `seen` is their digest. An answer is carried out only while `seen` still matches: anything that
moved since the question means the item is asked again (spec, section 4).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

from flotilla.core.text import visible

KINDS = ("pr", "chain", "stack", "branch", "trunk", "onboard", "edits", "cache")
TITLE = 60


@dataclass
class Item:
    key: str
    kind: str
    phase: str            # "before" publishing, or "after"
    title: str
    lines: list[str]
    facts: dict
    offered: list[str] = field(default_factory=list)   # the shapes a chain or stack is offered


def seen(item: Item) -> str:
    return hashlib.sha256(json.dumps(item.facts, sort_keys=True).encode()).hexdigest()[:16]


def shown(text: str, limit: int = TITLE) -> str:
    """Author-written text, made one safe line and short; a backquote would break the code span it sits in."""
    text = visible(" ".join(str(text).split())).replace("`", "'")
    return text if len(text) <= limit else text[:limit - 1] + "…"
