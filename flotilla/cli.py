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
    doctor = sub.add_parser("doctor", help="check this machine and project")
    doctor.add_argument("--quiet", action="store_true", help="print only what needs attention")
    return parser


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "version":
        print(__version__)
        return 0
    if args.command == "doctor":
        from flotilla.doctor import run_doctor
        return run_doctor(quiet=args.quiet)
    return 2
