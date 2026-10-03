# Privacy policy

Last updated: 2026-10-03. Applies to flotilla 0.7.0 and later.

flotilla is a Claude Code plugin that runs on your machine. It has no server, no account, no telemetry and no
analytics, and its authors receive no data from it. The only network calls it makes are `git` to your repository's
own remote and `gh` to GitHub, with your credentials - listed under [Network](#network).

## What it reads

- **Your repository** - its git history, branches, worktrees and the `.flotilla/` profile on trunk - to keep the
  work ledger and judge the commands its guards watch.
- **The Bash commands your sessions are about to run**, through Claude Code's PreToolUse hook, to decide whether a
  guard refuses one. A command is judged in memory; only what is listed below is written down.
- **What Claude Code says about its sessions and plugins**, to know which session holds which post and what a seat
  will load: the list of running sessions (`claude agents --json`: names, ids, working directories, status), each
  session's registry entry (`~/.claude/sessions/<pid>.json`, for how it was started), `~/.claude.json` for whether
  a directory is trusted, `claude plugin list --json` and the manifests of the plugins it names. It writes none of
  them.
- **Permission questions** a background session raises in `ask` mode, so the orchestrator can put them to you.

It does not read your conversations, session transcripts, Claude's memory, chat history or uploaded files.

## What it stores, where, and for how long

Everything lives in one directory on your machine: `${FLOTILLA_STATE_DIR}` if set, else
`${XDG_STATE_HOME:-~/.local/state}/flotilla/`, made readable by your user only (0700). `flotilla doctor` prints
its path.

| What | Contents | Kept |
| --- | --- | --- |
| Work ledger | every move on a piece of work: who made it (a session name), the branch, commit ids, the reason a session gave, the evidence | until you delete it |
| Run receipts | which test tiers ran over which commit, their result, and the last lines a failing run printed | until you delete them |
| Permission questions | who asked, when, the answer - and, while it waits, the call it asks about (a command, a file's new content) | the call: until the asking session has its answer or stops waiting (if that session was killed mid-wait, until flotilla next reads the queue); the rest: one day after the answer |
| Override records | a command run with `FLOTILLA_GATE_OVERRIDE` and the reason given, with any `NAME=value` and URL password removed | until you delete them |
| Fleet and lane state | seat names, which session leads, machine bookings for long runs | until you delete them |
| Hook activity | per session id: which hook last ran, and when | until you delete it |

The directory survives plugin updates and uninstalling on purpose, so a ledger is never lost by accident. To
remove everything, delete it.

In your repository flotilla writes only what you ask it to: the `.flotilla/` profile at onboarding, worktrees for
the seats you raise, and - only when you say yes in `/flotilla:guard` - `pre-commit` and `pre-push` hooks in
`.git/hooks/`.

## Network

flotilla has no service of its own to talk to. These are all the network calls it makes, from its hooks and its
commands:

- **`git ls-remote`** to your repository's `origin` - from the push guard, and before letting a checked command past
  auto mode's classifier - to learn what origin's trunk is;
- **`git fetch origin <trunk>`** when a session cuts or switches a branch, delivers work, or the fleet is cleaned up;
- **`git push origin <trunk>`** once, in `flotilla onboard publish`, to bring the onboarding commit to trunk after
  its tests ran green;
- **`gh`** (with the token you gave it) when your project ships through GitHub: `gh pr view` for a merge it judges,
  `gh run list` / `gh run view` for CI results, `gh repo view` and `gh auth status` while onboarding.

Nothing goes to any other host. Your project's own test commands, which flotilla runs when you ask for a receipt,
reach whatever they reach. The background sessions `flotilla spawn` starts are Claude Code sessions: what they send
to Anthropic is covered by Claude Code's terms, not this policy.

## Children

flotilla is a developer tool and is not directed at anyone under 18.

## Changes and contact

Changes to this policy are listed in [CHANGELOG.md](CHANGELOG.md). Questions: open an issue at
<https://github.com/TensusDS/flotilla/issues>; security concerns: see [SECURITY.md](SECURITY.md).
