# flotilla

Coordinate independent peer Claude Code sessions on one machine: named posts (orchestrator, sender, reviewer,
acceptance judge, main, minor), a worktree per session, a work ledger in which every move costs evidence, a lane
that books the machine for long runs, and guards on dangerous commands.

**Status: pre-alpha.** The foundation, onboarding, the work ledger (`/flotilla:status`, `/flotilla:brief`), the
fleet (`/flotilla:spawn`, `/flotilla:retire`), the lane (`/flotilla:lane`) and the watchers (`/flotilla:watch`,
session hooks) are in place; the guards are being built. Design: `docs/specs/2026-09-22-flotilla-design.md`.

## Requirements

Linux or macOS, Python 3.11+, git, Claude Code 2.1.280+.

Both Linux and macOS run in CI on every commit.

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

## Development

```bash
uv run --with pytest python -m pytest                   # the suite
uv run --python 3.11 --with pytest python -m pytest     # on the lowest supported Python
python3 tools/check_no_cyrillic.py                      # the English-only gate
claude plugin validate .                                # the manifest
```
