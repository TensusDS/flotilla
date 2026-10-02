---
name: spawn
description: Raise flotilla sessions - background Claude Code sessions, each with a post (orchestrator, sender, reviewer, judge, main, minor), a name given at birth and its own home worktree - from a composition such as "-r 1 -M 2" or the profile's default.
disable-model-invocation: true
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/scripts/flotilla spawn*)
---

0. **With no arguments, ask first who leads.** AskUserQuestion, header "Fleet", question "Who leads the fleet?":
   - "This session leads it (Recommended)" - "You talk to the fleet here; the rest starts in the background."
   - "A background orchestrator" - "Every seat starts in the background; you attach to the orchestrator to talk."

   **This session leads it:** run `${CLAUDE_PLUGIN_ROOT}/scripts/flotilla spawn --lead` and show what it prints: the
   orchestrator's name this session will carry. Claude Code shows the name with the person's next message - nobody
   renames anything; only if it says it could not tell which session runs it, tell the person to type
   `/rename <the name>`. Do not raise the rest yet: seats raised now would learn this session's old name. Tell the
   person: "Tell me what to build." On their next message, before anything else, run
   `${CLAUDE_PLUGIN_ROOT}/scripts/flotilla spawn --fill --dry-run`, then steps 2-4 below with
   `flotilla spawn --fill`; if it refuses because the name has not shown yet, say so and run it after the person's
   following message. From then on this session holds the orchestrator post: read `.flotilla/posts/orchestrator.md`
   on trunk and follow it, and use the `flotilla:flotilla` skill for every ledger move.

   **A background orchestrator:** go on with step 1 and `--default`.

1. Show the plan first: run `${CLAUDE_PLUGIN_ROOT}/scripts/flotilla spawn $ARGUMENTS --dry-run` from the repository
   root (with no arguments and a background orchestrator, use `--default`). Show every line it prints: names, trees, branches, the permission
   mode and the warnings.
2. Ask the person whether to raise exactly that fleet. Raising sessions spends money and occupies worktrees; do
   not proceed on your own judgement.
3. On a yes, run the same command without `--dry-run` and show the table it prints: each name with its
   `claude attach <id>` and its tree.
4. A session reported "launched, not yet seen in the census" is alive or starting: never run spawn again for it,
   because two processes would share one name. Tell the person to check `claude agents`.
5. When the profile's permission mode is `ask`, say that each session's permission questions come to the
   orchestrator, which puts them to the person; flotilla's own read commands and ledger moves pass without one. In
   `auto` mode Claude Code's classifier decides each call and no question waits on anyone.
