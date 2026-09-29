"""A fleet composition: how many sessions of which post (spec, sections 7.2-7.3).

Counts come from flags (one per template post) or from the profile's `fleet.default`, and are checked against the
project's posts: a count for a post the project does not have is refused by name. Sessions are raised acceptors
first - orchestrator, sender, reviewer, judge - then producers, so a producer's first handover finds its reader
alive; custom posts come last, by name. A post that writes one-copy resources (the sender) is never held by two
live sessions.
"""

from __future__ import annotations

ORDER = ("orchestrator", "sender", "reviewer", "judge", "main", "minor")
FLAGS = {"orchestrator": "-o", "sender": "-s", "reviewer": "-r", "judge": "-j", "main": "-M", "minor": "-m"}
ALIASES = {"review": "reviewer"}
SOLO_ABOVE = 3


class CompositionError(ValueError):
    """A composition that cannot be raised; the message says why."""


def normalise(counts: dict, posts: dict) -> dict[str, int]:
    found: dict[str, int] = {}
    for key, value in counts.items():
        name = ALIASES.get(key, key)
        if name not in posts:
            raise CompositionError(f"no post `{key}` in .flotilla/posts/; posts: {', '.join(sorted(posts))}")
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise CompositionError(f"`{key}` needs a whole number of sessions, not {value!r}")
        if value:
            found[name] = found.get(name, 0) + value
    return found


def raise_order(counts: dict[str, int]) -> list[str]:
    """The orchestrator last: its first report then sees every seat already raised (field test F8)."""
    first = [name for name in ORDER if name in counts and name != "orchestrator"]
    last = ["orchestrator"] if "orchestrator" in counts else []
    return first + sorted(name for name in counts if name not in ORDER) + last


def one_copy_problems(counts: dict[str, int], posts: dict, live_posts: dict[str, int]) -> list[str]:
    problems = []
    for name, count in counts.items():
        total = count + live_posts.get(name, 0)
        if posts[name].writes_one_copy and total > 1:
            problems.append(f"post `{name}` writes one-copy resources and is held by one session at most; this "
                            f"would make {total} ({live_posts.get(name, 0)} alive)")
    return problems


def warnings(counts: dict[str, int], posts: dict, live_posts: dict[str, int]) -> list[str]:
    total = sum(counts.values()) + sum(live_posts.values())
    if total <= SOLO_ABOVE:
        return []
    return [f"the fleet will hold {total} sessions and no {role}; above {SOLO_ABOVE} the spec suggests one"
            for role in ("orchestrator", "sender")
            if role in posts and counts.get(role, 0) + live_posts.get(role, 0) == 0]
