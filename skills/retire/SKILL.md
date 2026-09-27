---
name: retire
description: Retire a flotilla session - stop it, release its post, keep its worktree and name the work it leaves orphaned.
disable-model-invocation: true
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/scripts/flotilla retire*), Bash(${CLAUDE_PLUGIN_ROOT}/scripts/flotilla fleet*)
---

1. Run `${CLAUDE_PLUGIN_ROOT}/scripts/flotilla fleet` and show the line for the session named in $ARGUMENTS: its
   state, its tree and its open work.
2. If it holds open work or its tree has uncommitted changes, say so and ask the person to confirm the retire.
3. Run `${CLAUDE_PLUGIN_ROOT}/scripts/flotilla retire "$ARGUMENTS"` and show every line it prints. The tree is kept;
   orphaned rows wait for `flotilla work adopt`. Never remove the tree yourself.
