"""A TOML writer for flotilla's own files.

The standard library reads TOML (`tomllib`) but cannot write it. flotilla writes a small subset:
tables, arrays of tables, strings, integers, floats, booleans and flat arrays of those. This module
emits exactly that subset and refuses anything else by name instead of guessing a representation.
"""

from __future__ import annotations

import math
import re

_BARE_KEY = re.compile(r"^[A-Za-z0-9_-]+$")


class TomlWriteError(TypeError):
    """A value has no representation in the subset flotilla writes."""


def _string(text: str) -> str:
    out = ['"']
    for ch in text:
        code = ord(ch)
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        elif ch == "\r":
            out.append("\\r")
        elif code < 0x20 or code == 0x7F:
            out.append("\\u%04x" % code)
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def _key(key) -> str:
    if not isinstance(key, str) or not key:
        raise TomlWriteError(f"keys must be non-empty strings, got {key!r}")
    return key if _BARE_KEY.match(key) else _string(key)


def _scalar(value, where: str) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            raise TomlWriteError(f"{where}: {value!r} is not written")
        return repr(value)
    if isinstance(value, str):
        return _string(value)
    if value is None:
        raise TomlWriteError(f"{where}: TOML has no null; leave the key out instead")
    raise TomlWriteError(f"{where}: cannot write a {type(value).__name__}")


def _is_table_array(value) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(v, dict) for v in value)


def _value(value, where: str) -> str:
    if isinstance(value, list):
        if any(isinstance(v, (dict, list)) for v in value):
            raise TomlWriteError(f"{where}: arrays hold scalars only, or tables only")
        return "[" + ", ".join(_scalar(v, where) for v in value) + "]"
    return _scalar(value, where)


def _emit(lines: list[str], path: list[str], table: dict) -> None:
    for key, value in table.items():
        if not isinstance(value, dict) and not _is_table_array(value):
            lines.append(f"{_key(key)} = {_value(value, '.'.join(path + [str(key)]))}")
    for key, value in table.items():
        dotted = ".".join(_key(part) for part in path + [key])
        if isinstance(value, dict):
            lines.extend(["", f"[{dotted}]"])
            _emit(lines, path + [key], value)
        elif _is_table_array(value):
            for item in value:
                lines.extend(["", f"[[{dotted}]]"])
                _emit(lines, path + [key], item)


def render_toml(data: dict, header: str = "") -> str:
    lines = [f"# {line}".rstrip() for line in header.splitlines()]
    _emit(lines, [], data)
    return "\n".join(lines).strip("\n") + "\n"
