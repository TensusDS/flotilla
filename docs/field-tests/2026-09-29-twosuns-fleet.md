# Field test 3: a fleet builds a three.js walk under two suns (2026-09-29)

The third live run, after part 5 (`e9bb417`). A much larger task than the snake or 2048: a browser walking simulator
in three.js on a procedural 8 × 8 km alien world with two suns, Rayleigh and Mie scattering by star type, weather,
bloom, heat haze and sound (the task is the repository's README). It is meant to make the fleet split the work
into many rows that depend on each other, and the judge walk a web build in a browser for the first time.

Same shape as before: a local repository with a README task, a bare origin on the same machine, a fleet of seven in
`auto` mode — orchestrator, acceptance judge, two reviewers, main, minor, sender — all on Sonnet except **main on
Opus** (the post's own `model`), the task given to the orchestrator by the person, the fleet watched every three
minutes from outside.

Repository `/home/user/workspace/twosuns`, origin `/home/user/workspace/twosuns-origin.git`; flotilla installed from
GitHub at `e9bb417`.

## Setup

- **T1 — install by the README took the new revision** (`e9bb417`), after `claude plugin marketplace update
  flotilla`. The onboarding took the same answers as 2048 with surface `web`; the profile came out with
  `fleet.model = "sonnet"`, a required judge, trunk on origin as the deployed build, and the tier named `npm` (from
  `npm test`).
- **T2 — bug: every project shares one plugin copy, because the version never moved.** `plugin.json` still says
  `0.1.0` after five parts of fixes, so Claude Code keeps one cache directory `cache/flotilla/flotilla/0.1.0` for all
  of them: installing `e9bb417` into this project replaced the code game2048 runs, while
  `installed_plugins.json` still records game2048 at `3e7e903`. A project cannot stay on the revision it was set up
  with, and the record lies about what runs. Fix: bump `version` in `plugin.json` with every release (and check in CI
  that a changed plugin carries a new version).
- **T3 — a post's own model reaches the launch line.** `spawn --dry-run` shows `--model opus` for main session 3 and
  `--model sonnet` for the other six (`fleet.model`), with `model: opus` set in `.flotilla/posts/main.md` by hand.
- **T4 — the judge's browser.** No system Chrome on the VPS; a Chromium is in the Playwright cache
  (`chromium-1234`), and the user-scope `chrome-devtools-mcp` plugin is enabled. The README tells the judge to walk
  the build with Playwright in headless Chromium (software WebGL). Whether the background judge manages it is the
  first thing to watch.

## Running

H1. **obs — the task reached the orchestrator at 20:06:10, and it started the stages in order.** At 20:08:16 both
main sessions got a message (prompt hooks); by 20:10 main session 3 (Opus) had claimed `feat/stage-1-terrain` and
was working, while main session 4 (Opus) stopped idle with no row — the README's stages build on each other, and
the orchestrator held the second main back rather than open a later stage on nothing. Whether stage 2 is claimed
with `--requires feat/stage-1-terrain` (G3) is to watch. Also seen: an unnamed session `6e69229e` among this
project's sessions with only a session-start trace — the person's own `claude` run to accept the trust dialog.

H2. **obs — staged in series, the fleet leaves most of itself idle.** From 20:10 to at least 20:31 one row was open:
stage 1 with main session 3; main session 4 (Opus), the minor, both reviewers, the sender and the judge stood idle
(main session 4 got one message at 20:08:16 and stopped). The orchestrator read the README's "later stages build on
earlier ones" as a strict chain 1 → 2 → 3 → 4 → 5. The cause is the task's wording (ours), not flotilla; but most of
the later stages have parts that do not need stage 1 — the suns' orbits, the star spectra and the scattering (pure,
tested functions), procedural sound, the seeded placement of ruins — and only their wiring into the scene does. An
orchestrator that splits work should split along what really depends, and the dependent part is what `--requires`
is for. At ~20:32 the observer suggested to the person that they ask the orchestrator to parallelise.

H3. **obs — parallel rows, each with its dependency declared (G3 did not recur).** Between 20:38:48 and 20:39:45,
after the person's prompt at 20:34:41, the orchestrator routed three more rows at once: `feat/stage-2-two-suns`
(main session 4), `feat/stage-4-ruins` (main session 3, next to its handed stage 1) and `feat/stage-5-sound` (minor
session 40). All three are claimed with `requires` on `feat/stage-1-terrain` — the roster calls each author "blocked
on feat/stage-1-terrain", so `queue` will hold them until stage 1 is delivered. Stage 1 was handed (tip 2d5d293, two
commits) and review session 5 took it at ~20:39 (letter delivered; F23 did not recur). The three new branches were
started at stage 1's tip 2d5d293 (stacked; main session 3 pushed `feat/stage-1-terrain` to origin for them).

H4. **bug — a branch stacked on another open row reads as finished before it has any work.** At 20:40 `status` listed
`finished_not_handed` for all three new rows: "handover tiers are green over the branch tip, and it is not handed".
Their tip is stage 1's tip 2d5d293, whose handover receipt is green, and `has_own_commits` asks only whether the tip
is on trunk — it is not, because it carries stage 1's commits. So a row with no commit of its own is called finished
and the author is told to hand it over. The same false signal reaches `mine()` / the Stop guard as a "working"
row. Fix: own commits are the commits past the row's base **and** past the tips of the rows it requires (or: past
the merge-base with each required branch), not merely off trunk.

H5. **bug — a fresh branch at trunk's tip is reported as unread work in trunk, with `offledger` as the fix.** Stage
1 was accepted (review session 5, 20:41:48), queued, landed and shipped by the sender at 20:42:02–24 (merge 3716e99;
direct push from its own tree again — F14/F15 did not recur). The stage 2 and stage 4 authors then moved their
branches to the new trunk, 3716e99, before any commit of their own. At 20:43 `status` listed under findings, for
both: "unread_in_trunk - row r11 is claimed, and its tip 3716e99 is already in `origin/main`: it reached trunk
without a verdict; record it with offledger and a witness". Nothing of theirs reached trunk; the advice would record
a delivery that never was (the F26 shape, in `findings` this time). `hand` already refuses such a tip (decision 84)
and `_finished` no longer fires for it; the finding was not taught the same rule. Fix: `unread_in_trunk` skips a
row whose tip has no commits of its own (`handover.has_own_commits` false) — as with H4, "own" should mean past the
base and past what the row requires.

H6. **obs — the orchestrator used the path override on purpose.** At 20:42:34, right after stage 1 shipped, it
marked the row `walkable`: three open rows require stage 1, so the judge would otherwise wait for all of them
(decision 86), but stage 1 alone is a playable walk. The judge went busy at 20:43. Also: the minor handed stage 5
(sound) within five minutes of claiming it and opened `fix/streamer-alloc` (base 3716e99) on its own — whose
request that was is not visible from outside.

H7. **obs — G8 recurred: a session named after a seat's tree appears among this project's sessions.** At 20:46
`guard status` listed `twosuns-main-3-11`, with no hook ever fired, next to the eight seats; the census counted 21
live sessions (20 a tick before). The name is Claude Code's default for a session started in a directory
(`<dir>-<n>`, like the person's own `game2048-57`), here main session 3's tree; the census lists it as
`interactive / busy`, cwd `/home/user/workspace/twosuns-main-3`, with no short id. Who started it is not visible from
outside — a person opening `claude` in that tree, or a seat running Claude there. flotilla counts it as this
project's session (its cwd is in a worktree) though it holds no post; harmless so far, but a session no seat owns
is invisible to the ledger.
  *Update 20:48:* the person did not start it, so a seat did — most likely main session 3, in whose tree it runs.
  That session is outside the fleet: no post, no row, no guard that knows it (the ask guard lets a session with no
  post ask the person freely, and nobody is attached to it). Worth a rule: a seat that starts another Claude session
  says so to the orchestrator, or flotilla reports sessions in a seat's tree that hold no post.

H8. **design (the person, 20:49) — let seats raise short-lived helpers, inside the fleet's rules.** Following H7:
a seat that starts another Claude session is a good initiative, but it must not happen outside the ledger. The
person's direction: allow it, on the condition that a helper follows the same rules as the fleet and ends its work
properly instead of vanishing. A sketch to design against:
- **a helper is raised by flotilla, not by a bare `claude`** — `flotilla helper start --purpose "<what>"` from a
  seat: named after its parent (`main session 3 helper 1`), launched with the arrangement and a helper post, and
  recorded in the ledger as a row the parent owns;
- **it works in its own worktree** on a branch cut from the parent's branch (never in the parent's tree — two
  writers in one checkout), and the parent takes its result by merging it;
- **it makes no ledger move of its own and never asks the person** (the ask guard treats the helper post as not
  person-facing); its questions and its result go to its parent;
