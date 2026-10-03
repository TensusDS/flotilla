---
name: watch
description: Check the fleet once for what needs attention - deviations, dropped balls (a live session holding a move and not working), a move for a post no live session holds, and sessions that stopped twice while holding a move.
disable-model-invocation: true
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/bin/flotilla watch --once*)
compatibility: Claude Code only (CLI, IDE or desktop Code tab) - needs the Bash tool, git worktrees and claude --bg; not claude.ai chat or Cowork.
---

Run `${CLAUDE_PLUGIN_ROOT}/bin/flotilla watch --once` from the repository root.

Report every line it printed, attention items first. Its exit code says what the check found: 0 nothing needs
attention, 1 something does, 2 the census or the ledger could not be asked. On 2, say what could not be asked and
never report "all clear". Do not make any move yourself; this command only reads. A dropped ball is answered by the
orchestrator messaging the session named in it.
