"""Provider answers made safe to print and to journal (rig design, section 6).

A provider answers with more than it was asked: in the probe of 2026-10-06 `vastai create` returned the instance's
own API key, and it reached a log. Every answer passes here first: a field whose name says it holds a credential is
dropped at any depth, and its value is masked wherever else it appears - in another field, in an error text.
"""

from __future__ import annotations

import re

MASK = "[masked]"
_SECRET = re.compile(r"key|token|secret|password|passwd|credential", re.IGNORECASE)
_SHORTEST = 8   # a value this short is not a credential, and masking it would mask every "1" in the answer


def secret_name(name: str) -> bool:
    return bool(_SECRET.search(name))


def _strings(data, found: list[str]) -> None:
    if isinstance(data, str) and len(data) >= _SHORTEST:
        found.append(data)
    elif isinstance(data, dict):
        for value in data.values():
            _strings(value, found)
    elif isinstance(data, list):
        for value in data:
            _strings(value, found)


def _collect(data, found: list[str]) -> None:
    if isinstance(data, dict):
        for name, value in data.items():
            if isinstance(name, str) and secret_name(name):
                _strings(value, found)
            else:
                _collect(value, found)
    elif isinstance(data, list):
        for value in data:
            _collect(value, found)


def scrub_text(text: str, values: list[str]) -> str:
    for value in sorted(set(values), key=len, reverse=True):
        text = text.replace(value, MASK)
    return text


def _clean(data, values: list[str]):
    if isinstance(data, dict):
        return {name: _clean(value, values) for name, value in data.items()
                if not (isinstance(name, str) and secret_name(name))}
    if isinstance(data, list):
        return [_clean(value, values) for value in data]
    if isinstance(data, str):
        return scrub_text(data, values)
    return data


def scrub(data) -> tuple[object, list[str]]:
    values: list[str] = []
    _collect(data, values)
    return _clean(data, values), values
