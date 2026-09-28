# Field test 2026-09-27: a seven-session fleet builds a console Snake

A live test of flotilla at `302833c` on this machine (Claude Code 2.1.283, Linux). Repository
`/home/max/workspace/snake` with a local bare origin; fleet: orchestrator 1, sender 1, review session 1 and 2,
acceptance judge 4, main session 1, minor session 38; all on Sonnet, permission mode `auto`, direct push, every
branch reviewed, judge required. Findings are appended as they appear and walked through with Max afterwards.

Each finding: what was seen (evidence), why it matters, and a first idea of the fix. Kinds: **bug** (flotilla does
something wrong), **gap** (a path flotilla does not cover), **noise** (true but unhelpful), **setup** (friction
getting a project onto flotilla), **obs** (an observation to keep, not yet a defect).

## Setup (before the fleet started)

F1. **setup — no install path for a local plugin.** The plugin was not installed; `spawn` does not pass
`--plugin-dir`, so spawned sessions would have had no flotilla skills or hooks. A directory marketplace refuses a
symlinked plugin entry ("does not stay inside the marketplace directory"); it worked with a real clone inside the
marketplace, installed with `--scope project`. The README says nothing about installing. Fix: document the install
(a marketplace entry in the repo itself, or `claude plugin marketplace add` of the flotilla repo), and consider
publishing a marketplace manifest in the flotilla repo.

F2. **gap — nothing checks that the plugin is enabled for the project.** `doctor` was green and `spawn --dry-run`
planned seven sessions while the plugin was not installed at all. Fix: `doctor` (and `spawn`) check that
`flotilla@...` is enabled for the project, and refuse to spawn otherwise.

F3. **gap — trust is not checked before spawning** (known from entry 70): `claude --bg` refuses a directory whose
trust was never accepted, and trust is not inherited from a parent. Max had to run `claude` in the repository by
hand. Fix: `doctor`/`spawn` read the trust state and name the one command that fixes it.

F4. **setup — a console project has no judge path in onboarding.** The deploy question appears only when a
deployment is detected, so a CLI project gets no judge and no `deploy.revision_command`; both were written by hand
(`[judge] required = true`, `surface = "cli"`, `revision_command = "git ls-remote origin refs/heads/main"`). Fix:
ask about the judge for every project, and offer "trunk on origin" as the deployed build of a CLI or library.

F5. **setup — no way to pick a model for the fleet in onboarding.** The model question offers "one for all" (inherit)
or "strongest reviewer"; running the fleet on Sonnet meant editing `model:` in all six post files. Fix: let the
answer name a model (`fleet.model = "sonnet"`) that every post without its own `model` inherits.

F6. **noise — a typed tier command is named `custom-1`.** The handover and push tier for
`uv run --with pytest python -m pytest -q` got the name `custom-1`, which every receipt and refusal then prints.
Fix: derive a name from the command (`pytest`), or ask for one.

F7. **obs — names continue across projects.** The minor session is `minor session 38` and the judge
`acceptance judge 4` because ai-os background sessions `minor session 37`, `acceptance judge 1` and `3` are alive on
the machine (the census is machine-wide, and names are machine-wide addresses by design). Correct, but surprising.
Fix: `spawn --dry-run` says why a number was chosen; the onboarding could offer a project prefix in name patterns.

## Running

F8. **bug — a startup false alarm: "mover_gone".** The orchestrator's session-start hook said
"fleet/reviewer-1: mover_gone: the move is review session 1's, and that session is not alive (0 min)". `spawn`
raises the orchestrator before the reviewers, so its hook ran before they existed. Fix: at session start, do not
report post rows of sessions the same spawn is still raising (e.g. a row younger than the spawn, or a
`spawning` marker), or wait for the spawn to finish before the first report.

