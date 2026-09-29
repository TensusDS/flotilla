"""Text that came from a terminal program, made fit to store and to print (field test F19)."""

from __future__ import annotations

import re

#: CSI sequences (colours, cursor moves) and OSC sequences (hyperlinks, titles), BEL- or ST-terminated.
ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")


def strip_ansi(value: str) -> str:
    return ANSI.sub("", value)
