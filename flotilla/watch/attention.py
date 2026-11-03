"""How the person is asked: `all` (the default) or `digest` (person inbox design, goal 6).

The mode lives in `machine.toml` among the person's own keys, which Claude's edit tools are refused
(`guards/files.py`), and only the person's command sets it: the person guard refuses a Bash tool call that sets it,
and `caller.person_refusal` refuses a background session. A mode changes when the line on the person's screen shows,
never what `flotilla inbox` lists. Whatever the file says that is not usable reads as `all`: a broken setting must
never quiet the line.
"""

from __future__ import annotations

from pathlib import Path

ALL, DIGEST = "all", "digest"
MODES = (ALL, DIGEST)
DEFAULT_EVERY = 30   # minutes
MAX_EVERY = 7 * 24 * 60   # a week: past what a timedelta holds, every prompt failed (security review of 5f65ad5)


def mode(state: Path) -> tuple[str, int]:
    from flotilla.onboard.machine import read_machine
    try:
        data = read_machine(Path(state)) or {}
    except (OSError, ValueError):
        return ALL, DEFAULT_EVERY
    chosen = data.get("attention")
    every = data.get("attention_every", DEFAULT_EVERY)
    if isinstance(every, bool) or not isinstance(every, int) or every < 1:
        every = DEFAULT_EVERY
    every = min(every, MAX_EVERY)
    return (chosen if chosen in MODES else ALL), every


def run_attention_command(args) -> int:
    from flotilla.core import caller, paths
    state = paths.state_dir()
    if args.action is None:
        if args.every is not None:
            print("refused: --every sets the digest's period; name the mode too: `flotilla attention digest --every "
                  f"{args.every}`")
            return 2
        chosen, every = mode(state)
        print(f"attention: {chosen}" + (f", every {every} min" if chosen == DIGEST else "")
              + " - set by the person with `! flotilla attention all|digest [--every MIN]`")
        return 0
    if args.every is not None and args.action != DIGEST:
        print("refused: --every is the digest's period; `all` shows the line when the queue changes")
        return 2
    if args.every is not None and not 1 <= args.every <= MAX_EVERY:
        print(f"refused: --every is a whole number of minutes, from 1 to {MAX_EVERY} (a week)")
        return 2
    refused = caller.person_refusal("decides how they are asked")
    if refused:
        print(f"refused: {refused}")
        return 2
    from flotilla.onboard.machine import set_person_keys
    keys = {"attention": args.action}
    if args.every is not None:
        keys["attention_every"] = args.every
    try:
        set_person_keys(Path(state), **keys)
    except ValueError as err:
        print(f"refused: machine.toml cannot be read ({err}); repair it first")
        return 2
    chosen, every = mode(state)
    print(f"attention: {chosen}" + (f", every {every} min; a blocker is shown at once" if chosen == DIGEST else
                                    "; the line shows when what waits on you changes"))
    return 0
