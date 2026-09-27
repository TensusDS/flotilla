"""A permission question in words: who asks, for what, and what "for this session" would add."""

from __future__ import annotations

import json

from flotilla.broker.decide import session_rules

KEYS = ("command", "file_path", "url", "pattern", "path")


def summary(asked) -> str:
    for key in KEYS:
        value = asked.tool_input.get(key)
        if isinstance(value, str) and value:
            return f"{asked.tool} {value}"
    return f"{asked.tool} {json.dumps(asked.tool_input)[:200]}"


def _rule_text(rule: dict) -> str:
    kind = rule.get("type")
    if kind == "addRules":
        return "allow " + ", ".join(f"{r.get('toolName')}({r['ruleContent']})" if r.get("ruleContent")
                                    else f"every {r.get('toolName')} call" for r in rule.get("rules") or [])
    if kind == "setMode":
        return f"switch the session to {rule.get('mode')} mode"
    if kind == "addDirectories":
        return "let it work in " + ", ".join(rule.get("directories") or [])
    return json.dumps(rule)


def for_the_session(asked) -> str:
    return "; ".join(_rule_text(rule) for rule in session_rules(asked.tool, asked.tool_input, asked.suggestions))


def describe(asked, now: float) -> list[str]:
    return [f"question {asked.id}",
            f"  {asked.session} asks: {summary(asked)}",
            f"  waiting {int(now - asked.at)} s; told no in {max(0, int(asked.deadline - now))} s",
            f"  allow once: flotilla permit answer {asked.id} allow",
            f"  allow for the session ({for_the_session(asked)}): flotilla permit answer {asked.id} session",
            f'  deny: flotilla permit answer {asked.id} deny --why "<why>"']
