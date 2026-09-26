---
name: brief
description: Show the batch ready to ship in one block - every row, whose it is, who accepted it over which revision, what is held back and why, and the gate it will stand on - so the person can answer once.
disable-model-invocation: true
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/scripts/flotilla brief*)
---

Run `${CLAUDE_PLUGIN_ROOT}/scripts/flotilla brief` from the repository root and show its output as it is, in one
block. Do not shorten the gate line: when it says nothing verifies these commits, the person must read that before
answering. Do not ship anything yourself; the sender does, after the person's yes.
