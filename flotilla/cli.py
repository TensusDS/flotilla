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
    work = sub.add_parser("work", help="move a row of the work ledger")
    moves = work.add_subparsers(dest="move", required=True)

    def move_parser(name: str, text: str):
        parser_ = moves.add_parser(name, help=text)
        parser_.add_argument("branch")
        parser_.add_argument("--root", default=".")
        parser_.add_argument("--as", dest="as_name", default=None, help="act as this session (recorded)")
        return parser_

    claim = move_parser("claim", "claim work on a branch")
    claim.add_argument("--tree", default="")
    claim.add_argument("--ref", default="")
    claim.add_argument("--requires", nargs="*", default=[])
    claim.add_argument("--also", default="")
    move_parser("reserve", "hold a post's home branch").add_argument("--tree", default="")
    move_parser("hand", "hand work over at its tip").add_argument("--tip", default=None)
    moved = move_parser("moved", "record a tip that moved after handover")
    moved.add_argument("--tip", required=True)
    moved.add_argument("--agreed-by", dest="agreed_by", default="")
    move_parser("fix", "return work with what must change").add_argument("--why", required=True)
    move_parser("assign", "name the reader of a handed branch").add_argument("--reader", required=True)
    move_parser("recuse", "step back from reading a branch")
    move_parser("take", "say you are reading a branch")
    move_parser("accept", "accept the handed tip").add_argument("--reviewed", required=True)
    wait = move_parser("wait", "record whom a row waits on")
    wait.add_argument("--on", default="")
    wait.add_argument("--why", default="")
    wait.add_argument("--clear", action="store_true")
    move_parser("release", "say the work will not happen").add_argument("--why", required=True)
    move_parser("show", "print a row and its history")

    tree = sub.add_parser("tree", help="worktrees filed in the ledger")
    tree_actions = tree.add_subparsers(dest="action", required=True)
    cut = tree_actions.add_parser("cut", help="cut a worktree from trunk and claim it")
    cut.add_argument("branch")
    cut.add_argument("--tree", required=True)
    cut.add_argument("--expect", default=None)
    cut.add_argument("--ref", default="")
    cut.add_argument("--requires", nargs="*", default=[])
    cut.add_argument("--also", default="")
    cut.add_argument("--root", default=".")
    cut.add_argument("--as", dest="as_name", default=None)

    receipt = sub.add_parser("receipt", help="test-tier receipts over one revision")
    receipt_actions = receipt.add_subparsers(dest="action", required=True)
    run = receipt_actions.add_parser("run", help="run the tiers for a purpose over this tree's HEAD")
    run.add_argument("--purpose", choices=["handover", "push"], required=True)
    run.add_argument("--tree", default=".")
    run.add_argument("--timeout", type=float, default=1800.0)
    show = receipt_actions.add_parser("show", help="which receipts hold over a revision")
    show.add_argument("--tree", default=".")
    show.add_argument("--rev", default="HEAD")
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
    if args.command in ("work", "tree", "receipt"):
        from flotilla.ledger.commands import run_ledger_command
        return run_ledger_command(args)
    return 2
