# flotilla

Coordinate independent peer Claude Code sessions on one machine: named posts (orchestrator, sender, reviewer,
acceptance judge, main, minor), a worktree per session, a work ledger in which every move costs evidence, a lane
that books the machine for long runs, and guards on dangerous commands.

**Status: pre-alpha.** The foundation, onboarding, the work ledger (`/flotilla:status`, `/flotilla:brief`), the
fleet (`/flotilla:spawn`, `/flotilla:retire`), the lane (`/flotilla:lane`), the watchers (`/flotilla:watch`,
session hooks), the guards (`/flotilla:guard`) and the permission broker (`/flotilla:permit`) are in place. Design: `docs/specs/2026-09-22-flotilla-design.md`.

## Requirements

Linux or macOS, Python 3.11+, git, Claude Code 2.1.280+.

Both Linux and macOS run in CI on every commit.

## Install

flotilla is its own marketplace. In the project a fleet will work on:

    claude plugin marketplace add TensusDS/flotilla
    claude plugin install flotilla@flotilla --scope project

`--scope project` records it in the project's `.claude/settings.json`, so every session started in the main
checkout — the ones `flotilla spawn` raises included — loads flotilla. Then run `flotilla doctor` there. That file
enables flotilla and commits a marketplace entry that points at this repository, so everyone who opens the project
and trusts it installs flotilla from here.

## Check your machine

```bash
scripts/flotilla doctor
```

## Where flotilla keeps state

`${FLOTILLA_STATE_DIR}` if set, else `${XDG_STATE_HOME:-~/.local/state}/flotilla/`. It survives plugin updates and
uninstall on purpose. To erase the fleet's history, delete that directory; `flotilla doctor` prints its path.

## What the hooks say

At session start flotilla names the session, its post, its live peers and what it inherited. Before a prompt it
says what waits for this session's move — at once when that changed, otherwise at most every 30 minutes — and tells
the orchestrator what the fleet dropped. When a background session tries to stop while it holds a move, with
nothing in flight and no wait recorded, the Stop guard asks it to move or record the wait; a second stop goes
through and the orchestrator hears of it. `[watch] stop_guard = false` in `.flotilla/project.toml` turns the guard
off. For a scheduler: `flotilla watch --once` exits 0 when nothing needs attention, 1 when something does, 2 when it
could not ask.

## Guards

Before a Bash command runs, flotilla refuses three that do silent damage: a checkout, restore, reset or clean that
would destroy uncommitted work (it names the fix that works for that form); an in-place `sed` edit addressed by
line number; and a push to trunk or a tag, `gh pr create`, `gh pr merge` or `gh workflow run` without a green push
receipt over exactly what is pushed. A push to another branch needs none. `FLOTILLA_GATE_OVERRIDE="<why>"` at the
head of the push lets it through and records the reason. Two git hooks back them: `pre-commit` refuses a rewrite
of a shared file another open row has reserved (appends always pass), and `pre-push` asks git what is pushed, so a
push inside a script is judged too. `/flotilla:guard` shows what is on and installs the hooks, one yes each.

The command guards read the command line, not the shell: a command inside `eval`, `sh -c`, `$( )` or a script is
not seen (`flotilla guard --help` lists the rest). A revert or line-number guard that cannot tell lets the command
run and says so; the push guard refuses.

## Permission questions

A background session cannot show a permission prompt. When the project runs its sessions in `ask` mode, the
question goes to the orchestrator instead: `/flotilla:permit` shows the person one question at a time, oldest
first, with three answers — allow once, allow for the session (what that adds is said in words), deny with a reason
the session reads. A question nobody answers in nine minutes (`[broker] wait_seconds`) is refused on its own, before
Claude Code's own timeout would leave the session hanging. With no orchestrator alive, the question is refused at
once, naming the fix.

## Development

```bash
uv run --with pytest python -m pytest                   # the suite
uv run --python 3.11 --with pytest python -m pytest     # on the lowest supported Python
python3 tools/check_no_cyrillic.py                      # the English-only gate
claude plugin validate .                                # the manifest
```

## License

MIT - see `LICENSE`.