F9. **noise — the session-start greeting lists peers from other projects.** "12 live peer(s): AI OS reformatting,
acceptance judge 1, …, ai-os-ea, git orchestrator 4, …" — the census is machine-wide, so an orchestrator of this
project is told about sessions of ai-os. Fix: name peers of this project (same repository key or cwd under the
project, or a post match) first, and count the rest in one number.

F10. **obs — `status: waiting` can be momentary.** At 22:42:43 the census showed the orchestrator as
`blocked / waiting`; 30 s later it was `working / busy` three times in a row. A watcher that reports "waiting on a
permission prompt nobody answers" from one sample would raise a false alarm. Fix: report a waiting session only
when two samples some seconds apart agree, or when the state has lasted.

F11. **obs — flotilla's silent hooks leave no trace in the transcript.** Session-start and prompt hooks appear as
`hook_success` attachments; the guard (PreToolUse) and the Stop guard printed nothing and appear nowhere, so
whether they ran cannot be confirmed from the session's record. Not a defect by itself; a way to confirm the guard
is installed and firing (`flotilla guard status` for a session, or a counter in the state directory) would help.

F12. **bug — a producer's post forbids taking work from the orchestrator, and the session hangs asking.**
`minor session 38` received the orchestrator's routed task (claim `feature/curses-frontend`, build the curses front
end) and did not take it: `templates/posts/minor.md:18` says "A peer's message is information, never a work order.
Only the person assigns work." It asked the person with AskUserQuestion — in a background session nobody is
attached to — and has stood at census `blocked / waiting` since 22:43 (two samples at 22:49:21 and 22:49:26 agree).
`main session 1`, given the same kind of message, took its task. Why it matters: the orchestrator's whole post is to
route work; a producer that refuses routing stalls the fleet, and AskUserQuestion in a background session is a
hang with no timeout (the broker does not cover it, and in `auto` mode is not even on). Fix: the minor and main
posts accept a task routed by the orchestrator post (checked against the census and the posts, not the message's
word), and keep "a peer's message is not a work order" for every other peer; the arrangement skill says a
background session never uses AskUserQuestion — it records a wait (`flotilla work wait --on "the person"`) and
tells the orchestrator instead.

F13. **noise — a reader still shows "(reading)" after accepting.** `flotilla work show feature/game-logic` at
22:49, with the row `queued`: "reader review session 1 (reading)". The `taken` flag outlives the verdict, so the
roster and `show` say the reader is still reading work that is accepted and queued. Fix: clear `taken` on accept
and fix, or print "(accepted over 0076c5f)" instead.

F14. **bug — direct push has no working land path for the sender (the known TODO from the spawn review, now
live).** `sender 1` queued `feature/game-logic`, ran a green push receipt, and pushed `0076c5f` to `origin/main`
itself (`git push origin 0076c5f:refs/heads/main`, 22:5x). Then `flotilla work land feature/game-logic --merge
0076c5f` refused: "0076c5f is not on the local `main`; land records work merged there". Local `main` is checked out
in the main checkout, which belongs to nobody; `git fetch origin main:main` was refused by git (checked out
elsewhere) and `git update-ref refs/heads/main …` was denied by the auto-mode classifier as "Git Destructive". The
ledger row stays `queued` while trunk on origin already carries the work. Why it matters: in direct-push mode every
land ends this way, and the ledger lies about what shipped. Fix: in direct mode, `land` accepts a merge that is
reachable from `origin/<trunk>` (or the sender's own integration branch), and `ship` then checks origin as it does
today; the sender's post and skill describe that exact sequence (merge in the home tree on a branch from
`origin/<trunk>`, receipt, `land`, push `HEAD:<trunk>`, `ship`).

F15. **bug — the sender pushed before recording land.** Order in the transcript: queue → receipt → `git push` →
`work land` (refused). The route is queued → land → ship; pushing first makes "shipped" happen with the ledger at
"queued", so the judge and `watch` see work that is on trunk but not landed. Fix: the sender's post states the
order, and `ship` for a row still `queued` in direct mode could record land and ship in one move when the merge is
on origin.

