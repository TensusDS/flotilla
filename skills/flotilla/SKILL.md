---
name: flotilla
description: The arrangement every session in a flotilla fleet follows - who you are, how you start, how work moves through the ledger, whose move it is, and what never to do. Use when your system prompt says you hold a flotilla post, when you start as a flotilla session, or before any flotilla ledger move.
user-invocable: false
---

# Working in a flotilla fleet

Your system prompt names you, your post, your home worktree and the absolute path of the flotilla command line.
Use that path for every command below. Never infer your name or your post from the work.

## When you start

1. Take the census: `flotilla status`. Read deviations and findings first; they are moves that cannot happen now,
   or work git sees and the ledger does not.
2. Announce yourself to the live peers: list them (ListAgents) and send each one line - your name, your post,
   your home worktree.
3. Tell the person, in one short paragraph, what you inherited: rows in your name, broken chains, open findings.
4. Wait for a task. Do not start work nobody gave you.

## State lives in the ledger, not in letters

A letter is a notification of a move, never its carrier. Before you say "done", "handed", "accepted" or
"shipped", the move must be in the ledger, made by you through the command line:

- main and minor: claim by cutting a tree (`flotilla tree cut <branch> --tree <path> --ref <task>`), hand over
  committed and green (`flotilla receipt run --purpose handover`, then `flotilla work hand <branch>`), close what
  shipped (`flotilla work close <branch>`);
- reviewer: `flotilla work take <branch>`, then `flotilla work accept <branch> --reviewed <sha>` or
  `flotilla work fix <branch> --why "<what must change>"`;
- sender: `flotilla brief` for the person's one yes, then `flotilla work queue`, `land`, and `flotilla work
  reconcile` after every push;
- orchestrator: `flotilla work assign <branch> --reader "<session>"` and send the letter it prints;
- judge: `flotilla work walked` or `flotilla work broke` over the deployed build.

Your post's text in the system prompt says which of these are yours; a move your post may not make is refused.

## Whose move it is

`flotilla status` names, for every open row, whose move it is. If it is yours, make it or record why you wait:
`flotilla work wait <branch> --on "<whom>" --why "<why>"`. Falling silent while holding a move is the failure the
fleet is built against.

## Never

- edit the main checkout; work happens in linked worktrees;
- act under another session's name (`--as`); the ledger records who really called;
- read, return or accept your own work;
- type "shipped": the ledger asks the PR or origin;
- take a post nobody gave you, even when it is empty: tell the person one is needed.
