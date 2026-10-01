"""Text that came from a terminal program, made fit to store and to print (field test F19)."""

from __future__ import annotations

import re

#: CSI sequences (colours, cursor moves) and OSC sequences (hyperlinks, titles), BEL- or ST-terminated.
ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")


def strip_ansi(value: str) -> str:
    return ANSI.sub("", value)


import unicodedata

#: categories a terminal acts on, or that show nothing, reorder or end a line: controls, format characters (bidi,
#: zero-width, tags), line and paragraph separators, private use, unassigned, surrogates; spaces other than " "
_HIDDEN_CATEGORIES = {"Cc", "Cf", "Zl", "Zp", "Co", "Cn", "Cs"}
#: letters that render as blank space: Hangul fillers, the braille blank, the Mongolian vowel separator
_BLANK_LETTERS = {0x115F, 0x1160, 0x3164, 0xFFA0, 0x2800, 0x180E}
_NAMED = {"\n": "\\n", "\r": "\\r", "\t": "\\t"}


def _hidden(ch: str) -> bool:
    category = unicodedata.category(ch)
    return category in _HIDDEN_CATEGORIES or (category == "Zs" and ch != " ") or ord(ch) in _BLANK_LETTERS


def has_hidden(value: str) -> bool:
    return any(_hidden(ch) for ch in value)


def visible(value: str) -> str:
    """Text another session wrote, made safe to show a person: one line, and nothing in it can move the cursor,
    erase what was printed, hide, or reorder what is read (security review F5, F10, and its review)."""
    out = []
    for ch in value:
        if not _hidden(ch):
            out.append(ch)
            continue
        code = ord(ch)
        out.append(_NAMED.get(ch) or (f"\\x{code:02x}" if code < 0x100 else
                                      f"\\u{code:04x}" if code <= 0xFFFF else f"\\U{code:08x}"))
    return "".join(out)
