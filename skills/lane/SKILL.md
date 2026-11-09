---
name: lane
description: Show who holds the machine for long runs - bookings, the queue, unbooked runs computing, CI on this machine - so a person can see why a run waits.
disable-model-invocation: true
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/bin/flotilla lane)
compatibility: Claude Code only (CLI, IDE or desktop Code tab) - needs the Bash tool, git worktrees and claude --bg; not claude.ai chat or Cowork.
---

Run `${CLAUDE_PLUGIN_ROOT}/bin/flotilla lane` from the repository root and show every line it prints: whether
the lane daemon runs, who holds the lane and since when - with each run's memory ceiling and pressure - who waits
in which order and why, and the machine's answers. A line marked `unknown` means a question could not be asked;
never report it as free. Showing the lane ends bookings whose process no longer exists, and says so on the line
after the daemon's; do not take, release or run `lane sweep` yourself.
