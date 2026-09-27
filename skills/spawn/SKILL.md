---
name: spawn
description: Raise flotilla sessions - background Claude Code sessions, each with a post (orchestrator, sender, reviewer, judge, main, minor), a name given at birth and its own home worktree - from a composition such as "-r 1 -M 2" or the profile's default.
disable-model-invocation: true
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/scripts/flotilla spawn*)
---

1. Show the plan first: run `${CLAUDE_PLUGIN_ROOT}/scripts/flotilla spawn $ARGUMENTS --dry-run` from the repository
   root (with no arguments, use `--default`). Show every line it prints: names, trees, branches, the permission
   mode and the warnings.
2. Ask the person whether to raise exactly that fleet. Raising sessions spends money and occupies worktrees; do
   not proceed on your own judgement.
3. On a yes, run the same command without `--dry-run` and show the table it prints: each name with its
   `claude attach <id>` and its tree.
4. A session reported "launched, not yet seen in the census" is alive or starting: never run spawn again for it,
   because two processes would share one name. Tell the person to check `claude agents`.
5. When the permission mode is `manual`, say that each session stops and waits at every permission prompt until
   someone attaches and answers.
