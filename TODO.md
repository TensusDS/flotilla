# TODO

Deferred findings, kept here until a plan takes them. Each names where it came from.

**How this file is kept.** Every open item ends with when it was last checked against the code, on which version,
with what verdict and where: `*(checked <date> on <version>: still [S|B|P] - <file:line>)*` (S a guard lets
something through, B a bug a user meets, P polish, tests or docs), or `open, design`, or `measure` (needs a live
run). A later triage re-checks only items whose named files changed since that version. An item that is done -
fixed by a release, or found fixed - leaves its section for **Done** at the bottom, one line with the version.

## Rented machines (rig), after 0.9.0

- **Stage 2c (0.11.0):** packing by estimates from the runs measured on rig machines (longest first, short ones
  fill, none starved); the account credit beside the local count. Design `docs/specs/2026-10-06-rig-design.md`
  section 7. *(checked 2026-10-07 on 0.10.0: open, design)*
- **From the final review of 0.9.0** (minor, deferred): a `rig up` cut before create leaves a REQUESTED machine that
  stalls later calls for 15 minutes; the budget pre-check in `rig up` is outside the lock (bounded by the machine
  ceiling and the 90 % drain); a seat's `--why` reaches the orchestrator's model in fleet lines and `rig` (never a
  pasted line); an adopted machine's `created` is the adoption time, undercounting the floor by one call; spec
  section 5 promises the account credit in `flotilla rig`. *(checked 2026-10-07 on 0.9.0: still P -
  flotilla/rig/commands.py `_up`, flotilla/rig/surface.py, flotilla/rig/reaper.py)*
- **For `rig run`:** an ssh login shell on a vast machine does not carry `CONTAINER_ID`/`CONTAINER_API_KEY`/
  `FLOTILLA_WATCHDOG_MINUTES` (the watchdog's process does; live check 1) - renew by touching the heartbeat file.
  *(checked 2026-10-07 on 0.9.0: open, design)*
- **From the final reviews of 0.10.0** (minor, deferred): `${x@P}`, `${arr[..]}`, `$[..]` with a value set in an
  earlier segment run a substitution locally (the eval class, CEILING); a real silent drop keeps sshd's end open, so a
  chatty command blocks on a full pipe instead of SIGPIPE (the stand-in closes the pipe at once). *(checked 2026-10-08
  on 0.10.3: still P - flotilla/guards/person.py, tests/rigssh.py)*
- **Stage 3:** onboarding for a weak machine (the base set, the three questions, the cost with its consequences) and
  the `ssh` adapter for the person's own machine. *(checked 2026-10-07 on 0.9.0: open, design)*

## From the README's known limitations (2026-10-04)

Each is a limitation README states today, judged fixable in principle; none is planned yet.

- **Windows.** Linux and macOS only. Claude Code runs natively on Windows; what flotilla would need is a process table,
  process groups and signals, file locking and path handling for it, and CI on `windows-latest`. Large. *(checked
  2026-10-04 on 0.7.12: open, design)*
- **macOS skips two protections.** The memory floor reads `/proc/meminfo` and stopping a retired seat's leftover
  processes reads `/proc`; on macOS `sysctl hw.memsize` with `vm_stat`, and `ps -g` / process groups, would answer the
  same questions. *(checked 2026-10-04 on 0.7.12: open, design)*
- **One lane per machine, shared by every project's fleet.** Two fleets wait for each other's long runs; a per-project
  share of `lane_capacity`, or fair queueing between projects, would split the machine. *(checked 2026-10-04 on 0.7.12:
  open, design)*
- **Long-lived seats get slower, and nothing replaces one.** flotilla reads no transcript (policy), so it cannot see a
  seat's context size; it can count what a seat has done - rows closed, hours held - and offer to retire an idle seat
  and raise a fresh one past a threshold the profile sets. *(checked 2026-10-04 on 0.7.12: open, design)*
- **In `ask` mode a late answer is lost.** When the orchestrator is the person's own session and is busy, a seat's
  question times out (about nine minutes) and the person's later answer is not kept; keeping it for the same call's re-
  ask, for a short while, would spare asking twice. *(checked 2026-10-04 on 0.7.12: open, design)*
- **The leading session's name hook was measured on Claude Code 2.1.287 only.** `flotilla doctor` could check that the
  hook still names the session, as it now checks the census shape (0.7.7). *(checked 2026-10-04 on 0.7.12: open,
  design)*
- **Most guards read the rules from the local `refs/remotes/origin/<trunk>`**, which a session can move; only the push
  guard, `pre-push`, approvals and allows ask origin. The rest could ask origin too, through a cached `ls-remote`
  refreshed every few seconds, so a moved ref cannot change them. *(checked 2026-10-04 on 0.7.12: open, design)*
- **A broken profile on trunk closes trunk, the fix included.** The fix is pushed with `--no-verify`. A push whose only
  change makes `.flotilla/project.toml` readable again - checked by parsing the pushed profile - could be let through
  without the override. *(checked 2026-10-04 on 0.7.12: open, design)*
- **The push guards are no lock, and flotilla does not say whether the lock is there.** `flotilla doctor` could ask
  GitHub (`gh api repos/<o>/<r>/rules/branches/<trunk>` or branch protection) whether trunk is protected, and warn when
  it is not, naming the setting. *(checked 2026-10-04 on 0.7.12: open, design)*

## From watching the twosuns fleet on 0.7.1 (2026-10-03, night; corrected 2026-10-04)

- **The way in is too heavy for a first try.** Posts, seats, lane, receipts, a ledger of ten states, 13 skills and a
  540-line README stand between a newcomer and "one session writes, another reviews". A light mode - author and reviewer
  only, no sender, no lane - that starts in minutes, and a README whose opening says only that. *(checked 2026-10-04 on
  0.7.8: open, design - needs a spec)*

## From the review of 0.7.9 (2026-10-04)

- When the guards fail, an override anywhere in the command line counts (`echo FLOTILLA_GATE_OVERRIDE=x; git push`):
  `flotilla.guards.on_failure`, the hook's last resort and `bin/flotilla` read it as a substring, while the push guard
  counts it only at the head of the door's segment. *(checked 2026-10-04 on 0.7.9: still [P] - guards/__init__.py
  on_failure)*
