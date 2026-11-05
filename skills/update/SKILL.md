---
name: update
description: Keep flotilla current on this machine - show every flotilla install (the user's and each project's) with its version, the channel the machine follows and the newest release, then, on the person's yes, move the machine to the stable channel with automatic updates (or pin one release) and bring every install up. A project whose fleet is working is stood down first or its update deferred, as the person chooses. Use when doctor or a session's start says a pin holds the machine on an older release, when `claude plugin marketplace add` refused with "doesn't match its extraKnownMarketplaces entry", or when someone asks to update, upgrade or turn on automatic updates for flotilla.
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/bin/flotilla update), Bash(${CLAUDE_PLUGIN_ROOT}/bin/flotilla update --keep-channel)
disable-model-invocation: true
compatibility: Claude Code only (CLI, IDE or desktop Code tab) - needs the Bash tool and the `claude` command; not claude.ai chat or Cowork.
---

# Keep flotilla current on this machine

`flotilla update` reads and changes nothing. With `--confirm <mark>` it carries out exactly the plan it printed
that mark for - only for a person (a background session is refused), and this skill does not pre-approve it, so
Claude Code asks the person before it runs. The CLI is `${CLAUDE_PLUGIN_ROOT}/bin/flotilla`.

What it never does: edit Claude Code's own records, remove the marketplace (that uninstalls the plugin in every
project), uninstall anything, or rewrite a marketplace that is not `TensusDS/flotilla` on GitHub. Projects whose
committed `.claude/settings.json` pins another release are named with the edit to make; that edit is a commit in
that repository, its owner's.

## 1. Show what is there

Run `flotilla update`. Show the person, as printed: the channel, the newest release, every install with its
version, the projects pinned off the channel, and every project where a fleet is working (its background seats,
and the person's own sessions there).

## 2. Ask how to follow releases

Ask with AskUserQuestion, header "Updates", question "How should this machine follow flotilla releases?":

- **"Stable with automatic updates (Recommended)"** - "The machine follows the `stable` channel, which moves only
  to a released version; Claude Code updates the copies on disk by itself, and the next session started runs the
  new release."
- **"Pin the newest release"** - "The machine stays on v<newest> until you move it; nothing updates by itself."
- **"Leave it"** - "The channel stays as it is; only the installs listed behind are brought up."

"Pin" adds `--pin v<newest>`; "Leave it" adds `--keep-channel`.

## 3. Ask about each working fleet

For each project the report lists under "fleet working in", ask with AskUserQuestion, header "Fleet", question
"A fleet is working in <project> (<its seats>). What should the update do there?":

- **"Stop the fleet and update"** - "Its background seats are stood down as /flotilla:down does - open work is
  named and kept for the next fleet - then its install is updated. Your own session there keeps running on the old
  release until you start it again. Raise the fleet again with /flotilla:spawn."
- **"Defer"** - "Its fleet keeps working on the release it has; its install stays as it is, and its next session
  start and /flotilla:down remind you that the update waits."

"Stop" adds `--stop <project>`, "Defer" adds `--defer <project>`. If the person wants a project left out
entirely, add `--skip <project>`.

## 4. Show the plan and carry it out

Run `flotilla update` with the options the answers chose. Show the plan lines and the mark exactly as printed, and
ask: "Carry out this plan?" Only on a clear yes, run the same command with `--confirm <mark>`. If it says the plan
changed, run it again without `--confirm` and show the new plan. Report every line it prints - each update, any
refusal - and that sessions already running keep the release they loaded.
