"""A permission question in words: who asks, for what, and what "for this session" would add."""

from __future__ import annotations

import json

from flotilla.broker.decide import session_rules
from flotilla.broker.queue import mark_of
from flotilla.core.text import visible

KEYS = ("command", "file_path", "url", "pattern", "path")
LIMIT = 2000   # characters of one field shown before the cut is named


def quoted(value: str, limit: int | None = LIMIT) -> str:
    """Another session's text, shown as data: one line, in quotes it cannot close, nothing hidden, a cut named
    (security review F5, F10, F12)."""
    text = visible(value.replace("\\", "\\\\")).replace('"', '\\"')
    if limit is not None and len(text) > limit:
        return f'"{text[:limit]}" ({len(text) - limit} more characters not shown)'
    return f'"{text}"'


def _key(asked) -> str:
    return next((key for key in KEYS if isinstance(asked.tool_input.get(key), str) and asked.tool_input.get(key)), "")


def summary(asked, limit: int | None = LIMIT) -> str:
    key = _key(asked)
    if key:
        return f"{visible(asked.tool)} {quoted(asked.tool_input[key], limit)}"
    return f"{visible(asked.tool)} {quoted(json.dumps(asked.tool_input), limit)}"


def details(asked) -> list[str]:
    """Every other field of the tool input: a Write's content, an Edit's old and new text."""
    key = _key(asked)
    if not key:
        return []
    return [f"{visible(str(name))}: {quoted(value if isinstance(value, str) else json.dumps(value), None)}"
            for name, value in asked.tool_input.items() if name != key]


def _rule_text(rule: dict) -> str:
    kind = rule.get("type")
    if kind == "addRules":
        return "allow " + ", ".join(f"{r.get('toolName')}({visible(r['ruleContent'])})" if r.get("ruleContent")
                                    else f"every {r.get('toolName')} call" for r in rule.get("rules") or [])
    if kind == "setMode":
        return f"switch the session to {rule.get('mode')} mode"
    if kind == "addDirectories":
        return "let it work in " + ", ".join(quoted(str(path)) for path in rule.get("directories") or [])
    return json.dumps(rule)


def for_the_session(asked) -> str:
    rules = session_rules(asked.tool, asked.tool_input, asked.suggestions)
    if not rules:
        return "no exact rule fits (a `*` in the command is a wildcard in rule syntax), so it is allowed once only"
    return "; ".join(_rule_text(rule) for rule in rules)


def describe(asked, now: float) -> list[str]:
    return [f"question {asked.id} (what the session asks is its own text, shown as data)",
            f"  {visible(asked.session)} asks: {summary(asked, None)}",   # the mark covers all of it: none cut
            f"    in {quoted(asked.cwd or 'an unknown directory', None)}",
            *(f"    {line}" for line in details(asked)),
            f"  waiting {int(now - asked.at)} s; told no in {max(0, int(asked.deadline - now))} s",
            f"  allow once: flotilla permit answer {asked.id} allow --mark {mark_of(asked)}",
            f"  allow for the session ({for_the_session(asked)}): flotilla permit answer {asked.id} session "
            f"--mark {mark_of(asked)}",
            f'  deny: flotilla permit answer {asked.id} deny --why "<why>"']