- **it ends on the record**: `flotilla helper done --result "<what came of it>"` stops it and closes its row; the
  Stop guard blocks a helper that stops without it, and `watch` reports a helper alive past its purpose;
- **a session in a seat's tree that is neither a seat nor a registered helper is a finding** in `status`, so the
  next H7 is seen as it happens.
  *Decided by the person, 20:51:* a helper works in its own worktree, never in its parent's tree — otherwise the two
  race on the same files.

H9. **root cause — Claude Code retires idle background sessions, and under low memory within minutes.** Between the
20:49 and 20:52 ticks four seats left the census at once: sender 3, review sessions 5 and 6, minor session 40 — the
four that stood idle. The Claude Code daemon log (`~/.claude/daemon.log`) says why:
`20:52:07 [bg] bg retire 9476bca6: idle-prompt, idle 4m [low memory]` (and the same for f47b6413, 86904401,
a786dcf8, idle 5–6 m), then `bg settled … (done)`. The machine was at load 18 with 3.5 of 4 GB swap used (eight
seats, vite and a headless Chromium for the judge, and some twenty live sessions in all). The busy seats (both mains,
the judge, the orchestrator) were kept.
The same log explains the snake fleet's disappearance (**F27**, cause unknown until now): `bg retire …: idle-prompt,
idle 8h` — without memory pressure the daemon retires a background session idle for eight hours.
What flotilla did: the views reported it as designed — `seat_empty` for the four seats and one quiet `watch` line
"4 post seat(s) with no live session …" (G/F27 fixes hold) — and `minor session 40` shows `orphaned`. What it cannot
do: the fleet now has no sender and no reader; the stages still in progress will be handed to nobody. Fix to design:
a seat is a process Claude Code may retire, so flotilla must (a) say so plainly — `watch` names a seat retired by
the daemon, not only "not alive", reading the reason if it can; (b) give the orchestrator a way back —
`flotilla spawn --reseat <name>` (or `claude respawn <id>`, to measure) raising the same post again, its tree and
post row kept; (c) keep idle seats cheap, or warn at spawn when the machine's free memory cannot hold the fleet.

H10. **obs — the orchestrator noticed the empty seats and went to the person.** At 20:55 the census showed
`orchestrator 3` as `blocked / waiting`, the state of a question put to the person, three minutes after the daemon
retired four seats (H9) and `watch` raised the one quiet line about them. The orchestrator is the post that talks to
the person, so that is the route; what it asked is not visible from outside.

H11. **obs — the orchestrator re-seated the fleet itself, on the person's yes.** It asked at 20:52:48 whether to raise
a sender, two reviewers and a minor; after the person's answer it retired the four dead seats (`release … called by
retire by orchestrator 3`, 20:57:24 — the post rows freed, trees kept) and spawned `sender 4`, `review session 7`,
`review session 8` and `minor session 41` (alive by 20:58). The minor's shipped rows left the roster with its seat.
So the orchestrator can recover a fleet the daemon thinned — with `spawn`, which the plugin offers the person as a
command (`/flotilla:spawn`, not model-invoked) but which the CLI lets any seat run. That the orchestrator asked
first is what makes it acceptable; the posts should say it, and H9's (b) should give it one move for it.

H12. **design (the person, 20:58) — the orchestrator closes the task, and the fleet with it.** Some of the sessions
still alive on the machine belong to fleets long finished (the snake's `acceptance judge 4`, seats of the other-project
fleets), each holding memory — and memory pressure is what retired this fleet's seats (H9). The person's direction:
when the task is done, the orchestrator asks the person to check the work; if it is accepted, it offers to close
every session of the fleet (`fleet down`), and it lists the sessions that will stay alive if they are not closed —
including seats of past fleets on this machine, not only this project's. Pieces to design: a closing step in the
orchestrator's post; `flotilla fleet --all` (post rows of every ledger in the state directory whose session is
alive, with their project and idle time); and `fleet down` naming what it leaves behind.

H13. **H9 recurred within ten minutes of the re-seat, and took the judge.** At 21:04:01 the daemon retired
`acceptance judge 6` (idle 1 m), `sender 4`, `review session 7` and `review session 8` (idle 6–7 m), all
"[low memory]" — after the person had stopped nine stale background sessions of past fleets at 21:00 (swap went from
3.5 to 2.9 GB used, then back to 3.9 GB of 4 by 21:10). The pressure is the fleet's own now: two Opus sessions, and
main session 4 running a headless Chromium (275 + 171 + 93 MB) and a node script from its job directory
(`~/.claude/jobs/cf32b0ad/tmp`) to look at its own stage. Before it went, the judge walked stage 1 (21:01:43) and
stage 5 (21:02:23), and both rows were closed (by main session 3, and by minor session 41 — the orchestrator had
adopted the retired minor's rows to it at 20:57:32, so orphaned work found a live owner). A daemon threshold of one
idle minute means a seat that waits for work at all is at risk on a loaded machine: flotilla's fleet sizing should
count memory, and an idle seat should cost little (H9 (c)).

H14. **obs — under full swap the census itself stops answering, and flotilla says so instead of guessing.** At 21:13
swap stood at 4095 of 4095 MB (the person names the cause: a heavy session of another project on the same machine).
`claude agents --json` did not answer within `watch`'s 3 s nor `guard status`'s 30 s: `watch --once` exited 2 with
"census: could not be asked", and `guard status` printed "the census could not be asked … no session listed" — never
"attention: none". Meanwhile the orchestrator had raised two more readers, `review session 9` and `review session
10` (reading stage 4 and the minor's sound fix), and main session 3 had handed stage 4 and claimed
`feat/stage-3-post`. The daemon retired another session at 21:14:11 (`9022e6c8`, idle 2 m, low memory).

H15. **obs — the fleet keeps working through churn: the orchestrator re-seats on its own.** Nine sessions were
retired "[low memory]" between 21:00 and 21:16 (review sessions 9 and 10 right after accepting stage 4 and the sound
fix; minor session 41 once its fix was accepted). By 21:16 the orchestrator had raised `sender 6`, `acceptance judge
8` and `minor session 42` — seat numbers climb with every wave (sender 3→4→…→6, judge 6→…→8, reviewers 5–6 → 7–8 →
9–10, minor 40→41→42). It asked the person once (20:52:48, H10); the later re-seats went without a new question
(its `ask` trace still stands at 20:52:48) — on the strength of the first yes. Work did not stall: stage 4 and the
sound fix are accepted and wait for the new sender. Cost: each re-seat is a spawn and a new session in memory, which
feeds the pressure that retires the next idle seat; and a re-raised seat has a new name, so letters and holds naming
the old one go nowhere. Fixes to design with H9: re-seat the same name (`--reseat`), and a standing rule for how
far one "yes" to re-seat reaches.

H16. **gap — a judge walking a large 3D world in a software-rendered browser cannot reach what it must check.** The
judge's wait on stage 4 (`feat/stage-4-ruins`, shipped), recorded on the orchestrator at ~21:30: on 401951b, in
headless SwiftShader at ~7 fps, it walked only as far as a spire half-way, the path ended at a dune wall, and it saw
no ruins up close, no footprints, no beacon — "a way to reach the ruins is needed (a start position or
coordinates), otherwise the walk is not along the path". The wait route is right (the orchestrator, not the person;
F16/F17 did not recur). What it shows: for a web 3D product the judge's "walk the human path" needs the product to
offer test hooks — a teleport or start position by URL (`?pos=…`, the seed is already by URL), a time-of-day
override — and the task should ask for them. It is a gap in the task and the judge post, not in the ledger; worth a
line in the judge's post: "when the path is too long to walk in the test browser, ask for a way to start near it;
walking a different path is not the path". Also seen: stage 2 handed (review session 11 reading it next to the
weather core), stage 3 post-processing shipped, `review session 12` raised for the minor's fix, and main session 4
claimed `feat/stage-3-weather-post` requiring stage 2.

H17. **gap — a stage split into a core and its wiring reaches the judge as a walkable row, because the split was
not recorded as a dependency.** Stage 3 was split into `feat/stage-3-weather-core` (r29, main session 3: "weather as
pure functions of seed, time and place") and `feat/stage-3-weather-post` (r31, main session 4, which wires it in).
r31 was claimed at 21:30:16 requiring stage 2 only; r29 shipped at 21:31:51 (fa74def). The part gate from part 5
(`_whole_or_walkable`, `pending_dependents`) reads `requires`, so nothing held r29 back: `whose move` gave it to the
judge. At 21:36:16 acceptance judge 8 walked fa74def, saw clear sky at every hour, and recorded a wait on
orchestrator 3: "r29 looks like a core with no hookup to rendering; if it is walkable on its own, tell me how to call
the weather (`?weather=`/`__perf`), otherwise I walk when the row that wires it ships". The judge's route was right
(a wait on the orchestrator, not a walk of a different path, not the person; F16/F17 did not recur). The cost was
one judge walk in a software-rendered browser (minutes) spent finding out by looking what the ledger could have
said. The earlier `feat/stage-3-post` (b0562ed, "not wired yet") shipped the same way. Fix direction: when a row is
claimed on a ref shared with an open or recently shipped row (here both carry `stage-3`/`stage-3w`), or a commit
title says "not wired", the claim or the orchestrator's assignment should ask for `--requires` or `walkable`; and
the orchestrator post should say that a split stage records its edge at claim time.

H18. **obs — the reviewer's injections found three guards that did not guard.** Review session 11 returned r29 at
21:27:22 with "154 tests + tsc green, but three injected regressions stay green": the storm slot window narrowed to
one slot, the seed dropped from the storm schedule, and the storm-drags-clouds term removed — each with the test it
needs (tens of slots, several seeds, map corners). Eight other injections went red on the right test. Main session 3
fixed and re-handed at 21:30:33; accepted at 21:31:24. This is the review discipline working as written, on a
domain (procedural weather) far from the one the plugin was built on.

H19. **bug — the one session that books the lane is the one that waits; the runs that skip it go first.** At 21:40
the lane is held by nobody, and sender 6 has waited in its queue since 21:32:33 (b40, the push receipt for
`fix/tests-and-startup`, r27 queued), eight minutes. What holds it is the `foreign run` answer: two unbooked
`vitest run` processes computing, pid 3199857 in `twosuns-reviewer-11` (elapsed 08:22, the reviewer's injection runs
on stage 2) and pid 3225293 in `twosuns-main-3` (main session 3 on `feat/controls-settings`). Neither went through
`flotilla lane run`, although the skill says a full suite does. Swap 3961/4095, 970 MB free. The effect is an
inversion: the sender, which is the only session in the fleet obeying the lane, is the only one kept off the machine,
and the implementer and reviewer who skipped it keep starting new runs in front of it; with two long-running peers
the queue can wait indefinitely, and `whose move` still says "the sender" with the sender's own reason. No earlier
F or G finding covers the lane (it was not exercised under load in the snake or 2048 fleets). Fix directions: the
guard that already watches Bash commands could route a known long-run command (the project's tier commands, e.g.
`npm test`) through the lane or refuse it with the `lane run` form; and when a waiter has been kept out by unbooked
runs past a threshold, `watch` should name the unbooked runs and their trees as an attention item for the
orchestrator.
Resolution, from the lane journal (`~/.local/state/flotilla/lane/lane.jsonl`): b40 went `waiting` 21:32:33 → `held`
21:42:47 → `released` 21:43:07. The sender waited 10 min 14 s for a run that took 20 s; it got in through a gap
between two of the reviewer's runs (the next one, pid 3233485 in `twosuns-reviewer-11`, was already computing at
21:43:08). The ratio, 30 to 1, is the cost of H19 on one push.
Recurred at 21:46:06, wider: three sessions queue behind the reviewer's unbooked runs while the lane is held by
nobody — b41 sender 6 (push receipt, since 21:43:44; r27 is still `queued`, so the b40 receipt did not carry the
push through), b42 main session 3 (handover receipt, since 21:43:55), b43 main session 4 (handover receipt, since
21:46:02). The unbooked run is a fresh pid again (3249118 in `twosuns-reviewer-11`): the reviewer's injection loop
starts one `vitest run` after another, and every gap between them is too short for the queue to be served. Three
booked runs of seconds each are held by one unbooked session.

H20. **bug — `land` refuses a conflict-resolving merge that the reviewer read, and the refusal comes after the push,
so the row is stranded while its work is already in trunk.** Stage 2 (r11): accepted by review session 11 at
21:53:40 over 93c4369 (range from base 1a41b1c), queued by sender 6 at 21:53:53. The sender merged and pushed:
origin/main is dbe9cf6 "merge: feat/stage-2-two-suns" (21:53:53). Then `land` was refused, and the sender recorded a
wait on orchestrator 3 at 21:54:26: "land refused: batch carries unread merge aaaf5e2 (origin/main into branch, by
owner). Already pushed: origin/main=dbe9cf6. Need: reader to read aaaf5e2 or a way to record it". At 21:55 the row
is `queued` while its work is in trunk; the judge cannot be given it and `whose move` sends it to the orchestrator.
Cause, verified: aaaf5e2 (21:21:51, main session 4 merging origin/main 401951b into the branch before handing it)
had conflicts (`git merge-tree --write-tree aacdf3a 401951b` exits 1), so its tree was resolved by hand. It lies inside
the read range (`git rev-list 93c4369 ^1a41b1c` lists it). In `batch.Accounting.account` a merge commit is judged
only by `clean_merge`, and returns None when that is not True, before the `self.read` lookup that covers ordinary
commits in a reviewed range. A merge the reviewer read in that range should count as read: the hand-resolved
conflict is exactly what the reviewer saw in the diff of the range. Second half: in direct-push mode the sender
pushes before `land` (as fixed for F14/F15, which did not recur), so the unread-work gate cannot stop a push; a
refusal can only leave the ledger behind trunk. Either the accounting runs before the push (a `land --check` on the
local merge), or a refusal after the push is recorded as a deviation with its own repair path, not a wait on the
orchestrator.

H21. **bug — the designed repair move for batch work, `inbatch --read-by`, is denied by Claude Code's auto-mode
classifier as "Self-Approval", and the whole delivery line stops on the person.** At 21:58:06 the census shows
orchestrator 3 and sender 6 `state=blocked`, and `watch` exits 1 with three rows waiting on the person:
- r11 stage 2 (H20): "inbatch of aaaf5e2 (read by review 11) blocked by auto-mode classifier as Self-Approval;
  already pushed as dbe9cf6, land pending";
- r32 `feat/controls-settings` (accepted by review session 12 at 21:54:05, queued 21:54:35): the sender's own batch
  merge b7513e3 "merge: feat/controls-settings" onto dbe9cf6 had conflicts (`git merge-tree` exits 1; both touch
  `main.ts`), so it is work born in the batch; the sender's `inbatch … --read-by review 12` was denied the same way;
- r33 `fix/resize-during-load` held behind r32 by the sender's choice ("touches main.ts too; goes after r32 to avoid
  a second unread conflict"), and r31 stage 3 weather-post accepted with the sender idle.
Two different cases share one symptom. For r11 the merge was read (H20 is the flotilla bug, and `inbatch` was a
workaround the ledger should not have needed). For r32 the ledger is right that the hand-resolved conflict is unread,
but the move that records it is made by the sender in the reader's name: `outside.inbatch` requires
`--read-by <someone else>` and is a sender move. To the classifier this reads as one agent attesting its own merge
through another's name, and it has a point: nothing in the move proves review 12 read b7513e3, which was created
after its verdict. Old findings: the F14 shape recurred (work on origin, row `queued`, an improvised repair hit the
classifier — then "Git Destructive", now "Self-Approval"); F15 did not (the order push-then-land is now the design).
F17 recurred in part: a refusal with no reachable next move went to the person, though this time through the
orchestrator, as the design wants. Fix direction: batch work is recorded by its reader — the reader reads the batch
merge and makes the move itself (`flotilla work read-batch <branch> <commit>`), the sender only asks for it; together
with the H20 fix and a pre-push `land --check`, the conflict is found before the push, when a reader can still be
asked.

H22. **H9/H13 recurred: a second retire wave took five seats while the delivery line stood on the person; and a
2 GB browser run went past the lane unseen.** `~/.claude/daemon.log`: at 21:59:02 `bg retire` 1ead45d4 (review
session 11, idle 3m), 7b2e16d3 (minor session 42, idle 8m), 12d3e69b (acceptance judge 8, idle 10m) and a stale
spare; at 22:01:08–11 473d6d3d (review session 12, idle 3m) and db599d54 (review session 13, idle 3m) — every one
`[low memory]`. At 22:01:18 the census has four of the fleet's nine (main 3, main 4, orchestrator 3 `blocked`,
sender 6); `status` shows five `seat_empty` deviations and `watch` names them with the `fleet down` hint (the part-5
SEATS item works). Memory: 351 MB free, swap 4095/4095. The largest consumers were not the fleet: ~6 GB of Gradle
and Kotlin JVMs from another project on the machine (the person's), then a 2 GB `chrome-headless-shell` started at
~21:56 by `node audit.mjs` under `timeout 400`, whose parent is main session 3 (cwd
`~/.claude/jobs/7a4405bd/tmp/pw`) — a browser run, which the skill routes through the lane. The lane showed it as
nothing: `foreign run: ok - no unbooked run`. So the lane's foreign-run detector sees test runners in the
project's trees and misses a browser run started from a session's job directory: the H19 gap is wider than
unbooked vitest. The sessions retired were exactly the ones idle because the line was stopped (H21), so the stall
and the retire wave compound: when the person answers, the orchestrator will first have to re-seat readers.

H23. **obs — after H20/H21 the sender began asking before the push, by hand.** r36 `feat/bench-harness` (main
session 4) was queued at ~22:12; the sender did not merge and push it but recorded a wait on orchestrator 3: "branch
history has author merges e38461c and 6f961ef; asked whether they were read before I push". This is the
`land --check` before the push proposed under H20, done by the session itself after one burn, and it confirms the
direction: the unread-merge question belongs before the push. It also shows the cost of not having it in the tool:
the check is a question to the orchestrator, which is itself blocked on the person (H21, 16 min at 22:13), so a fifth
row joins the stall. Meanwhile `feat/sound-weather` (r35) shipped cleanly at ~22:09 (5904e24, no author merges) and
waits on a judge seat that is empty since H22 (`watch`: "no live session holds the `judge` post").

H17 recurred at ~22:15 with a fresh judge. The orchestrator raised acceptance judge 9 (seat `fleet/judge-9`); its
first move was a wait on r35 `feat/sound-weather` (shipped 5904e24): "r35 is pure logic of the weather's sound
with no hookup to main.ts; no audible effect in the game until r31 lands. I walk it together with r31." Same shape
as r29: a part shipped without `--requires` naming the row that wires it (r31, still `accepted` behind the H21
stall), so the part gate did not hold it and the judge found out by looking. Two of the stage-3 rows now wait on
r31, which waits on r32, which waits on the person.

H24. **bug — in direct-push mode the repair move for batch work is a dead end: `inbatch` wants the commit on the local
trunk and not yet on origin, while the flow has already pushed it.** At 22:19 r32 `feat/controls-settings` reads:
"pushed as 9f851b9; land refused on unread b7513e3; inbatch refused: not on the local main (5904e24); needs git -C
/home/user/workspace/twosuns pull --ff-only". origin/main is 9f851b9 "merge: feat/controls-settings", which carries
b7513e3 (the sender's hand-resolved merge from H21). `outside.inbatch` makes two checks in a row: the commit must be
an ancestor of `refs/heads/main` (the main checkout's local main, 5904e24, which belongs to no session and is moved by
nobody), and it must NOT be an ancestor of origin/main ("already on origin; … recorded with offledger"). The sender
pushes before `land` by design (the F14/F15 fix), so by the time `land` refuses, the unread commit is on origin, and
`inbatch` can never pass: pulling the main checkout would only move the refusal from the first check to the second.
The honest record is `offledger` or a reader's verdict over the pushed range, and neither is what the sessions were
told. F14 recurred in its first half: a session again asks the person to move the main checkout's local `main`
(then `fetch main:main`/`update-ref`, now `pull --ff-only`), a checkout no fleet session owns. Stage 2 (r11) landed
and shipped at 22:17:07 after the person's answer, and the orchestrator marked it walkable at 22:17:34; how its land
passed over aaaf5e2 was not examined here. With it main session 4 is unblocked (`waiting`), and three rows still
stand behind r32 (r31, r33, and r36's question).

H25. **H9 again (third wave), and two smaller things.** At 22:28:03 the daemon retired cf32b0ad (main session 4,
idle 12m — idle because both its rows were stuck behind H21/H24) and 0d43d310 (review session 14, idle 2m), both
`[low memory]`; memory 589 MB free, swap 3674/4095. The census at 22:28:07 still listed review session 14 as
`blocked`, status None, and `status` as idle with its seat — the census and the daemon disagree for a few seconds
after a retire. Main session 4 now shows `orphaned` with three rows (r11 shipped, r31 accepted, r36 queued).
(a) **obs — the sender now refuses to hand-resolve.** r38 `fix/spire-fog` (main session 3, accepted by review 14)
conflicted with origin/main 9f851b9 in `src/main.ts` (`onLockChange`); the sender recorded a wait on the
orchestrator: "needs the owner to merge origin/main and resubmit for a read" instead of resolving it itself as it
did for b7513e3 (H21). This is the right route and it was learned inside the run, not given by the tool.
(b) **minor — the empty-seat item prints the seat's age, not how long it has been empty:** `watch` said "1 post
seat(s) with no live session: main session 4 … (2 h)" one minute after the retire; the seat was reserved at ~20:04.

H26. **The judge broke stage 2; three things around it.** At 22:31:01 acceptance judge 9 recorded `broke` on r11
(stage 2, shipped) over the deployed 5904e24, walking `?day=1&time=0.5` (full eclipse per `docs/stage-2.md`),
`?time=0.02` (red night) and `__perf.stats()` — the start hooks H16 asked for now exist and the judge used them.
The broke move opened r41 at once, and the orchestrator had already raised main session 5 (seat `fleet/main-5`),
which adopted main session 4's rows (r11, r31, r36) and the orphaned minor 42's r33 at ~22:28:46 and claimed r41 —
the part-5 adopt and broke paths worked end to end. Also raised: review session 15.
(a) **minor — the automatic fix branch name nests the prefix:** `fix/feat/stage-2-two-suns` (`judging.py:116`,
`f"fix/{branch}"`); a branch that already has a type prefix should become `fix/stage-2-two-suns`.
(b) **minor — `whose move` says "nobody named" for a broken shipped row** (`commands.py:330`, the `""` that part 5
made `who_moves` return while the fix has not arrived). The line should name what it waits on — "the fix r41
`fix/feat/stage-2-two-suns` (main session 5)" — as the part gate's `""` should name the pending dependents; "nobody"
reads as an orphaned row.
(c) **bug — a queued row that conflicts with trunk has no way back to a reader.** r38 `fix/spire-fog`: after the
sender's refusal (H25a) main session 3 merged origin/main 9f851b9 into the branch as 42b0aeb, resolved the
`main.ts` conflict, got a green receipt (284/284) and pushed the branch; then: "42b0aeb is ready to read, but from
queued neither hand nor moved is legal; needs the orchestrator to put the row back into a readable state". The
state machine has `fixing` from `handed` (a reader's return) but no move from `queued`/`accepted` back to the owner
when the sender finds a conflict. The sender needs a `return` move (queued → fixing, with the reason), the same
shape as the reviewer's `fix`.

H27. **bug — a false `broke`: the judge walked another session's stale preview server, and the ledger has no way to
take a broke back.** Ledger entries for r41: at 22:31:01 acceptance judge 9 broke r11 (stage 2) "on the deployed
build 5904e24" and opened the fix row for main session 5; at 22:35:20 main session 5 waited on the judge ("stage 2
is live on origin/main 9f851b9 … `?day=1&time=0.5` -> eclipse 1; the orchestrator asked the judge to re-check on a
fresh build before any edit"); at 22:36:26 it released r41: "false broke: judge walked a stale foreign preview on
port 4173; stage 2 verified live on 9f851b9 by main 5 and judge 9". So the judge's `npx vite preview` did not get
the default port 4173 (another tree's preview, started earlier, still held it — by 22:37 nothing listens there),
and the browser opened that one. Three gaps: (1) `broke` records the revision the judge names but checks nothing
about it — `walked` compares `--build` with the deployed revision (`judging.walked`), `broke` does not, and neither
can know what the browser actually loaded; a build stamp the page exposes (the product's own revision in
`__perf.stats()` or a meta tag), read back by the judge and recorded, is the only honest proof. (2) Preview servers
from several trees share one default port on one machine; the judge post should start its preview on a port of its
own (`--strictPort` on a free port, as main session 5 did with 4755) and the project's `deploy` section could carry
the command. (3) There is no retraction: after r41 was released, r11 still reads `broke`, `status` shows the
deviation `broken_unfixed … and no fix row is open`, `watch` raises it, and `whose move` says "nobody named". The
judge needs an `unbroke` (or `broke --retract <why>`) that leaves the history and clears the state. F-/G-numbers:
none covered this; G10 (a fix settled by another row) is the nearest and did not recur.

H28. **bug — `moved` after acceptance needs the original reader's agreement, and the reader was retired.** r31
`feat/stage-3-weather-post` (now owned by main session 5): its tip moved a616236 → 37fe2d0 after acceptance (a merge
of origin/main 9f851b9, needed because r32 went first; receipt green, 291 tests). The sender's wait on the
orchestrator: "work moved refused: needs --agreed-by review session 13, who is no longer in the fleet".
`handover.moved` requires `agreed_by == row.reader` once the row was taken; review session 13 was retired by the
daemon at 22:01:11 (H22). There is no path through another reader: the rule needs "the reader, or a live reader of
the same post when the original is gone" — or, better, the move should send the row back to `handed` for any
reader when the owner's agreement cannot be had. With H24 and H26c this is the third way a row in the delivery half
gets stuck with no legal move, all three ending as a wait on the orchestrator.

H29. **bug — pulling the main checkout makes `land` skip the unread-work check entirely; and a correction to H24.**
The main checkout's `main` moved twice by `pull --ff-only` (reflog: 22:16:43 to 5904e24, 22:35:42 to 9f851b9 — made
on the person's side, as the sessions' waits asked). Right after each, the stuck row landed with no `inbatch`
record and no reader for the unread commit: r11 at 22:17:07 (over aaaf5e2) and r32 at 22:38:56 (over b7513e3, the
sender's hand-resolved merge that nobody read; evidence `{"on": "trunk"}`). Cause, verified in
`delivery.land`: when the landed commit is on the local trunk (`on_local`), unread work is counted with
`batch.unaccounted(ledger, rows, trunk_head, base=row.base)`, and `batch.outgoing` lists `trunk_head ^origin/main` —
empty once local main equals origin, i.e. whenever the push already happened. The `on_origin` branch counts from
`commit^1` and is the one that refused; the `on_local` branch passes everything. So the gate is decided by the state
of a checkout nobody owns: pull it, and a merge nobody read lands with the ledger's blessing.
**Correction to H24:** I wrote there that pulling the main checkout "would only move the refusal from the first
check to the second". That was wrong: after the pull, `land` itself passes (above), so `inbatch` is never reached.
The observation in H24 stands (the `inbatch` path is contradictory after a push); its consequence was misjudged,
and I passed that wrong consequence on to the person at 22:19 and 22:22 ("`git pull` will not help"). Fix
direction: `land` in direct mode always counts what the landed merge brought over its first parent (the
`on_origin` rule), regardless of where the local trunk stands; with the H20 fix, the reader-read merges pass and
the sender's own merges stop.
Also: r38 `fix/spire-fog` was pushed as 0fe4df3 (22:39) and now waits on the person for the same pull.

H30. **minor — a wait outlives its cause, and `watch` keeps putting it to the person.** At 22:43 r33
`fix/resize-during-load` still reads "waiting on the person: r32 (touches main.ts too) is blocked on inbatch
permission; r33 goes after r32", and `watch` lists it under attention at 14 min, although r32 shipped at 22:38:56
and left the open list. A wait note is free text; nothing ties it to the row it names. When a wait names another
row (`r32`), the ledger could record that link and drop or flag the wait once that row moves on; at least `watch`
could print "r32 has since shipped" next to it. The stale item adds to what the person is asked to look at.

H31. **H29 recurred, and the H28 dead end was escaped by release-and-reclaim.** The main checkout was pulled again at
22:52:56 (to 0fe4df3); r38 `fix/spire-fog` landed at 22:53:18 with `{"on": "trunk"}` and shipped — the `on_local`
path of H29, this time over a merge (42b0aeb) that review session 15 had read, so the outcome was right and the check
still did not run. At 22:53:53 the sender's queue of r31 was refused again (H28: `--agreed-by` needs the retired
review session 13); at 22:54:16 main session 5 released r31 ("reader review 13 gone; tip moved after accept; re-handed
under a new row for a live reader") and claimed r43 `feat/stage-3-weather-wiring` for the same work. It works, and it
is the only legal way out, but it cuts the row's history (the accept, the receipts, the judge's H17 wait on r29/r35
point at r31, not r43). At 22:54:36 r36 `feat/bench-harness` hit the H24 shape: pushed as 1d1a782, "land refused on
unread e38461c, inbatch needs local main = origin/main" — the sender asked about these author merges at 22:12 (H23)
and pushed anyway after 42 minutes without an answer.

H32. **minor — two misleading lines after the H31 workaround.**
(a) `status` findings at 22:58: "`feat/stage-3-weather-post` moved to 37fe2d0 after row r31 ended (released at
a616236); claim the new work". The tip moved before the release (the refused `moved` of H28 was about exactly that
move); the row kept a616236 only because `moved` was refused. The work was already claimed again as r43. The
`after_close` finding should compare with the branch's tip at release time, or not fire when an open row on the same
ref (`stage-3`) holds the new tip.
(b) r43's last lane run reads "green: bash: line 1: kill: (3500188) - No such process over 25cdd83". The run was a
compound shell command (a preview server started in the background, a walk, then `kill $srv`), and the one-line
summary is the last line of output — here the teardown's error, not the verdict. `lane run` should take the summary
from the last line that is not an error of the wrapper, or let the command name its verdict line; a green verdict
next to an error text reads as a contradiction.

H26c recurred at ~23:03: r43 `feat/stage-3-weather-wiring` (accepted by review session 15, queued) conflicts with
origin/main 1d1a782 in `src/main.ts` (imports; the bench block against the weather wiring); the sender waits on the
orchestrator, with no move to send the queued row back to its owner. The main.ts hot spot now has cost five rows a
stall (r31/r43, r32, r33, r36, r38): every stage wires itself into the one entry file.

H33. **Unread work reached trunk: the post-accept conflict resolution of r43 was pushed with no reader, and the
ledger saw it only afterwards.** r43 `feat/stage-3-weather-wiring`: accepted by review session 15 at 23:01:31 over
25cdd83; queued 23:01:55; the sender found a conflict with origin/main 1d1a782 (23:02:05, H26c). Main session 5 merged
origin/main (7e5725a) and added a commit, 434a323 "fix(bench): run the storm segment through the pinned weather" —
new code, not only a conflict resolution — got a green receipt (326 tests) and recorded "moved refused: not legal
from queued" (23:06:02, H26c again: no way back to a reader). At ~23:12 the sender pushed anyway: origin/main is
b7f1f78 "merge: feat/stage-3-weather-wiring", carrying 7e5725a and 434a323, neither read (the verdict is over
25cdd83). The sender's wait at 23:12:11 asks the person for the H24/H29 pull "for inbatch of batch merges". This
time the ledger noticed after the fact: `status` findings at 23:13 — "main: direct_commit - 434a323 fix(bench): …: on
`origin/main`, and no verdict covers it". So the detection half works; the prevention half does not exist in direct
mode (the push precedes every check, H20), and the three dead ends (H24, H26c, H28) pushed the sessions into exactly
the act the ledger exists to prevent. If the person now pulls the main checkout, H29 will land r43 without a word
about 434a323.

H34. **overnight (2026-09-30) — the fleet stood 15 hours on the person, and the idle retire took every worker.**
From 23:13 on 09-29 the delivery line waited on the person's pull (H24/H29/H33). `~/.claude/daemon.log`: at
07:03:03–07:13:03 `bg retire … idle-prompt, idle 8h` for acceptance judge 9, main session 3, main session 5 and review
session 15 — the ordinary 8-hour idle rule, not low memory. The orchestrator and the sender survived (they were the
ones waiting, `blocked`). At 14:12 the census holds orchestrator 3, sender 6 and three fresh seats the orchestrator
raised on the person's return (acceptance judge 10, main session 6, review session 16); main session 5's rows are
`orphaned` and `status` shows `mover_gone` for r44. `watch` ages read "15 h". The person pulled the main checkout at
14:11:33 (to b7f1f78), so by H29 r36 and r43 will now land without `inbatch` — including 434a323, unread (H33). Watch
next: whether the land passes silently, and whether the `direct_commit` finding on 434a323 stays after it.

H35. **H29 confirmed a third time: after the person's pull, 434a323 landed unread with the ledger's blessing; the
`direct_commit` finding survives the land.** The sender picked up by itself after the pull (no message from the
person reached it, only the orchestrator's re-seat traffic): at 14:16:22 r36 `feat/bench-harness` and r43
`feat/stage-3-weather-wiring` landed, both with evidence `{"on": "trunk", "trunk": "b7f1f78…"}`, and shipped at
14:16:23. The r43 range carries 7e5725a and 434a323, neither read (H33); r36's author merge e38461c likewise. No
`inbatch`, no reader. At 14:18 `status` still lists "main: direct_commit - 434a323 … on `origin/main`, and no verdict
covers it" — so the detection is independent of the land, and it is now the only trace. Also: r43's `whose move`
reads "nobody named (the judge walks it after fix/exposure-owner ship)", the part gate naming what it waits on —
which is what H26b asks for the broken case (r11 still reads bare "nobody named"). H26c recurred a third time: r33
`fix/resize-during-load` queued at 14:16:57, conflict with b7f1f78 in `src/main.ts` (the resize handler), wait on the
orchestrator at 14:17:11.

H30 recurred at 14:24: r44 `fix/exposure-owner` still reads "waiting on sender 6: orchestrator: do not continue
until r43 … is on origin/main", although r43 landed at 14:16:22, and main session 6 has been running its receipts
on r44 since 14:18 (runs at 14:18:08, 14:20:49, 14:23:49). The owner works; the row says it waits. A wait naming a
row that has since moved on is the same stale-note shape as r33 at 22:43 yesterday.

H36. **bug — an adopted row cannot be taken into the new owner's tree: the retired session's worktree still holds
the branch.** r33 `fix/resize-during-load` (minor session 42's work, retired at 21:59 on 09-29 by H22; its seat was
released at 14:12, the row adopted by the orchestrator and handed on to main session 6). At 14:24:59 main session 6
recorded: "tree switch refused: branch is checked out in /home/user/workspace/twosuns-minor-42 (no live session);
needs that tree released". `git worktree list` still shows `twosuns-minor-42 8d61b42 [fix/resize-during-load]`, clean,
ahead 1 / behind 28. Git refuses a second checkout of a branch, and flotilla has no move that frees a dead seat's
tree: `release` of the seat row and `adopt` of the work rows both leave the worktree as it was. The seat release (or
`adopt`, when the old owner is not live) should detach the dead seat's tree from the branch
(`git -C <tree> switch --detach`) when it is clean, or say which tree holds it and how to free it when it is not.
G12 (`fleet down` leaving trees) is the nearest; this is the same leftover met from the other side, mid-run. The
owner moved on to other work (claimed a new row at 14:25:09). Also at 14:26:47 the sender again asked before
pushing (H23 pattern): "confirm a live reader read author merges 3cbc4c2 and bb9d9f8 before I push 45d19d4" on r44.

H37. **The unread 434a323 got a reader after all — by patch-id, through another row; and the person's pull has become
a step of every push.** At 14:30 `status` findings are "none": the `direct_commit` on 434a323 cleared. Verified:
f118c07 "fix(bench): run the storm segment through the pinned weather" carries the same patch (`git patch-id
--stable` 2a419a54… for both) and lies in r44 `fix/exposure-owner`'s range (ancestor of 917deb4), which review
session 16 accepted at 14:26:07 over 917deb4. So the reviewed-cherry-pick rule of `batch.Accounting` (picks by
patch-id) settled H33 honestly: the change was read, in another row. But the sender's line keeps needing the
person: r44 pushed as 45d19d4 and at 14:27:29 waits on the person — "land needs local main pulled to include the
read merges 3cbc4c2 and bb9d9f8". And the sender now reasons about H29 in its own words: r48 "merged locally as
a3fa6cc (green 335), not pushed: local main is stale, land would sweep in r44's unread batch; push after r44 lands"
(14:29:02). The sessions have learned to route around the ledger's gaps; the pull of a checkout nobody owns has
become a per-push chore for the person (09-29 22:16, 22:35, 22:52; 09-30 14:11; now a fifth). Fix direction for part
6: `land` in direct mode must not depend on the main checkout at all (count from the landed merge's first parent,
H29), and then no pull is needed.

H38. **bug — the part gate also stops a `broke`: a judge who has seen a real defect cannot record it until the
orchestrator marks the row walkable.** r43 `feat/stage-3-weather-wiring` (shipped; open dependents r44
`fix/exposure-owner` and `fix/storm-sun-exposure` not shipped). At 14:31:46 acceptance judge 10 recorded a wait:
"broke on r43 refused (a part of the path: fix/exposure-owner, fix/storm-sun-exposure not shipped); waiting for
walkable from the orchestrator or r44 landing. The seam is reproduced…". The orchestrator marked r43 walkable at
14:31:40 (crossing messages), and at 14:31:55 the judge broke it: "a straight vertical seam in the dust band: a
light wedge on the left, a denser, higher wall on the right, the edge as if cut" (`?weather=storm`, phase 1–2); the
fix row r50 opened and main session 6 claimed it at 14:32:06. Cause: `judging.broke` calls `_whole_or_walkable`
(line 114), the gate written for `walked` — "do not accept a part as the whole". For `broke` it inverts the intent: a
defect seen on a part is a defect, and the part's dependents shipping will not make it go away. `broke` on a part
should pass (perhaps noting the part's open dependents in the letter), and only `walked` should wait for the whole.
The cost here was small (a minute, thanks to the crossing walkable), but it put the orchestrator in the path of a
bug report.

H36 escaped the H31 way. After the person's pull (the line moved: r44 landed `{"on": "trunk"}` at 14:37:30, r48
`{"on": "origin/main"}` at 14:37:32, both shipped), main session 6 released r33 at 14:37:39 — "branch pinned by dead
session's worktree; re-handed as new row fix/resize-load-2" — and claimed r51 on a fresh branch. Second
release-and-reclaim of the run; each one drops the row's history and makes a second branch for the same work (the old
`fix/resize-during-load` stays in `twosuns-minor-42`). The orchestrator also raised main session 7 (seat r52, 14:37:44;
claimed r53 at 14:38:11).

H39. **gap — the judge cannot walk what a headless browser cannot perceive (sound), and flotilla has no path that
hands such a walk to the person.** r48 `feat/sound-weather-hook` ("hear the weather while walking", shipped 14:37:32).
At 14:39:39 acceptance judge 10 recorded a wait on orchestrator 3: "sound is not audible in headless: walked/broke
cannot be recorded honestly; I ask the person to listen to the storm on a real machine, or to say how to record it".
The judge refuses to fake a walk — right — and routes through the orchestrator, not the person directly (F16/F17
did not recur). What is missing: (1) a judge-side proxy the product can offer, as with `?pos=`/`?day=` in H16 — an
audio graph readout (per-layer gains, an AnalyserNode RMS during `?weather=storm`) exposed on `__perf`; (2) a
ledger path for "this walk belongs to the person": the judge records the walk as the person's
(e.g. `walked --by-person` pending, or a `person` item in `watch`), and H12's closing step collects such items for
the person's final check instead of leaving a row open on the orchestrator.

H40. **obs — the person's design question went back to research, through the orchestrator's own helpers (H8's light
form).** r49 `fix/storm-sun-exposure`: main session 6 put three options to the person (14:44:52: close / retune the
low-sun Mie halo / dim it with `sunDim`). At 14:54:22 it recorded: "person asked for a physics check of Rayleigh/Mie
halo size first; orchestrator's researchers will send numbers to verify the shader against; build nothing until…".
The "researchers" are in-session subagents of orchestrator 3: the census count did not change (8 live), and nothing
in the ledger names them. That is the right weight for a read-only lookup — H8's worktree-per-helper design is for
helpers that write — and it suggests part 6 name the line: in-session subagents for reading/research, ledgered helper
sessions (own tree) only for work that edits.

H5 recurred at 15:06: `status` findings "fix/storm-sun-exposure: unread_in_trunk - row r49 is claimed, and its tip
c9b9913 is already in `origin/main`: it reached trunk without a verdict; record it with offledger". c9b9913 is the
sender's merge of r51 (`fix/resize-load-2`, landed 15:03:30); r49 has no commit of its own — main session 6 moved its
fresh branch to the new trunk. Same false signal as H5 on 09-29 20:43, with the same harmful advice (`offledger` for
work that does not exist). Not fixed yet; stays on the part-6 list.

H9 recurred on 09-30 at 15:24:04: `bg retire b4199852: idle-prompt, idle 22m [low memory]` — review session 16, idle
since its last accept (15:01:22); 528 MB free, swap 3714/4095. `watch` named the empty seat at once (1 post seat with
no live session), with the seat's age "(1 h)" rather than the time empty (H25b, recurred).

H41. **obs — a browser capture was killed by the OOM killer, and the owner stopped instead of retrying.** r49
`fix/storm-sun-exposure`, 15:30:31, main session 6 waits on orchestrator 3: "before-frames capture was killed by the
system for low memory (616 MiB free of 15 GiB); not restarting on my own; ablation table is done; need a go to rerun
the browser captures". The right reflex (a killed run is not a result, and a blind retry under the same pressure
dies again). By 15:30:40 memory had freed (9650 MB free, swap still 4063/4095), so the go is cheap now; nothing in
flotilla tells a waiting session that the machine's state changed. The lane knows the machine (`machine:` answers in
`flotilla lane`); a wait whose reason is "memory" could be released by the lane when memory returns, the way `take
--wait` queues a run.
Same minute, second victim: main session 7 on r53 (15:30:38): "a headless 300-frame run through the lane was killed
by the system for lack of memory (0 GB free); I do not restart without permission — waiting for word when the
machine frees up". This one was booked through the lane, so the lane's single slot did not protect it: the pressure
came from outside the lane (main session 6's capture, which was not booked, and non-fleet processes). A lane that
guards only the fleet's own long runs cannot keep a booked run alive; its `machine:` check could at least refuse to
start a run when free memory is under a floor, and name what holds it.

H42. **gap — the orchestrator waiting on the person is invisible to `watch`.** From 15:42 to at least 15:48 the census
shows orchestrator 3 `state=blocked status=waiting` (it has a question open to the person), while `watch` lists only
the stale `broken_unfixed` (H27) and the empty seat. The PERSON item from part 4 fires only for rows whose recorded
wait names the person; the orchestrator's own question lives in its session and in no row. Meanwhile two main
sessions wait on the orchestrator (H41), so the whole line hangs on a question `watch` does not show. The census
already carries the signal (`status=waiting` for a live orchestrator), so `watch` could raise "orchestrator 3 is
waiting on the person (N min)" as an attention item — the one thing a person polling `watch` needs to know.

H39 answered in the product: minor session 43 (raised 15:52) rendered the weather sound to WAV files and delivered
them to the person (16:02:26: "WAV files ready and sent; waiting on a decision: take tools/ to handover or close r56
without merging"), then released r56 at 16:02:36 — "audio capture tooling only; recordings delivered to the person;
not for trunk". So "the person listens" became an artifact the person can open, without a real-machine run — the
same idea as the `.exe` for 2048. The ledger has no word for this outcome: the row is `released` (reads as "will not
happen"), though its product — the recordings — was the point. A `delivered-to-person` close, or an evidence field
on release, would keep that honest.
Also: main session 7 on r53 (16:01:38): the second 300-frame run "stopped by my own 30-minute background limit, not
by memory: 300 frames did not accumulate, the frame rate is unknown" — SwiftShader at 480×270 is too slow for the
frame-count check; waiting on a decision about the lane queue. Review session 17 raised at 16:01:46 (seat r57).

H20 recurred at 16:13:08, in its purest form: r53 `fix/black-frame` was accepted by review session 17 at 16:12:33 over
2b2c059 — the verdict revision itself is the merge "merge: origin/main into fix/black-frame" — and `land` refused
"on unread 2b2c059 (read by review 17)". The reader read exactly that commit; `batch.Accounting.account` still returns
None for it because it is a merge and not a clean one. The sender asks the person for the sixth pull (H37), and holds
r49 (accepted by review session 18 at 16:12:54, merged locally as a937a71) behind it "until local main is pulled".

H43. **obs — the reviewer took over the conflict check, and that closes H26c's gap from the other end.** r60 (main
session 6), 17:07:42: review session 17 returned it with `fix`: "1) the tip does not merge with trunk be94670 (r59
footsteps): `git merge-tree 0ad681d be94670` conflicts in src/…". A conflict found at review time goes back to the
owner through the ordinary `fix` move; found by the sender after `queue`, it has no move at all (H26c, three times).
So one cheap fix for part 6 is to have the review post (and `accept`) run the merge-tree check against current trunk
and refuse to accept a tip that does not merge — the sender then rarely meets a conflict, and when trunk moves
between accept and queue, the sender still needs the `return` move. Same tick: r63 `fix/dust-hides-terrain` (with
`docs/visibility-physics.md`) accepted 17:07:39 and shipped 17:08:20 in 41 s; the judge walked and closed the three
sound rows (r35, r48, r59) at 17:05, which settles H39 for this run.
H42 widened, cause unknown: review session 17 shows `state=blocked status=idle` in the census from 17:09 to at least
17:15, after its `fix` on r60 (17:07:42). Its hook trace: `stop` 17:08:03, `permission never`, `ask never` — so it is
not a permission prompt or an AskUserQuestion that flotilla's hooks saw. What "blocked" means here was not
established. `watch` says nothing about it; a live seat blocked for minutes with no ledger reason is exactly what the
H42 item should surface, whatever the cause.
It ended by itself: r60 was re-handed at 17:23:55 and review session 17 took it at 17:24:24, `state=working` at
17:27. So its `blocked` covered exactly the wait for the owner's fix — the session's own reading of its state, not a
prompt. Still worth surfacing: in the census "blocked" looks the same whether a session waits on a peer (harmless)
or on the person (not), and only the ledger can tell which.

H44. **gap — a tooling row that will never ship holds the part gate of the row it measures.** Minor session 43's audio
capture rows are "tooling only, not for trunk" (H39 follow-up), yet each is claimed with `--requires` the row it
records: r61 and r64 require r59 (footsteps), r65 `feat/audio-capture-atmo` requires r60 (sound atmosphere). By the
part gate, r60 is then a part whose dependent has not shipped: at 17:47:46 acceptance judge 10 recorded "walked
refused: a part of the path (feat/audio-capture-atmo not shipped); the sound measurements on cda688b are made and
sent, waiting for walkable". The orchestrator marked r60 walkable (17:47:40, crossing), and the judge walked it at
17:54:32. `--requires` carries two meanings the gate cannot tell apart: "I build on it" (the whole is not whole
without me) and "I need it to exist first" (a measurement, a tool, a test harness). Fix direction: only rows that
are going to ship count as dependents — a claim can say so (`--requires` for building, a separate `--after` for
ordering only), or the part gate ignores dependents whose owner declared them not for trunk. Same tick: sound
atmosphere 2 (r67) accepted 17:51:43, shipped 17:55:03, walked and closed by 18:00:42; H20 did not recur on it
(no author merge in its range).

H44 recurred at 19:46–19:47 (r68 `fix/wind-strong-shape`, dependent `feat/sound-spatial` not shipped): the judge's
walk was refused as a part, the orchestrator marked it walkable at 19:46:54, the judge recorded its wait at 19:47:00
and walked at 19:47:06. Second time the same crossing: the orchestrator now marks sound rows walkable pre-emptively,
which is the workaround the part-6 fix direction (`--after` for ordering-only links) would make unnecessary.

H45. **gap — a seat with no work goes unnoticed for hours; and the late-day slowdown is mostly heavier work.** Asked
by the person at 20:21 whether the fleet idles more as the run goes on. Measured from the ledger (09-30):
- **Shipped per half-hour**: 2–3 from 14:00 to 17:30, then 0–1 (18:00–18:30 and 20:00 none), 2 at 19:30.
- **Work per row grew**: rows claimed 14:25–17:37 took 2–66 min from claim to hand (median ~26); the ones claimed
  after 17:00 took 53–88 min (r66 hitch audit 53, r68 wind shape 87, r69 quality switch 78), with 8–9 lane runs
  each — measurement-heavy tasks (perf, sound levels), and the last ones are end-of-task work (README conformance,
  release package). Reading grew too: r66 32 min, r71 reading since 19:44 (37 min at 20:21).
- **An idle seat**: main session 7's last move was `close r59` at 17:05:43; at 20:21 it holds only its seat
  (`roster: main session 7: idle - fleet/main-7 (reserved)`) — 3 h 15 min with no work, while main 6 and main 8
  each hold one to two rows and the two reviewers are the queue. `watch` says nothing: a seat with no row is not a
  dropped ball, and the SEATS item covers only seats whose session is gone. It costs memory (each seat ~0.4 GB, the
  machine retired seats three times under pressure) and throughput.
Fix direction: `watch` for the orchestrator raises "main session 7 has had no work for 3 h: give it a row or
retire it" past a threshold, and the orchestrator post says to size the fleet to the queue — a spare implementer
while reading is the bottleneck is better retired or turned into a reader.

H46. **obs — the queue drained at 21:10, and nothing tells the person.** With r76 closed at 21:10:08 the only open
rows are r11 (stage 2, the false broke of H27 that no move on plugin 0.1.0 can take back) and two of minor session
43's audio-capture tooling rows (r64, r65) waiting on the orchestrator. Eleven seats are live and idle. `watch` still
exits 1 on the stale `broken_unfixed` alone, so a person polling it sees "attention" on a finished fleet and no
line saying the work is done. This is exactly the moment H12 designs for (the orchestrator asks the person to check
the result and offers `fleet down`, listing what stays alive); until then it rests on the orchestrator noticing.

H47. **H9 again, caused by the fleet's own sizing: five implementers raised under low memory, and the retire wave
took every reader.** After the person gave a new batch (~21:40), the orchestrator raised main sessions 9, 10 and 11
(seats r78, r85, r86 at 21:42–21:51) next to mains 6, 7 and 8 — six implementers claimed rows r79–r88 within ten
minutes. At 21:55:03 the daemon retired, `[low memory]`, minor session 43 (idle 3m), review session 17 (idle 3m)
and a spare; review session 18 is gone from the census as well. At 21:58 the census holds six mains, the judge and
the sender `blocked`, the orchestrator `blocked`, and no reviewer: every handover now waits for a reader to be
raised into the same memory. The retire rule takes the idle, and readers are idle exactly while implementers work,
so adding implementers retires readers first. `watch` names the three empty seats and the two `mover_gone` rows of
minor 43. Fix direction (with H9/H45): the orchestrator sizes the fleet to memory and to the queue — implementers
no more than readers can follow — and `flotilla spawn` refuses or warns when free memory is under a floor.
Correction, same minute, from the full daemon log: the 21:55:03 wave also retired a0b1c29b (sender 6, idle 3m) and
669e61f3 (acceptance judge 10, idle 3m). The census at 21:58 still listed both, `state=blocked status=None` — a
retired session lingering in the census (the H25 disagreement, now for three minutes rather than seconds). So the
wave took the sender and the judge as well as the readers: nobody is left to read, land or walk.

H48. **bug — a retired session stays in the census and flotilla counts it alive, so its empty seat is never
raised.** `~/.claude/daemon.log`: `bg retire a0b1c29b` (sender 6) and `bg retire 669e61f3` (acceptance judge 10) at
21:55:03, then `bg settled a0b1c29b (done)` / `bg settled 669e61f3 (done)`; `ps` shows no process for either id at
22:04. Yet the census still lists both with `state=blocked status=None`, and `flotilla fleet` at 22:04 prints
"sender 6 (sender) alive (blocked), claude attach a0b1c29b" and the same for the judge. So `watch` names no empty
seat for them (its SEATS line lists only minor 43 and reviewers 17/18, whose census entries did disappear), and the
orchestrator, re-seating at 22:02 (review sessions 19 and 20, minor session 44), raised no sender and no judge.
Every row that gets accepted will now queue on a sender that does not exist, with `whose move` naming "the sender"
and the dropped-ball check seeing `blocked` — reported as "not working, message them", a message to nobody. The
signal to tell them apart is in the census itself: a live background session reports a `status`; a retired one
lingering in the list reports `status=None`. Fix direction: treat a background entry with no status (or a daemon
`settled … (done)` record) as gone, everywhere liveness is asked (`live_names`, `fleet`, `retire._running`, the
SEATS item). G12's fix (retire counts any listed session as running) is the same question from the other side and
must keep stopping such entries.
H48 follow-up: after the person's message reached orchestrator 3 (relayed 22:11), it released the dead seats (r24
sender 6 22:11:42, r46 judge 10 22:11:43) and reserved sender 7 and acceptance judge 11 (22:11:44–45); the stale
census entries for a0b1c29b and 669e61f3 were gone by 22:12:31. In the same minute it also reserved main session 12
(r94, 22:11:33; claimed r97 at 22:12:26) — a seventh implementer, against the sizing note in the message (H47).
Also: main session 6 recorded on r11 that stage 2 "was walked on 9f851b9 but the ledger could not record it" and
asked judge 11 to re-walk — the H27 false broke still standing, one day later, for want of `unbroke` on 0.1.0.

## The person's verdict (2026-09-30, ~22:15)

The person walked the build and called it "very cool". Liked: the atmosphere and the sound. Missing: the visual side
reads a little sterile, and there are too few points of interest — the ruins are there, but there is nothing to do in
them. The person carries the fleet to the end and stands it down themselves; monitoring stopped at 22:15 (last
recorded finding H48).

H49. **gap — every seat brings the person's whole MCP and plugin set, and that costs as much memory as the sessions
themselves.** Measured at 22:40 on 09-30 at the person's request ("the fleet complains about memory again"): 1.4 GB
available, swap 3958/4095. By process group: Claude sessions 6.5 GB in 29 processes; **MCP servers 6.1 GB in 167
processes** — per server type chrome-devtools-mcp 2.0 GB (58 processes), serena 1.1 GB (28), mcp-pdf-server 1.0 GB
(28), playwright-mcp 1.3 GB (39); Chrome 1.2 GB. Each background seat starts every MCP server the person's user
scope enables, whatever its post needs: a reviewer or a sender needs none of a PDF server, a devtools bridge or a
code index, and only the judge needs a browser. On top, the `security-guidance` plugin's edit hook starts a headless
`claude` per edit in a seat's tree (seen: pid 1754421, 225 MB, parent `security_reminder_hook.py`, cwd
`twosuns-main-12`). No dead session was found: every large Claude process maps to a live census entry except the
daemon's pre-warmed spare (136 MB) and one interactive `claude` on pts/4 in `/home/user/workspace/twosuns` started
09-29 20:03 with its own four MCP servers, which the census does not list. The fleet itself had grown back to seven
implementers (main sessions 8–14; 13 and 14 raised at ~22:36). Fix direction: `flotilla spawn` launches seats with
an MCP configuration per post (`--strict-mcp-config` with the servers the post declares, e.g. the judge's browser),
and onboarding names the user-scope plugins whose hooks run in every seat; the sizing rule of H47 counts a seat at
its real cost (~0.8 GB with its servers), not the session's.

H50. **gap — a retired session's detached processes outlive it.** At ~00:22 on 2026-10-01, 27 `vite` dev servers
were alive with no session behind them: each was started detached (`nohup ... &`) by a session that had since been
retired or had died, was re-parented to pid 1, and kept its memory with nobody to stop it. Retire stopped the session
and released its seat, and nothing looked at what the session had left running in its tree. Measured on this
machine: a process detached by `nohup ... &` or `setsid` from a session's shell is re-parented to pid 1 (there is no
subreaper), and a background Claude session runs under its own pty host, so "has no terminal" does not tell a
person's process from a session's. Fixed in 0.3.1 for every retire from then on: retire stops the orphans (parent
pid 1) working in the seat's tree, with everything under them, and spares a subtree that holds a terminal or a live
census session. Nothing goes back to the trees of seats retired before 0.3.1: the 27 found here were stopped by
hand. The same machine shows why the mark cannot be the directory alone: the deployment's `app-serving` and the CI
runners are also orphans of the same user under pid 1, told apart only by their cgroup, a `.service` unit
(review of the fix, 2026-10-01).
A second form, found the same night (~01:00): `main session 12` had copied trunk into its own job directory and run
`vite --port 4461` there, `~/.claude/jobs/cd30b6e8/tmp/trunk` (193 MB, parent pid 1). That directory is outside the
seat's tree, so 0.3.1 would not look at it. Fixed in 0.3.2: retire also looks at the job directory of the session it
stops (`jobs/<its id>/` under the Claude Code configuration directory).

H51. **gap — the 0.3.2 retire would cut a plugin's session-end hook.** Standing the fleet down at ~02:00 on
2026-10-01 left, in every seat's tree, a `bash .../remember/0.33.0/scripts/session-end-hook.sh` running
`save-session.sh <session id>`: the `remember` plugin saves the session as it ends. Each was started by the stop
itself (01:59:50, the second `claude stop` ran), re-parented to pid 1, had its working directory in the tree and no
terminal, so it met every mark 0.3.2 uses for a leftover: a retire would have sent it SIGTERM mid-save. The hooks
end by themselves within seconds. Fixed in 0.3.3: retire reads the clock before it stops the session, and only an
orphan that started before then is a leftover.

H52. **gap — a plugin update leaves the project's posts behind.** After the fleet moved to 0.3.3 (2026-10-01, ~03:00)
it stalled on three steps at once, two of them from one cause: `.flotilla/posts/*.md` are copied into the project at
onboarding, existing files are kept, and nothing ever says they are older than the plugin's templates. twosuns ran
reviewer v1 (no `vouch`), sender v4 (no `return`), judge v2 (no `unbroke`, and no `plugins:`, so 0.3.3 started the
judges without the browser plugin) and no helper post. The reviewer had read four batch commits and could not record
it; the sender was told to write the reading in the reviewer's name (`inbatch --read-by`) and Claude Code's auto-mode
classifier refused that as a bypass, correctly. r11 could not be cleared without `unbroke`. Fixed by hand on
twosuns trunk (28a7589: every post brought to its 0.3.3 template, the project's `model: opus` kept). Fix direction:
`doctor` and `watch` name a post whose `template_version` is below the plugin's, and a command brings posts up to the
templates while keeping the project's own edits.

H53. **gap — a message wakes a retired session.** `orchestrator 3`, stopped by `fleet down` at ~02:00, was alive
again at 03:06 under its old id next to `orchestrator 4`: a message addressed to a stopped background session
resumes it. Nothing in flotilla noticed a live session holding the name of a closed seat. Stopped by hand. Fix
direction: `watch` names a live session whose seat row is closed and offers `claude stop`; letters and the posts
address the live holder of a post, not a name from memory. Also seen the same morning: the old seats' trees, kept on
the person's word, still held their branches (0.1.0 did not detach a retired seat's tree), and new seats stopped on
`tree switch`; 22 clean trees were detached by hand, keeping them.
