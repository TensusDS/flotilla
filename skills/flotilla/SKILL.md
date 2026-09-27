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

- main and minor: take each task in your home tree (`flotilla tree switch <branch> --ref <task>` moves it to a new
  branch from trunk and files the claim; the same command switches back to a branch returned to you), hand over
  committed and green (`flotilla receipt run --purpose handover --tree <home tree>`, then
  `flotilla work hand <branch>`), close what shipped (`flotilla work close <branch>`);
- reviewer: `flotilla work take <branch>`, read the handed tip in your home tree (`git switch --detach <tip>`), then `flotilla work accept <branch> --reviewed <sha>` or
  `flotilla work fix <branch> --why "<what must change>"`;
- sender: `flotilla brief` for the person's one yes, then `flotilla work queue`, `land`, and `flotilla work
  reconcile` after every push;
- orchestrator: `flotilla work assign <branch> --reader "<session>"` and send the letter it prints;
- judge: `flotilla work walked` or `flotilla work broke` over the deployed build.

Your post's text in the system prompt says which of these are yours; a move your post may not make is refused.

## Long runs go through the lane

A full suite, a browser run or anything that takes the machine goes through the lane:
`flotilla lane run --for <branch> -- <command>` waits its turn, runs the command, releases the lane and records
the result on the row, where `flotilla status` shows it. Receipts take the lane themselves. A run killed by a
signal is recorded as killed, with no verdict: never report it as passed, and never read a run's success from the
exit code of a pipeline. The lane is not a lock: a run started without it is seen only as an unbooked run, and
nobody waits for its result.

## Whose move it is

`flotilla status` names, for every open row, whose move it is. If it is yours, make it or record why you wait:
`flotilla work wait <branch> --on "<whom>" --why "<why>"`. Falling silent while holding a move is the failure the
fleet is built against.

## When a hook speaks

A line headed "flotilla - your move" names a move that is yours. Make it, or record whom you wait on:
`flotilla work wait <branch> --on "<whom>" --why "<why>"`. A wait is a move too: it puts the ball, visibly, with
someone else. The Stop guard blocks a background session that tries to stop while it holds a move with nothing in
flight and no wait recorded; answer it the same way, never by stopping again. If you are the orchestrator, a line
headed "flotilla - the fleet" names dropped balls: message the session it names.

## Never

- edit anything outside your home tree: it is the one directory you were given, and the main checkout belongs
  to nobody;
- act under another session's name (`--as`); the ledger records who really called;
- read, return or accept your own work;
- type "shipped": the ledger asks the PR or origin;
- take a post nobody gave you, even when it is empty: tell the person one is needed.
