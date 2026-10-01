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
    hook.add_argument("event", choices=["session-start", "prompt", "stop", "guard", "permission", "ask"])
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
    write.add_argument("--confirm", default="", help="the mark `write` printed with the commands the person agreed to")
    work = sub.add_parser("work", help="move a row of the work ledger")
    moves = work.add_subparsers(dest="move", required=True)

    def move_parser(name: str, text: str):
        parser_ = moves.add_parser(name, help=text)
        parser_.add_argument("branch")
        parser_.add_argument("--root", default=".")
        parser_.add_argument("--as", dest="as_name", default=None, help="act as this session (recorded)")
        parser_.add_argument("--skip-event", dest="skip_event", default=None,
                             help="skip one event script, on a person's decision (recorded); needs --skip-why")
        parser_.add_argument("--skip-why", dest="skip_why", default="")
        return parser_

    claim = move_parser("claim", "claim work on a branch")
    claim.add_argument("--tree", default="")
    claim.add_argument("--ref", default="")
    claim.add_argument("--requires", nargs="*", default=[], help="rows this one builds on (walked with them)")
    claim.add_argument("--after", nargs="*", default=[], help="rows this one only goes after (never a part of them)")
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
    release = move_parser("release", "say the work will not happen, or name the delivered row that fulfilled it")
    release.add_argument("--why", default="")
    release.add_argument("--settled-by", dest="settled_by", default="", metavar="BRANCH")
    move_parser("queue", "put accepted work in the sender's batch").add_argument("--pr", type=int, default=None)
    move_parser("land", "record the trunk commit that carries the work").add_argument("--merge", default=None)
    move_parser("ship", "ask the PR or origin whether the work shipped; record it if it did")
    inbatch = move_parser("inbatch", "record a change born in the batch (BRANCH is a label)")
    inbatch.add_argument("--commit", required=True)
    inbatch.add_argument("--read-by", dest="read_by", required=True)
    inbatch.add_argument("--why", required=True)
    back = move_parser("return", "send an accepted or queued row back to its author (sender)")
    back.add_argument("--why", required=True)
    move_parser("approve", "approve accepted work for trunk where a person authorizes merges (the person only)")
    vouch = move_parser("vouch", "vouch for a batch commit no verdict covers (reader)")
    vouch.add_argument("--commit", required=True)
    offledger = move_parser("offledger", "record work that reached trunk outside the ledger")
    offledger.add_argument("--merge", required=True)
    offledger.add_argument("--witness", required=True)
    offledger.add_argument("--attested", default="")
    walked = move_parser("walked", "record a walk of shipped work on the deployed build")
    walked.add_argument("--build", required=True)
    walked.add_argument("--steps", required=True)
    walked.add_argument("--saw", required=True)
    broke = move_parser("broke", "record where shipped work broke; files the fix row")
    broke.add_argument("--where", required=True)
    broke.add_argument("--saw", required=True)
    broke.add_argument("--fix-branch", dest="fix_branch", default="")
    move_parser("unbroke", "take a broke back: it was the judge's mistake (judge)").add_argument("--why",
                                                                                                required=True)
    close = move_parser("close", "close your shipped work")
    close.add_argument("--ref", default="")
    close.add_argument("--why", default="")
    move_parser("adopt", "give work whose owner is gone to a live session").add_argument("--to", required=True)
    hold = move_parser("hold", "keep a handed branch out of the reading queue, with a condition")
    hold.add_argument("--until", required=True)
    hold.add_argument("--why", required=True)
    move_parser("unhold", "lift a hold")
    walkable = move_parser("walkable", "mark a part walkable on its own, or take the mark back (orchestrator)")
    walkable.add_argument("--why", default="")
    walkable.add_argument("--clear", action="store_true")
    urgent = move_parser("urgent", "ask for a row to ship out of turn")
    urgent.add_argument("--why", default="")
    urgent.add_argument("--cancel", action="store_true")
    reconcile = moves.add_parser("reconcile", help="ask the PR or origin about every queued or landed row")
    reconcile.add_argument("--root", default=".")
    reconcile.add_argument("--as", dest="as_name", default=None, help="act as this session (recorded)")
    move_parser("show", "print a row and its history")

    tree = sub.add_parser("tree", help="worktrees filed in the ledger")
    tree_actions = tree.add_subparsers(dest="action", required=True)
    cut = tree_actions.add_parser("cut", help="cut a worktree from trunk and claim it")
    cut.add_argument("branch")
    cut.add_argument("--tree", required=True)
    cut.add_argument("--expect", default=None)
    cut.add_argument("--ref", default="")
    cut.add_argument("--requires", nargs="*", default=[], help="rows this one builds on (walked with them)")
    cut.add_argument("--after", nargs="*", default=[], help="rows this one only goes after (never a part of them)")
    cut.add_argument("--also", default="")
    cut.add_argument("--root", default=".")
    cut.add_argument("--as", dest="as_name", default=None)
    switch = tree_actions.add_parser("switch", help="move your home tree to a new branch from trunk and claim it")
    switch.add_argument("branch")
    switch.add_argument("--ref", default="")
    switch.add_argument("--requires", nargs="*", default=[], help="rows this one builds on (walked with them)")
    switch.add_argument("--after", nargs="*", default=[], help="rows this one only goes after (never a part of them)")
    switch.add_argument("--also", default="")
    switch.add_argument("--root", default=".")
    switch.add_argument("--as", dest="as_name", default=None)

    receipt = sub.add_parser("receipt", help="test-tier receipts over one revision")
    receipt_actions = receipt.add_subparsers(dest="action", required=True)
    run = receipt_actions.add_parser("run", help="run the tiers for a purpose over this tree's HEAD")
    run.add_argument("--purpose", choices=["handover", "push"], required=True)
    run.add_argument("--tree", default=".")
    run.add_argument("--timeout", type=float, default=1800.0)
    run.add_argument("--lane-wait", dest="lane_wait", type=float, default=1800.0,
                     help="seconds to wait for the lane (default 1800)")
    run.add_argument("--no-lane", dest="no_lane", action="store_true", help="run without booking the lane")
    show = receipt_actions.add_parser("show", help="which receipts hold over a revision")
    show.add_argument("--tree", default=".")
    show.add_argument("--rev", default="HEAD")

    events_ = sub.add_parser("events", help="the project's event scripts")
    event_actions = events_.add_subparsers(dest="action", required=True)
    check_ = event_actions.add_parser("check", help="check every event script on trunk (or in this tree)")
    check_.add_argument("--root", default=".")
    check_.add_argument("--tree", action="store_true", help="check this working tree's scripts instead of trunk's")
    replay = event_actions.add_parser("run", help="run one event script over a row, as a move would")
    replay.add_argument("name")
    replay.add_argument("--row", required=True)
    replay.add_argument("--root", default=".")
    event_actions.add_parser("schema", help="print the event contract")
    status = sub.add_parser("status", help="who does what, whose move, deviations, findings, stalled work")
    status.add_argument("--root", default=".")
    status.add_argument("--stalled", type=float, default=None, metavar="HOURS",
                        help="also list open rows that have not moved for this many hours")
    sub.add_parser("brief", help="the batch ready to ship, for one yes").add_argument("--root", default=".")
    sub.add_parser("metrics", help="time in state, returns, reader throughput, event failures").add_argument(
        "--root", default=".")
    from flotilla.fleet.compose import FLAGS
    spawn_ = sub.add_parser("spawn", help="raise background sessions, each with a post and a home worktree")
    for post, flag in FLAGS.items():
        spawn_.add_argument(flag, dest=f"count_{post}", type=int, default=0, metavar="N", help=f"{post} sessions")
    spawn_.add_argument("--post", action="append", default=[], metavar="NAME=N", help="sessions of any post")
    spawn_.add_argument("--default", action="store_true", help="the profile's fleet.default composition")
    spawn_.add_argument("--dry-run", action="store_true", help="show names, trees and commands; change nothing")
    spawn_.add_argument("--anyway", action="store_true", help="raise even under the memory floor")
    spawn_.add_argument("--root", default=".")
    retire_ = sub.add_parser("retire", help="stop a session and release its post; its work stays")
    retire_.add_argument("name")
    retire_.add_argument("--root", default=".")
    fleet_ = sub.add_parser("fleet", help="the fleet's sessions, their trees and their work; `down` retires them")
    fleet_.add_argument("action", nargs="?", choices=["down"], help="retire every seat but your own")
    fleet_.add_argument("--root", default=".")
    helper_ = sub.add_parser("helper", help="raise a helper session for a piece of your work, or finish as one")
    helper_sub = helper_.add_subparsers(dest="helper_action", required=True)
    helper_raise = helper_sub.add_parser("raise", help="raise a helper in its own tree, from your branch's tip")
    helper_raise.add_argument("--for", dest="branch", required=True, help="your open branch it helps")
    helper_raise.add_argument("--task", required=True, help="what the helper is to do")
    helper_raise.add_argument("--anyway", action="store_true", help="raise even under the memory floor")
    helper_raise.add_argument("--as", dest="as_name", default=None, help=argparse.SUPPRESS)
    helper_raise.add_argument("--root", default=".")
    helper_done = helper_sub.add_parser("done", help="record what you did as a helper and release your seat")
    helper_done.add_argument("--summary", required=True)
    helper_done.add_argument("--as", dest="as_name", default=None, help=argparse.SUPPRESS)
    helper_done.add_argument("--root", default=".")
    watch = sub.add_parser("watch", help="what the fleet needs attention for (exit 0 none, 1 some, 2 unknown)")
    watch.add_argument("--once", action="store_true", help="check once and exit (v1 has no schedule of its own)")
    watch.add_argument("--wait", type=float, default=0.0,
                       help="block up to this many seconds until something new needs attention (exit 1), or "
                            "nothing new (exit 0)")
    watch.add_argument("--interval", type=float, default=20.0, help=argparse.SUPPRESS)
    watch.add_argument("--confirm", type=float, default=5.0, help=argparse.SUPPRESS)
    watch.add_argument("--root", default=".")
    from flotilla.guards import CEILING
    guard = sub.add_parser("guard", help="the command guards and their git hooks", epilog=CEILING,
                           formatter_class=argparse.RawDescriptionHelpFormatter)
    guard_actions = guard.add_subparsers(dest="action", required=True)
    guard_check = guard_actions.add_parser("check", help="what the guards would say about a command, without it")
    guard_check.add_argument("guarded_command", metavar="COMMAND")
    guard_check.add_argument("--cwd", default=".")
    guard_actions.add_parser("status", help="which guards are on, which git hooks are installed").add_argument(
        "--root", default=".")
    guard_install = guard_actions.add_parser("install", help="install the git hooks named, one flag each")
    guard_install.add_argument("--pre-commit", action="store_true", help="file reservation")
    guard_install.add_argument("--pre-push", action="store_true", help="the second push barrier")
    guard_install.add_argument("--root", default=".")
    guard_hook = guard_actions.add_parser("githook", help="entry point for the git hooks")
    guard_hook.add_argument("name", choices=["pre-commit", "pre-push"])
    guard_hook.add_argument("hook_args", nargs="*")
    permit = sub.add_parser("permit", help="permission questions from background sessions, one at a time")
    permit_actions = permit.add_subparsers(dest="action", required=True)
    permit_actions.add_parser("list", help="every question that still waits").add_argument("--root", default=".")
    permit_next = permit_actions.add_parser("next", help="the oldest question and its three answers")
    permit_next.add_argument("--wait", type=float, default=0.0, help="seconds to wait for one (default 0)")
    permit_next.add_argument("--root", default=".")
    permit_answer = permit_actions.add_parser("answer", help="answer a question: allow, session or deny")
    permit_answer.add_argument("id")
    permit_answer.add_argument("choice", choices=["allow", "session", "deny"])
    permit_answer.add_argument("--why", default="")
    permit_answer.add_argument("--mark", default="", help="the question's mark, as `flotilla permit next` printed it")
    permit_answer.add_argument("--root", default=".")
    import argparse as _argparse
    lane = sub.add_parser("lane", help="book the machine for long runs (not a lock)")
    lane.add_argument("--root", default=".")
    lane_actions = lane.add_subparsers(dest="action")
    lane_take = lane_actions.add_parser("take", help="book the machine by hand; release it when done")
    lane_take.add_argument("--note", default="")
    lane_take.add_argument("--wait", type=float, default=0.0, help="seconds to wait for it (default 0)")
    lane_release = lane_actions.add_parser("release", help="release a booking taken by hand")
    lane_release.add_argument("--booking", default=None)
    lane_run = lane_actions.add_parser("run", help="book, run a command, release; record it on a row with --for")
    lane_run.add_argument("--for", dest="for_", default=None, metavar="BRANCH")
    lane_run.add_argument("--note", default="")
    lane_run.add_argument("--wait", type=float, default=1800.0, help="seconds to wait for the lane (default 1800)")
    lane_run.add_argument("--tree", default=".")
    lane_run.add_argument("run_command", nargs=_argparse.REMAINDER, metavar="COMMAND")
    lane_actions.add_parser("sweep", help="remove bookings whose process is gone")
    for item in (lane_take, lane_release, lane_run, lane_actions.choices["sweep"]):
        item.add_argument("--root", default=_argparse.SUPPRESS)
        item.add_argument("--as", dest="as_name", default=None)
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
    if args.command in ("work", "tree", "receipt", "events", "status", "brief", "metrics"):
        from flotilla.ledger.commands import run_ledger_command
        return run_ledger_command(args)
    if args.command in ("spawn", "retire", "fleet", "helper"):
        from flotilla.fleet.commands import run_fleet_command
        return run_fleet_command(args)
    if args.command == "lane":
        from flotilla.lane.commands import run_lane_command
        return run_lane_command(args)
    if args.command == "watch":
        from flotilla.watch.commands import run_watch_command
        return run_watch_command(args)
    if args.command == "guard":
        from flotilla.guards.commands import run_guard_command
        return run_guard_command(args)
    if args.command == "permit":
        from flotilla.broker.commands import run_permit_command
        return run_permit_command(args)
    return 2
