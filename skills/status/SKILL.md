---
name: status
description: Show the fleet's work - who is idle, working, waiting on whom or blocked, whose move each open row is, what deviates from the route, what git shows outside the ledger, and, given hours, what stalled.
disable-model-invocation: true
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/bin/flotilla status*)
compatibility: Claude Code only (CLI, IDE or desktop Code tab) - needs the Bash tool, git worktrees and claude --bg; not claude.ai chat or Cowork.
---

Run `${CLAUDE_PLUGIN_ROOT}/bin/flotilla status` from the repository root. If the person passed a number of
hours ($ARGUMENTS), run `${CLAUDE_PLUGIN_ROOT}/bin/flotilla status --stalled $ARGUMENTS` instead.

Report in this order, keeping every line the command printed:
1. deviations and findings first — each is a move that cannot happen now, or work git sees and the ledger does not;
2. whose move each open row is, naming the session (or "the sender", "the judge");
3. the roster.

A census line that says unknown means liveness was not asked: never report it as "nobody is alive" or "everyone is
fine". Do not make any move yourself; this command only reads.
