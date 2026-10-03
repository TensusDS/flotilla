---
name: doctor
description: Check this machine and project for flotilla — Python, platform, git, Claude Code version, the session census, the state directory and the project profile.
disable-model-invocation: true
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/bin/flotilla doctor*)
compatibility: Claude Code only (CLI, IDE or desktop Code tab) - needs the Bash tool, git worktrees and claude --bg; not claude.ai chat or Cowork.
---

Run `${CLAUDE_PLUGIN_ROOT}/bin/flotilla doctor` from the repository root and report every line it prints.
Lines marked `fail` come first, each with the fix the command names. A census marked unknown means the number of
live sessions could not be asked; never report it as zero sessions.
