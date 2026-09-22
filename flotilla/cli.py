"""Command-line dispatcher.

Each subcommand imports its module inside its branch, so a hook that exits early never pays
for modules it does not use.
"""

from __future__ import annotations

import argparse

from flotilla import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="flotilla", description="Coordinate independent peer Claude Code sessions.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("version", help="print the flotilla version")
    return parser


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "version":
        print(__version__)
        return 0
    return 2
