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
    hook = sub.add_parser("hook", help="entry point for Claude Code hooks")
    hook.add_argument("event", choices=["session-start"])
    onboard = sub.add_parser("onboard", help="measure, detect, ask and write the project profile")
    actions = onboard.add_subparsers(dest="action", required=True)
    actions.add_parser("machine", help="measure this machine into the state directory")
    for name, text in (("detect", "print what the repository declares"),
                       ("next", "print the next questions"),
                       ("check", "report drift between the profile and the repository"),
                       ("reset", "forget the answers given so far")):
        actions.add_parser(name, help=text).add_argument("--root", default=".")
    answer = actions.add_parser("answer", help="record the answer to one question")
    answer.add_argument("question")
    answer.add_argument("values", nargs="+")
    answer.add_argument("--root", default=".")
    write = actions.add_parser("write", help="run the tiers once and write .flotilla/project.toml")
    write.add_argument("--root", default=".")
    write.add_argument("--force", action="store_true", help="replace an existing profile")
    write.add_argument("--no-run", action="store_true", help="do not run the tiers now")
    write.add_argument("--keep-unmeasured", action="store_true", help="write even if a tier is not green")
    write.add_argument("--timeout", type=float, default=1800.0, help="seconds per tier (default 1800)")
    return parser


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "version":
        print(__version__)
        return 0
    if args.command == "doctor":
        from flotilla.doctor import run_doctor
        return run_doctor(quiet=args.quiet)
    if args.command == "hook":
        import sys
        from flotilla.hooks import run_hook
        return run_hook(args.event, sys.stdin)
    if args.command == "onboard":
        from flotilla.onboard.commands import run_onboard
        return run_onboard(args)
    return 2
