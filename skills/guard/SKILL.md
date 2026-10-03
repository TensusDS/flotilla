---
name: guard
description: Show which command guards are on and which git hooks are installed, install the git hooks the profile asks for (one yes each), or check what the guards would say about a command.
disable-model-invocation: true
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/bin/flotilla guard *)
compatibility: Claude Code only (CLI, IDE or desktop Code tab) - needs the Bash tool, git worktrees and claude --bg; not claude.ai chat or Cowork.
---

If the person passed a command ($ARGUMENTS), run `${CLAUDE_PLUGIN_ROOT}/bin/flotilla guard check "$ARGUMENTS"`
and report every line: refused, warning, or allowed. Otherwise:

1. Run `${CLAUDE_PLUGIN_ROOT}/bin/flotilla guard status` from the repository root and report every line.
2. For each git hook marked "(the profile asks for it)" and not installed, name it and what it does — `pre-commit`
   refuses a rewrite of a shared file another open row has reserved; `pre-push` refuses a push to trunk or a tag
   without a green push receipt, even from a script — and ask the person, one question per hook. Install only what
   they said yes to: `${CLAUDE_PLUGIN_ROOT}/bin/flotilla guard install --pre-commit` or `--pre-push`.
3. If a hook is "another tool's hook" or `core.hooksPath` is set, show the line the install printed and leave that
   file to the person: never edit another tool's hook yourself.

Say plainly what the guards cannot see (`flotilla guard --help` prints it): a command inside `eval`, `sh -c` or a
script is not read by the command guards; the `pre-push` hook is what catches a push hidden that way.
