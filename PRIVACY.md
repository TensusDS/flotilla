# Privacy policy

Last updated: 2026-10-03. Applies to flotilla 0.7.0 and later.

flotilla is a Claude Code plugin that runs entirely on your machine. **It sends nothing anywhere**: it has no
server, no account, no telemetry and no analytics, and its authors receive no data from it.

## What it reads

- **Your repository** - its git history, branches, worktrees and the `.flotilla/` profile on trunk - to keep the
  work ledger and judge the commands its guards watch.
- **The Bash commands your sessions are about to run**, through Claude Code's PreToolUse hook, to decide whether a
  guard refuses one. A command is judged in memory; only what is listed below is written down.
- **Claude Code's own list of running sessions** (`claude agents --json`: names, ids, working directories) to know
  which session holds which post, and `~/.claude.json` to see whether a directory is trusted. It writes neither.
- **Permission questions** a background session raises in `ask` mode, so the orchestrator can put them to you.

It does not read your conversations, Claude's memory, chat history or uploaded files.

## What it stores, where, and for how long

Everything lives in one directory on your machine: `${FLOTILLA_STATE_DIR}` if set, else
`${XDG_STATE_HOME:-~/.local/state}/flotilla/`, made readable by your user only (0700). `flotilla doctor` prints
its path.

| What | Contents | Kept |
| --- | --- | --- |
| Work ledger | every move on a piece of work: who made it (a session name), the branch, commit ids, the reason a session gave, the evidence | until you delete it |
| Run receipts | which test tiers ran over which commit, their result, and the last lines a failing run printed | until you delete them |
| Permission questions | who asked, when, the answer - and, while it waits, the call it asks about (a command, a file's new content) | the call: until the asking session has its answer or stops waiting; the rest: one day |
| Override records | a command run with `FLOTILLA_GATE_OVERRIDE` and the reason given, with any `NAME=value` and URL password removed | until you delete them |
| Fleet and lane state | seat names, which session leads, machine bookings for long runs | until you delete them |

The directory survives plugin updates and uninstalling on purpose, so a ledger is never lost by accident. To
remove everything, delete it.

In your repository flotilla writes only what you ask it to: the `.flotilla/` profile at onboarding, worktrees for
the seats you raise, and - only when you say yes in `/flotilla:guard` - `pre-commit` and `pre-push` hooks in
`.git/hooks/`.

## Network

flotilla itself opens no connection. The commands it runs for you reach what they always reach: `git` your own
remote, `gh` GitHub when your project ships through it, and your project's own test commands whatever they call.
Claude Code's own data handling is covered by Anthropic's terms, not this policy.

## Children

flotilla is a developer tool and is not directed at anyone under 18.

## Changes and contact

Changes to this policy are listed in [CHANGELOG.md](CHANGELOG.md). Questions: open an issue at
<https://github.com/TensusDS/flotilla/issues>; security concerns: see [SECURITY.md](SECURITY.md).