- Onboarding a repository whose only workflows are symlinks now records no CI and reads no job ids from them; it matters
  only if GitHub Actions runs a symlinked workflow, which nobody measured. *(checked 2026-10-04 on 0.7.9: measure)*

## From the twosuns orchestrator's notes and the review of 0.7.17 (2026-10-06)

- Trial merge at accept: conflicts between groups of changes surface only at release; `git merge-tree` against trunk
  and the other accepted rows at `accept` would show them at once. *(open, design)*
- `moved` after `accept` needs the reader's `--agreed-by`; a pure rebase (same patch ids) could pass without it.
  *(open, design)*
- The letter after `assign` is sent by hand and forgotten; a hook could require it in the same turn. *(open, design)*
- A guard against `git reset --hard` / force-push on a branch with an accepted revision (twosuns: a seat reset its
  branch and undid the reviewer's edits). *(open, design)*
- `adopt --from` moves only rows the gone session owned; rows it was reading wait for `assign --reader` one by one.
  *(checked 2026-10-06 on 0.7.17: still [P] - ledger/steering.py adopt_from)*
- A held queued row in direct mode: `land` refuses with "the orchestrator runs unhold", but nothing tells the
  orchestrator; the hold could surface in the watch. *(checked 2026-10-06 on 0.7.17: still [P] - ledger/delivery.py)*

## From lane admission, stage 1, and its reviews (2026-10-05)

- Stages 2-4 of `docs/specs/2026-10-05-lane-resource-admission-design.md`: budget admission (CPU 1.5x cores, memory
  read under the lock with reservations), overtaking with the shadow guard, fleet size reading the lane. Stage 2 must
  treat an estimate as a hint with a ceiling, never as a hard refusal: any session can write the journal.
  *(open, design)*
- A run nested inside another `lane run` adds samples to the project's prior twice (outer and inner record); a
  failure writing the inner record would replace the caller's exception. *(checked 2026-10-05 on 0.7.16: still [P]
  - lane/acquire.py held)*
