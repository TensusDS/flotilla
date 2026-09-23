# flotilla

Coordinate independent peer Claude Code sessions on one machine: named posts (orchestrator, sender, reviewer,
acceptance judge, main, minor), a worktree per session, a work ledger in which every move costs evidence, a lane
that books the machine for long runs, and guards on dangerous commands.

**Status: pre-alpha.** The foundation is in place; the ledger, spawning, the lane and the guards are being built.
Design: `docs/specs/2026-09-22-flotilla-design.md`.

## Requirements

Linux or macOS, Python 3.11+, git, Claude Code 2.1.280+.

macOS is supported by design but **not yet verified in CI**: continuous integration runs on a self-hosted Linux
runner. Of the macOS code paths, only process-parent lookup through `ps` is exercised, because the tests run it on
Linux too.

## Check your machine

```bash
scripts/flotilla doctor
```

## Where flotilla keeps state

`${FLOTILLA_STATE_DIR}` if set, else `${XDG_STATE_HOME:-~/.local/state}/flotilla/`. It survives plugin updates and
uninstall on purpose. To erase the fleet's history, delete that directory; `flotilla doctor` prints its path.

## Development

```bash
uv run --with pytest python -m pytest                   # the suite
uv run --python 3.11 --with pytest python -m pytest     # on the lowest supported Python
python3 tools/check_no_cyrillic.py                      # the English-only gate
claude plugin validate .                                # the manifest
```
