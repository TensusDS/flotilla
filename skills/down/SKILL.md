---
name: down
description: Stand the fleet down - retire every flotilla session of this project but your own, clean up the trees and branches whose work exists elsewhere, and name what stays and the work left orphaned.
disable-model-invocation: true
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/bin/flotilla fleet*)
compatibility: Claude Code only (CLI, IDE or desktop Code tab) - needs the Bash tool, git worktrees and claude --bg; not claude.ai chat or Cowork.
---

1. Run `${CLAUDE_PLUGIN_ROOT}/bin/flotilla fleet` and show every line: each seat, its state, its tree and its
   open work.
2. If any seat holds open work or uncommitted changes, say which, and ask the person to confirm standing the fleet
   down anyway. Otherwise ask them to confirm once.
3. Run `${CLAUDE_PLUGIN_ROOT}/bin/flotilla fleet down` and show every line it prints. It ends by cleaning up:
   trees whose every commit and file exists elsewhere are removed and their branches judged on their own, commits
   only a reflog reaches are parked under `refs/flotilla/parked/`, and the rest are kept with why. Orphaned rows wait
   for `flotilla work adopt`; a seat it could not retire is named with the reason. Never remove a tree yourself.
