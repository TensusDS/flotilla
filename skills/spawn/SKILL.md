---
name: spawn
description: Raise flotilla sessions - background Claude Code sessions, each with a post (orchestrator, sender, reviewer, judge, main, minor), a name given at birth and its own home worktree - from a composition such as "-r 1 -M 2" or the profile's default.
disable-model-invocation: true
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/scripts/flotilla spawn*), Bash(${CLAUDE_PLUGIN_ROOT}/scripts/flotilla fleet)
---

0. **With no arguments, find out first whether the fleet has a leader.** Run
   `${CLAUDE_PLUGIN_ROOT}/scripts/flotilla fleet`. If it lists a live orchestrator or a session that leads the fleet,
   do not ask: the person is topping the fleet up - go on with step 1 and `--fill`. Otherwise AskUserQuestion, header
   "Fleet", question "Who leads the fleet?":
   - "This session leads it (Recommended)" - "You talk to the fleet here; the rest starts in the background."
   - "A background orchestrator" - "Every seat starts in the background; you attach to the orchestrator to talk."

   **This session leads it:** run `${CLAUDE_PLUGIN_ROOT}/scripts/flotilla spawn --lead` and show what it prints: the
   orchestrator's name this session will carry. If it refuses because another session already leads, show that
   and stop. Claude Code shows the name with the person's next message - nobody renames anything; only if it says
   it could not tell which session runs it, tell the person to type `/rename <the name>`, and before raising
   anything check that `flotilla fleet` lists this session under that name. Do not raise the rest yet: seats raised
   now would learn this session's old name. Tell the person: "Tell me what to build." On their next message, before
   anything else, raise the rest - `flotilla spawn --fill`, or the composition the person named
   (`flotilla spawn $ARGUMENTS`) if they named one - through steps 1-4 below, dry run first; if it refuses because
   the name has not shown yet, say so and run it after the person's following message. From then on this session
   holds the orchestrator post: read `.flotilla/posts/orchestrator.md` on trunk and follow it, and use the
   `flotilla:flotilla` skill for every ledger move.

   **A background orchestrator:** go on with step 1 and `--default`; if the dry run says nobody leads, the answer is
   already given - plan `flotilla spawn -o 1` after this spawn without asking again.

1. Show the plan first: run `${CLAUDE_PLUGIN_ROOT}/scripts/flotilla spawn $ARGUMENTS --dry-run` from the repository
   root (with no arguments, `--default` or `--fill` as step 0 decided). Show every line it prints: names, trees,
   branches, the permission mode, the warnings and the gaps.
2. **A `gap:` line in the dry run is a question for the person, not a remark** - unless step 0 already answered
   it. The fleet would lack someone, and a person new to flotilla will not know what is missing.
   - "nobody leads this fleet": AskUserQuestion, header "Leader", question "Nobody would lead this fleet. Who
     leads it?", options "This session leads it (Recommended)" - "You talk to the fleet here" -, "A background
     orchestrator" - "Raised with the rest; you attach to talk" -, and "Nobody for now" - "The seats wait for
     work". This session: go to step 0's lead path and raise nothing now - the composition shown here rises once
     the name shows. A background orchestrator: after this spawn, also run `flotilla spawn -o 1` (dry run first).
   - "nobody merges": ask whether to add a sender; on yes, after this spawn also run `flotilla spawn -s 1` (dry run
     first).

   Then ask the person whether to raise exactly that fleet. Raising sessions spends money and occupies worktrees; do
   not proceed on your own judgement. The same `gap:` lines printed again after the launch are already answered.
3. On a yes, run the same command without `--dry-run` and show the table it prints: each name with its
   `claude attach <id>` and its tree.
4. A session reported "launched, not yet seen in the census" is alive or starting: never run spawn again for it,
   because two processes would share one name. Tell the person to check `claude agents`.
5. When the profile's permission mode is `ask`, say that each session's permission questions come to the
   orchestrator, which puts them to the person; flotilla's own read commands and ledger moves pass without one. In
   `auto` mode Claude Code's classifier decides each call and no question waits on anyone.