- The journal is folded whole on every write and never compacted. *(checked 2026-10-05 on 0.7.15: still [P] -
  lane/book.py fold)*

## From the research on AI coding in large codebases (2026-10-05)

- **A write lease on an area of the code.** Two seats writing the same files conflict: 19.8% of overlapping PRs by one
  agent and 41.7% by different agents hit textual conflicts (arXiv 2607.04697, 33,596 PRs); Cognition moved to
  "writes stay single-threaded". flotilla routes work by rows, not by files: a claim could name the paths (or
  directories) it writes, the orchestrator would refuse or queue a second claim on the same paths, and `fleet size`
  could count independent areas as writers (stage 2's `K_eff`). Needs: how a seat declares paths before it knows
  them, and what a lease does when work spills over. *(open, design)*
- **The cost of always-loaded instructions, per seat.** Every seat pays the project's CLAUDE.md, rules without
  `paths:` and the post file on every turn; long context files raised cost by 20-23% without a significant gain in
  success (arXiv 2602.11988), and Claude Code's docs advise CLAUDE.md under 200 lines, path-scoped rules and skills
  loaded on demand. `flotilla doctor` could measure what a seat loads before its first word (bytes and lines, by
  file) and name the largest, and onboarding could warn above a threshold; discipline that a hook can hold belongs
  in a hook, not in the always-loaded text. *(open, design)*

## From fleet sizing, stage 1, and its reviews (2026-10-05)

- Stages 2-4 of `docs/specs/2026-10-04-fleet-sizing-design.md`: the code's independent areas and hotspots from one
  `git log`; money (first measure whether a `--bg` session runs its status line); the watch's grow and shrink
  questions. *(open, design)*
- Claimed rows of sessions no longer alive still count as work in flight and inflate the backlog; ask the census
  whether the owner lives. *(checked 2026-10-05 on 0.7.14: still [P] - fleet/backlog.py from_ledger)*
- Measurements carry no date, so a peak months old reads as today's (spec section 2, "when it was taken").
  *(checked 2026-10-05 on 0.7.14: still [P] - onboard/firstrun.py measure_peaks)*
- TODO parsing: a heading such as "Done criteria" swallows its items, a fence opened by ``` is closed by ~~~ or a
  shorter fence, `1. [ ]` items are not counted. *(checked 2026-10-05 on 0.7.14: still [P] - fleet/backlog.py
  todo_items)*
- The `ps` path of the peak sampler (no /proc, macOS) is untested on a real Mac. *(measure)*
- cgroup v1: only the leaf's limit is read (a parent slice's is missed; `hierarchical_memory_limit` would give it),
  and a combined `cpu,memory` mount without a `memory` link is not found. The cgroup read is flotilla's own, while
  seats run wherever the Claude daemon lives. *(checked 2026-10-05 on 0.7.14: still [P] - core/resources.py
  _cgroup_limits)*
- Two orchestrators at once (one interactive, one background, or two background) count as one seat or none; the
  ceiling may overshoot by one. *(checked 2026-10-05 on 0.7.14: still [P] - fleet/sizing.py _leader_kind)*
- A torn `machine.toml` drops a configured `lane_capacity` to 1 without saying so in `flotilla lane`. *(checked
  2026-10-05 on 0.7.14: still [P] - lane/commands.py capacity)*
- The peak's `ru_maxrss` backstop holds the largest single child, not a group's sum (pytest-xdist workers, node and
  its browser). *(checked 2026-10-05 on 0.7.14: still [P] - lane/peak.py children_max_rss_kb)*
- The pace note says "too few samples" when three or more rounds have a median of 0. *(checked 2026-10-05 on
  0.7.14: still [P] - fleet/pace.py pace)*

## From the review of 0.7.13 (2026-10-04)

- A seat seen in the census only after spawn's wait never gets its session id recorded, and a helper still running after
  `helper done` has a released row: both keep the dialog with the census down. A hook-side fill-in - on a census hit, an
  open reserved row of that name with no id gets it - would cover both. *(checked 2026-10-04 on 0.7.13: still [P] -
  fleet/spawn.py raise_seat)*
- The "Legal from here" refusal lists every annotation, `launched` included, which only spawn records; it should list
  the moves a post may make. *(checked 2026-10-04 on 0.7.13: still [P] - ledger/transitions.py next_state)*

## From the review of 0.7.12 (2026-10-04)

- The refusal texts flotilla prints itself were spot-checked, not audited: each one a person must resolve should name
  what to run. *(checked 2026-10-04 on 0.7.12: open, audit)*

## From the review of 0.7.11 (2026-10-04)

- `flotilla spawn --fill` stops at the first one-copy refusal under the lock and raises none of the posts after it;
  skipping that post with a warning would raise the rest. *(checked 2026-10-04 on 0.7.11: still [P] - fleet/spawn.py
  spawn)*
- A `queue_command` that exits 126 (not executable) is waited on like a passing failure; like 127 it will not change by
  waiting. A clock that steps back keeps a seat row "fresh" past the 600 s launch window. *(checked 2026-10-04 on
  0.7.11: still [P] - lane/machine.py _queue, fleet/spawn.py LAUNCH_WINDOW)*

## From the twosuns update to 0.7.1 (2026-10-03)


## From the broker final review (2026-09-27)

- `status: waiting` may also mean prompts other than permissions; a waiting session is reported only when it holds a
  ledger move. *(checked 2026-10-04 on 0.7.8: still [P] - watch/fleet.py:38-39)*
- A background orchestrator counts as live: questions then wait the full budget instead of naming `claude attach`.
  *(checked 2026-10-04 on 0.7.8: still [P] - broker/decide.py:211-212)*
- The skill asks the model to read a typed answer as allow or deny; make "Other" always a deny with the words. *(checked
  2026-10-04 on 0.7.8: still [P] - skills/permit/SKILL.md:16)*
- Measure live: whether Claude Code kills a hook's process when its session is stopped (the hook now also withdraws when
  its parent changes). *(checked 2026-10-04 on 0.7.8: measure)*

## From the broker plan (2026-09-27)

- Measure with a live fleet: an orchestrator woken by `flotilla permit next --wait` finishing in the background, and the
  AskUserQuestion round trip back to a background session's hook. *(checked 2026-10-04 on 0.7.8: measure)*

## From the guards final review (2026-09-27)

- `flotilla guard check` with an override in the command writes a bypass record although nothing ran; a real override
  push is recorded twice (command guard and pre-push). Record only when the command runs. *(checked 2026-10-04 on 0.7.8:
  still [P] - guards/push.py:496-499, 546-548)*
- A session whose working directory is not onboarded does not judge `git -C <onboarded> push` (spec 8: hooks are silent
  outside a project); the pre-push hook covers it. Plan decision 8's "another flotilla project" holds only from inside
  one. *(checked 2026-10-04 on 0.7.8: unclear, low - the pre-push hook covers it - guards/run.py:35-38)*
- The line-number guard refuses addresses that cannot go stale: GNU `0,/re/`, `1,/re/`, `1i\`, and a digit after `;`
  inside a substitution with spaces (`s/foo; 2 items/bar/`). *(checked 2026-10-04 on 0.7.8: partly fixed: `0,/re/` and
  `1,/re/` pass; `1i\` and `s/foo; 2 items/bar/` still refused [P] - guards/line_edit.py:243)*
- `git checkout -- $(git diff --name-only)` passes silently; paths from a substitution are unknown and should warn.
  *(checked 2026-10-04 on 0.7.8: still [B] - inside the stated ceiling (`$( )`); a warning is what is asked -
  guards/revert.py:67-91)*
- `diff --numstat` without `-z`: a quoted non-ASCII path never matches a reservation pattern. *(checked 2026-10-04 on
  0.7.8: still [B] - guards/reserve.py:38-42)*
- The line printed for `core.hooksPath` has no missing-link branch, so a pre-commit added there fails closed. *(checked
  2026-10-04 on 0.7.8: still [B] - guards/githooks.py:204-205)*
- The budget test asserts 1 s, not ~200 ms (measured: 0.0 / 24 / 52 ms in process, ~89 ms process start). *(checked
  2026-10-04 on 0.7.8: still [P] - tests/test_guards_run.py:89)*
- `guard install` ignores a failed `refresh_link`. *(checked 2026-10-04 on 0.7.8: still [P] - guards/githooks.py:236)*

## From the guards plan (2026-09-27)


## From the watchers final review (2026-09-27)

- The hook-level throttle test does not guard the digest's age exclusion: both renders read "(2 h)"; cross an hour.
  *(checked 2026-10-04 on 0.7.8: still [P] - tests/test_hooks.py:162-165)*
- Every Stop and prompt pays for the census and the ledger before deciding; check the Stop payload before gathering, and
  read the census once at session start (the doctor reads it too). *(checked 2026-10-04 on 0.7.8: still [P] -
  hooks.py:70)*
- A census error whose text varies (stderr) re-prints on every prompt; hook exceptions are not throttled at all.
  *(checked 2026-10-04 on 0.7.8: still [P] - hooks.py:173, 74-79)*
- `HOOK_CHECK_TIMEOUT` is defined in both `hooks.py` and `watch/context.py`; no test pins the 10 s prompt/stop budget.
  *(checked 2026-10-04 on 0.7.8: still [P] - hooks.py:22, watch/context.py:14)*
- The claimed-work summary line is appended last and is the first cut past 20 items. *(checked 2026-10-04 on 0.7.8:
  still [P] - watch/render.py:43-48)*
- The breaks log is never pruned; `stop_hook_active` set by another plugin's Stop hook is recorded as a flotilla break.
  *(checked 2026-10-04 on 0.7.8: still [P] - watch/fleet.py:205-206, watch/guard.py:39)*
- `watch --once` has no test for the ledger-could-not-be-read exit 2; an exception in its gather exits 1, not 2.
  *(checked 2026-10-04 on 0.7.8: still [P] - watch/commands.py:42)*
- `flotilla/ledger/events.py` lacks a final newline. *(checked 2026-10-04 on 0.7.8: still [P])*

## From the watchers plan (2026-09-27)


## From the lane final review (2026-09-27)

- A `run` annotation resets `updated_at`, so `status --stalled` never shows a row whose owner keeps re-running tests.
  *(checked 2026-10-04 on 0.7.8: still [P] - ledger/model.py:117)*
- A hand holder's own `pytest` counts twice (the booking and a foreign run) when `lane_capacity` is 2 or more. *(checked
  2026-10-04 on 0.7.8: still [P] - fails closed - lane/book.py:108, lane/acquire.py:66)*
- An invalid `lane_capacity` in `machine.toml` falls back to 1 silently. *(checked 2026-10-04 on 0.7.8: still [P] -
  lane/commands.py:28-30)*

## From the spawn and posts final review (2026-09-27)

- A dry run without the census skips the one-copy check, and its warning speaks only of names. *(checked 2026-10-04 on
  0.7.8: still [P] - fleet/commands.py:164-167)*
- The dry-run command is not shell-quoted; print it with `shlex.join`. *(checked 2026-10-04 on 0.7.8: still [P] -
  fleet/commands.py:186-188)*
- `fleet.model = "reviewer-strongest"` silently overrides a post's explicit `model`. *(checked 2026-10-04 on 0.7.8:
  still [P] - fleet/launch.py:81-83)*
- Every session announces itself to every peer at start: N·(N−1) letters that carry only state (spec-level). *(checked
  2026-10-04 on 0.7.8: open, design)*
- `git status --porcelain` counts an untracked directory as one entry, so "1 uncommitted file" can undercount. *(checked
  2026-10-04 on 0.7.8: still [P] - fleet/retire.py:92-94, fleet/helpers.py:91, ledger/core.py:318)*
- Measure on a live background session (the billed e2e smoke): spawning from inside a session's Bash, and
  `${CLAUDE_PLUGIN_ROOT}` in `allowed-tools`. (`claude stop <id>` removes the session from the census at once; by name
  it fails: entry 70; `retire` already passes the id.) *(checked 2026-10-04 on 0.7.8: measure)*

## From the ledger part B final review (2026-09-26)

- A `pre-` script that calls a `flotilla work` move deadlocks on the ledger lock until the 30 s timeout; say so in
  `docs/events/schema.json`'s documentation. *(checked 2026-10-04 on 0.7.8: still [P] - docs only -
  docs/events/schema.json)*
- An accepted row the brief holds back still counts as read when its commits ride in with another row's land. *(checked
  2026-10-04 on 0.7.8: still [B] - its read commits land before what it depends on; nothing unread reaches trunk -
  ledger/batch.py:158-163, ledger/report.py:36-45)*

## From the ledger part A final review (2026-09-24)

- An unknown base is recorded as `""`; record it as unknown, so a reader can tell "not asked" from "git could not say".
  *(checked 2026-10-04 on 0.7.8: still [P] - ledger/core.py:227)*
- `_stacked_on` reads `None` (git could not answer) as "not stacked"; it should be a third outcome. *(checked 2026-10-04
  on 0.7.8: still [P] - ledger/handover.py:63-72)*
- The `fetch` in `tree cut` fails silently; say so, and name the trunk revision the tree was cut from. *(checked
  2026-10-04 on 0.7.8: still [P] - ledger/tree.py:41-44, 98-101)*
- `--reviewed` accepts a branch name; require a revision (a sha prefix), since a name moves. *(checked 2026-10-04 on
  0.7.8: still [P] - ledger/reading.py:98)*
- An unknown owner post counts as "not main" in the transition rules; refuse instead. *(checked 2026-10-04 on 0.7.8:
  still [P] - errs towards review - ledger/core.py:62-67)*
- `--agreed-by` on `moved` is only the owner's word; part B may ask the named reader. *(checked 2026-10-04 on 0.7.8:
  open, design)*
- Test gaps: `assign` with the census unreachable; `hand`/`moved` with an unresolvable tip; malformed `may` forms in a
  post; the race test does not assert the holder's name; `--requires` resolving a released row by id. *(checked
  2026-10-04 on 0.7.8: still [P] - all five gaps)*
- Not judged by the reviewer, open for part B: the order of checks inside a move; separate clones sharing one ledger;
  `landed` has no release edge; a purpose with no tiers counts as valid; the strict frontmatter parser; letters are
  printed, not delivered. *(checked 2026-10-04 on 0.7.8: open, design - `landed` has a release edge now
  (ledger/transitions.py:25); separate clones: measure)*

## From the onboarding final review (2026-09-23)

- The machine profile does not record arch, git version or Claude Code version. *(checked 2026-10-04 on 0.7.8: still [P]
  - onboard/machine.py:67-87)*
- The questionnaire does not ask whether two full test runs fit on the machine at once. *(checked 2026-10-04 on 0.7.8:
  still [P])*
- The onboard skill does not state that background isolation stays on. *(checked 2026-10-04 on 0.7.8: still [P])*

## Done

- 0.10.3: rig measurements fixed for packing (CPU of helpers, the machine's GPU peak, shared marked); a run's leftovers end with it; gone runs swept once; sampler bound to its run; atomic --get placement; rename and directory-put gate fixes; comments on rig lines are data.
- 0.10.0: rig stage 2b - `rig run`: runs share a machine, are measured there, and bring artifacts back (decision 235).
- 0.9.0: the person guard refuses a glob or a variable in a command word (`flotilla r?g open`, `$F rig open`).
- 0.9.0: crontab lines are split on newlines only, so CR and form feed survive a rewrite.
- 0.9.0: the launcher's last resort keeps its line unless a service was listed.
- 0.9.0: a reap that steps aside on the lock is not counted as a launcher miss.
- 0.9.0: rig stage 2a - `rig open/close/allow-image/up`, the watchdog, rig lines in `fleet` (decision 234).
- 0.7.16: a receipt's setup and its red and timed-out tiers are measured; a stopped run is recorded as killed; a
  run inside a booking is recorded beside it; an estimate borrows its duration from a coarser step.
- 0.7.13: a seat is known by its session id, kept on its post row, and refused rather than left hanging when the census
  is down (decision 230)
- 0.7.13: `flotilla doctor` names each post older than the shipped template (decision 230)
- 0.7.12: a refusal the person can lift reaches them as a session's proposal with its check; the orchestrator labels and
  judges it (decision 229)
- 0.7.11: a failed second CPU sample no longer drops a live run (decision 228)
- 0.7.11: the CI queue is one call over a hundred runs, stopping at the first on this machine (decision 228)
- 0.7.11: a `queue_command` that is not text or not found is a lasting unknown (decision 228)
- 0.7.11: the broker answers once where hard links are refused (decision 228)
- 0.7.11: pid 0 is not alive; a reused pid is still bounded by the question's deadline (decision 228)
- 0.7.11: staged files go on a failed write and are swept with age (decision 228)
- 0.7.11: two spawns at once raise one copy of a one-copy post (decision 228)
- 0.7.10: GitHub that could not be asked is "not yet", not "refused" (decision 227)
- 0.7.10: a failed fetch is said, and "could not tell" is not "does not have" (decision 227)
- 0.7.10: a PR closed without merging names release and a new claim (decision 227)
- 0.7.10: `vanished` spares work that reached trunk (decision 227)
- 0.7.10: `reconcile` exits by its worst line and takes --skip-event (decision 227)
- 0.7.10: the brief holds back an accepted row whose branch git cannot find (decision 227)
- 0.7.10: `receipt show` exits 1 when no tiered purpose holds, and takes --purpose (decision 227)
- 0.7.9: `git switch -f` / `--discard-changes` meet the revert guard (decision 226)
- 0.7.9: a guard hook that fails before judging refuses a push (decision 226)
- 0.7.9: a receipt vouches only for the revision and purpose written in it (decision 226)
- 0.7.9: a symlinked workflow no longer refuses every push as a drift (decision 226)
- 0.7.9 (review): `git checkout -qf` / `--for` meet the revert guard; a non-ASCII workflow name gives both sides one
  digest; a guard hook that fails before flotilla loads (old Python, import error) still refuses a push.
- 0.7.9: `gh pr merge [<pr>] --disable-auto` asks no receipt; a merge into another base still does, by decision - its
  base cannot be pinned (decision 226)
- 0.7.7: `flotilla doctor` checks the shape of the Claude Code records flotilla reads (decision 225; was "It leans on
  Claude Code internals").
- 0.7.6: a wait whose object is gone no longer silences the watch, and work piled on one seat is named (decision 224).
  The case first recorded for it did not happen: a watcher's `tail -12` cut the first row of `status`.
- recorded: the measurements stand in decisions log entry 70; the 1 s test bound is the open item above. (The guard's
  time, measured on this machine: 0.0 ms for a plain command, 24 ms for a read o...)
- found moot on 0.7.8: README and USER_MANUAL no longer mention `stop_guard`. (The README says `[watch] stop_guard =
  false` in `.flotilla/project.toml`; the profile is r...)
- found fixed on 0.7.8: watch/fleet.py:36-44 reads `status`, and `waiting` is named a prompt. (The watchers read a
  background session's `state` only; the census also gives `status` (`bu...)
- found fixed on 0.7.8: fleet/launch.py:65-68 refuses a mode outside the allowed width. (A post file on trunk can set
  `permission_mode: bypassPermissions`; consider a profile opt-...)
- found fixed on 0.7.8: templates/posts/sender.md:25-28 lands from its own tree (`git -C <tree> push origin
  HEAD:<trunk>`); the push guard's acceptance of that form is not verified. (In direct-push mode the sender cannot check
  out trunk in its home tree (trunk is checked o...)
- found fixed on 0.7.8: doctor.py and spawn's plan (claude_state.setup_problems) say so before launching. (`claude --bg`
  refuses a directory whose trust was never accepted, and trust is not inherit...)
