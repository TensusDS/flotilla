"""Text that came from a terminal program, made fit to store and to print (field test F19)."""

from __future__ import annotations

import re

#: CSI sequences (colours, cursor moves) and OSC sequences (hyperlinks, titles), BEL- or ST-terminated.
ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")


def strip_ansi(value: str) -> str:
    return ANSI.sub("", value)


#: characters a terminal acts on or reorders rather than shows: C0, DEL, C1, and the format characters that
#: change direction or hide (bidi embeddings and isolates, zero-width marks, the byte order mark)
_HIDDEN = {*range(0x00, 0x20), 0x7f, *range(0x80, 0xa0), *range(0x200b, 0x2010), *range(0x202a, 0x202f),
           *range(0x2060, 0x2070), 0xfeff}
_NAMED = {"\n": "\\n", "\r": "\\r", "\t": "\\t"}


def visible(value: str) -> str:
    """Text another session wrote, made safe to show a person: one line, and nothing in it can move the cursor,
    erase what was printed, or reorder what is read (security review F5, F10)."""
    out = []
    for ch in value:
        code = ord(ch)
        if code in _HIDDEN:
            out.append(_NAMED.get(ch) or (f"\\x{code:02x}" if code < 0x100 else f"\\u{code:04x}"))
        else:
            out.append(ch)
    return "".join(out)
