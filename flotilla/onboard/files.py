"""Reading repository files for detection: an unreadable file reads as empty, never as an error."""

from __future__ import annotations

from pathlib import Path


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
