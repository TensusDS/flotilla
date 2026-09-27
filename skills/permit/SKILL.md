---
name: permit
description: Put background sessions' permission questions to the person one at a time, oldest first - show the question, ask allow once, allow for the session, or deny, and record the answer so the asking session is told at once.
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/scripts/flotilla permit *)
---

You are the orchestrator, or the person asked you to answer questions. One question at a time, oldest first:

1. Run `${CLAUDE_PLUGIN_ROOT}/scripts/flotilla permit next`. Exit 3 means no question waits: say so and stop.
2. Ask the person with AskUserQuestion. The question names the session and exactly what it asks (the command, the
   file, the URL, as printed). Three options: "Allow once"; "Allow for this session" (its description is the text
   the command printed after "allow for the session"); "Deny". A typed answer ("Other") is the reason for a deny,
   unless it clearly says to allow.
3. Record it: `${CLAUDE_PLUGIN_ROOT}/scripts/flotilla permit answer <id> allow`, `... session`, or
   `... deny --why "<the person's words>"`. A refusal ("withdrawn", "abandoned", "already closed") means nobody waits
   for that answer any more: say so, do not retry.
4. Go back to step 1: a question that arrived meanwhile waits its turn.

If AskUserQuestion is not available to you (a background session), print the question and the three commands so the
person can answer by attaching, and never answer on the person's behalf.

To keep watching, run `${CLAUDE_PLUGIN_ROOT}/scripts/flotilla permit next --wait 3600` in the background; when it
returns, run this routine, then start the wait again.