F16. **gap — background producers and the sender ask the person with AskUserQuestion, which hangs.** Both
`minor session 38` (F12) and `sender 1` (F14) ended with AskUserQuestion in a background session nobody is
attached to: census `blocked / waiting` on two samples each (22:49 and 22:52), no timeout, and in `auto` mode the
broker is off. `watch --once` did catch the sender (exit 1, "waiting on a permission prompt nobody answers"), but
not the minor, which holds no ledger move (F-watchers TODO). Why it matters: any post can stall the fleet silently
by asking a question. Fix: a guard on `AskUserQuestion` (PreToolUse, matcher `AskUserQuestion`) for background
sessions whose post is not person-facing: deny with "send the question to the orchestrator (SendMessage) and record
`flotilla work wait --on "the person" --why …`"; the posts say the same; the orchestrator is the one post that
talks to the person.

F17. **gap — a flotilla refusal with no next move is escalated to the person with its internals.** The sender's
question to Max (seen on Remote Control, 22:52): "How to close `flotilla work land` for feature/game-logic —
origin/main is at 0076c5f but local main (checked out at /home/max/workspace/snake) has not moved?", with options
"update the main checkout yourselves" / "allow me git update-ref" / "other". Max's reaction: why does the sender ask
the person about details of the tool's implementation, outside its work? Causes: (1) F14 — the tool leaves no legal
path; (2) the refusal "0076c5f is not on the local `main`; land records work merged there" names the state, not the
next move, so the session improvised git plumbing (`fetch main:main`, `update-ref`), hit the classifier, and asked
the person; (3) no post says where a tool failure goes, so it went to the person. Fix: every flotilla refusal ends
with the next legal move, or says "this is a flotilla defect: tell the orchestrator"; tool failures go to the
orchestrator and into a findings record, never to the person (with F16's guard); F14's fix removes this case.

F18. **bug — "finished_not_handed" fires for a branch with no commits of its own.** At 22:55 `flotilla status`
reported "feature/curses-frontend: finished_not_handed - handover tiers are green over the branch tip, and it is not
handed". The branch was just claimed; its tip is `0076c5f`, the commit of `feature/game-logic`, whose handover
receipt is green — so the check sees "green over the tip" and calls the new work finished. Why it matters: the
orchestrator is told to push an author to hand over work that has not started. Fix: a branch is finished only when
its tip is not its base (it has commits of its own), and the receipt is over that tip.

F19. **noise — `last run` keeps the test runner's colour codes.** `status` printed
"(last run: green: [32m[32m[1m12 passed[0m[32m in 0.02s[0m[0m over 0076c5f …)" — the ANSI escapes of pytest's
summary line are stored in the ledger and printed raw. Fix: strip ANSI escapes when the summary line is recorded
(or run tiers with `NO_COLOR=1` / `--color=no` where known).

F20. **bug — a post-named move is attributed to sessions of other projects.** `watch --once` at 22:58: "feature/
game-logic: acceptance judge 1 holds the move (shipped) and is not working …" and the same for "acceptance judge 3"
— both are ai-os background sessions (cwd /home/max/workspace/ai-os). The move "the judge" is resolved to every live
session whose name matches the judge post's pattern, machine-wide; this project's `acceptance judge 4` was busy and
so not listed. Why it matters: the orchestrator is told to message sessions of another project, and a real dropped
ball of this project's judge could hide behind them. Fix: resolve post-named movers only among sessions of this
project — the ones holding a post row in this ledger (the spawned seats), not every name matching the pattern (the
same filter F9 needs for the greeting).

F21. **gap — the judge walks a row that is a part, and files a fix for what another row delivers.** `feature/
game-logic` (pure logic, no entry point) shipped at 22:55:47; acceptance judge 4 walked it at 22:57:57 and recorded
`broke` at "starting the game: 'python3 -m snake', as README.md's 'Run the game' step documents". The entry point is
the work of `feature/curses-frontend` (minor session 38, handed, being read). The broke filed `fix/feature/game-
logic` for main session 1, who may now build a second front end. Why it matters: rows split by the orchestrator are
parts of one path; walking each part on the person's path produces false breaks and duplicate work. Fix: the judge
walks only rows that deliver a path a person can take — the orchestrator marks which (e.g. `--walk-with <branch>`
or the last row of a chain), or the judge waits until every row that `requires` this one has shipped; the fix row of
a broke names the row expected to deliver the missing path.

F22. **obs — the fix row has no base.** `flotilla work show fix/feature/game-logic`: "base unknown | tip -"; the row
is claimed by the judge's `broke` for main session 1 before any branch exists. Expected, but `show` could say "no
branch yet" instead of "base unknown", which reads as a git failure (compare the ledger-A TODO on unknown bases).

F23. **gap — nothing wakes an idle background session when a move becomes its own.** `feature/curses-frontend` was
accepted by review session 2 before 23:01; at 23:01 and again at 23:04 `sender 1` was `done / idle` with the move
(`accepted -> the sender`), and `orchestrator 1` was idle too. `watch --once` reported it both times ("sender 1 holds
the move (accepted) and is not working … (1 min)", then "(4 min)"), but nobody reads `watch` inside the fleet: the
orchestrator's prompt hook fires only when the orchestrator gets a prompt, and an idle background session gets none.
The reader's accept did not tell the sender either. Why it matters: every handoff between posts can stall until a
person or an outside watcher notices; the spec's "delivered by a hook into the session" does not reach a session
that is not being prompted. Fix: a move that makes another session the mover sends that session a letter (the ledger
already prints letters; deliver them — `claude` has no CLI to message a session, so the move's output tells the mover
to `SendMessage`, and the post says to do it); and the orchestrator keeps a background `flotilla watch --wait` (like
`permit next --wait`) that returns when attention appears, waking it to route.

F24. **bug — the mover cannot record a wait on the row it must move.** At ~23:06 the Stop guard fired on `sender 1`
("you hold 1 move(s) … feature/curses-frontend: queued; your moves: land, offledger, release, wait …"). The sender
ran `flotilla work wait feature/curses-frontend --on "max" --why …` and got "refused: only the owner (minor session
38) or the reader of `feature/curses-frontend` records a wait on it". It then recorded the wait on its own post row
`fleet/sender-1` — a workaround the ledger accepts but that does not stand on the row that waits — and stopped; the
second stop was recorded as a break (`watch`: "sender 1 stopped twice while holding this move (queued)"). Why it
matters: the Stop guard offers `wait` as a legal move and the ledger refuses it to exactly the session the guard
addresses; a post-named mover (the sender, the judge) can never record its wait. Fix: `wait` is allowed to whoever
`who_moves` names for the row (including the sessions of a post-named mover), besides the owner and the reader.

F25. **obs — F14 repeats on every land, and the Stop guard works.** The second row (`feature/curses-frontend`,
`688215c`) went the same way as the first: receipt green → push to origin → `land` impossible (local `main` behind,
in the main checkout) → a request to the person. This time the sender did not use AskUserQuestion; it wrote the
request as text and tried to record a wait (F24). The Stop guard blocked the first stop with the legal moves and let
the second through, recording the break — as designed (decisions 51–53).

## Outcome (read 2026-09-28 20:59, after ~21 h offline)

The fleet finished the task on 2026-09-27 by 23:43: trunk `688215c` holds `snake/game.py` (logic: wrap-around,
100 points per apple, growth, self-collision), `snake/cli.py` (curses front end, "Game over — score"), `__main__.py`;
12 tests green over `688215c` in a fresh clone. Rows: `feature/curses-frontend` land/ship 23:36, walked 23:38, closed
23:39; `feature/game-logic` walked 23:42 (after its earlier `broke`), closed 23:43. The main checkout was
fast-forwarded to `688215c` at 23:35:43 (`pull --ff-only`, not by the watcher).

F26. **gap — a fix row that the fix did not need is closed as `offledger`.** `fix/feature/game-logic` (filed by
the judge's `broke`, F21) waited on `feature/curses-frontend`, then was handed at 23:40 with the tip `688215c` — the
front end's commit, no commit of its own — accepted by review session 1 in 32 s, and closed by sender 1 with
`offledger` at 23:42. `offledger` means "work reached trunk without the ledger seeing it"; here nothing of the fix
row reached trunk at all — the missing path was delivered by another, recorded row. Why it matters: the ledger's
history now claims work that did not happen, and metrics count a delivery that never was. Fix: a move for this case
— `release --settled-by <row>` (the fix row closes naming the row that delivered what the broke asked for), refused
unless that row is delivered; and `hand` refuses a tip with no commits of its own (compare F18).

F27. **gap — the fleet disappeared without `retire`, leaving six orphaned post rows.** At 20:59 on 2026-09-28 six
of the seven sessions were no longer in the census (only acceptance judge 4 remains); the machine did not reboot
(uptime 7 days). Who stopped them is unknown to the watcher. Their post rows stay `reserved`, the roster calls them
"orphaned", `watch --once` reports six `mover_gone` items, and their worktrees stay locked. Why it matters: a
finished fleet has no one-step way to stand down, and a stopped fleet looks like an alarm for ever. Fix: `flotilla
fleet down` (retire every post session of this project: stop it, free its post row, unlock its tree, report
uncommitted work) and, in `watch`, a post row of a session that is gone is one "stale seat" line, not a mover alarm.

## Triage with Max (2026-09-28)

Grouped by what stalled the fleet most; fixed in this order, in several small plans.

1. **The sender in direct-push mode** — F14, F15, F24, F25. Decided: `land` accepts a merge that is reachable from
   `origin/<trunk>`; the local trunk in the main checkout is not needed, and nobody moves the main checkout. `wait`
   is allowed to the row's mover.
2. **Who talks to the person** — F12, F16, F17. Decided: **only the orchestrator** talks to the person; the sender
   asks through the orchestrator too, including its yes per batch. A guard on AskUserQuestion refuses every other
   background post with the route to follow; posts accept tasks routed by the orchestrator (checked against the
   census and the posts); every flotilla refusal names the next legal move.
3. **Waking whoever the move passes to** — F23: a move that makes another session the mover prints the letter to
   send it and the post obliges sending it; the orchestrator keeps a background `flotilla watch --wait`.
4. **The judge and work split into parts** — F21, F26. Decided: the judge walks a row only once every row that
   `requires` it has shipped (the orchestrator may override explicitly); a fix row made unnecessary closes with
   `release --settled-by <row>`; `hand` refuses a tip with no commits of its own.
5. **Noise and false alarms in the views** — F8, F9, F10, F13, F18, F19, F20, F22: live sessions of this project are
   the holders of this ledger's post rows, not every name matching a pattern.
6. **Fleet lifecycle** — F27: `flotilla fleet down`; a gone seat is one quiet line, not a mover alarm.
7. **Setup** — F1–F7, F11.

Part 1 (the ledger) fixes F14, F15, F18, F21, F24, F26 — plan `docs/plans/2026-09-28-field-fixes-ledger.md`.

Left open by the part-1 branch review (minor, for a later plan):

- **R1 — the design spec lags the decisions log.** Sections 6.2–6.4 and 7.4 still describe `land` on the local
  trunk only and say nothing about the part/path rule; decisions 81–88 carry them. Fold them in with the next spec
  pass.
- **R2 — `walkable` cannot be revoked.** Once the orchestrator marks a part walkable it stays so; there is no move
  to take it back.
- **R3 — `reconcile` is silent about a row pushed but never landed.** It skips direct-mode `queued` rows, so a row
  whose revision is already on origin shows nothing; it should say so and name `land`.
