"""Rental services, one adapter each, chosen by name (rig design, section 2).

An adapter is one standalone file - standard library only, importing nothing from flotilla - because the reaper's
launcher loads a copy of the same file when no flotilla is installed. Every adapter keeps one contract and passes one
suite (`tests/test_rig_contract.py`).
"""

from __future__ import annotations

import importlib
from pathlib import Path

KNOWN = ("vast",)


def load(name: str):
    if name not in KNOWN:
        raise ValueError(f"no adapter `{name}`")
    return importlib.import_module(f"flotilla.rig.providers.{name}")


def files() -> list[Path]:
    return [Path(__file__).resolve().parent / f"{name}.py" for name in KNOWN]
