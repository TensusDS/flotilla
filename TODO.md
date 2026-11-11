# TODO

Deferred findings, kept here until a plan takes them. Each names where it came from.

**How this file is kept.** Every open item ends with when it was last checked against the code, on which version,
with what verdict and where: `*(checked <date> on <version>: still [S|B|P] - <file:line>)*` (S a guard lets
something through, B a bug a user meets, P polish, tests or docs), or `open, design`, or `measure` (needs a live
run). A later triage re-checks only items whose named files changed since that version. An item that is done -
fixed by a release, or found fixed - leaves its section for **Done** at the bottom, one line with the version.

## The community plugin directory refuses flotilla (since about 2026-11-05) - waiting on Anthropic, not a fix

Submitted to Anthropic's community plugin directory on 2026-10-04. 0.29.2 passed the directory policy check; every
version since 0.29.3 is refused. Nothing flotilla changed caused it: `hooks/` and the rest are the same in 0.29.2
and 0.31.0 apart from the version line. Around 2026-11-05 the directory began to accept local code only from
"Verified organizations", and no public page says what that is or how to become one (the open question, with a
dozen other plugins refused the same week and no answer from Anthropic as of 2026-11-09:
https://github.com/anthropics/claude-plugins-official/issues/7024; domain verification reportedly does not help).

The four refusals, all "accepted only from Verified organizations":

- `LOCAL_CODE_HOOK_COMMAND` - hooks of type command, and of any type: remove every hook.
- `bin/` - remove the folder.
- `allowed-tools` beyond plain Read, Grep, Glob and LS, with no `${...}` path: cut every list to those four.
- files outside Markdown, text, JSON, YAML and images, or starting with `#!`: remove them (every `.py` file).

Meeting them leaves Markdown skills that cannot run flotilla - no guards, no ledger, no lane - so the person's
ruling (2026-11-09) is **not to chase the policy**: flotilla ships from its own marketplace as before, and the
submission waits until Anthropic says how an organization becomes Verified. A "lite" directory edition was
weighed and set aside for the same reason. Do not remove hooks or code to pass the check, and do not install hooks
into the user's settings from a command to get around it. Checked against the checker for real problems: no file
is non-UTF-8 or holds a NUL byte, and none of the four points at something to fix outside the directory.
*(checked 2026-11-09 on 0.31.0: open, waiting on Anthropic - hooks/hooks.json, bin/, skills/*/SKILL.md)*

## From the reviews of 0.31.2 (2026-11-10, what reaches the guards and what they answer on a failure)

Four review rounds. What is left belongs to one cause: the guards read a command with a matcher and bash reads it
with a parser, and each new reading closed the cases found and left a rarer one. The fix is one parse of the
command that every guard shares, not one more reading.

- **A continued door on a line that opens a heredoc, after a quoted heredoc whose body ends a line in a backslash**
  (`cat <<'EOF'`, `keep\`, `EOF`, `cat <<EOF; r\`, `m -rf <seat>`, `EOF`) is seen by none of the three readings
  (typed, bodies dropped then lines joined, lines joined then bodies dropped). Bash joins a continued line before it
  looks for a heredoc, joins inside an unquoted body and never inside a quoted one; `segments` does a blind replace.
  *(checked 2026-11-10 on 0.31.2: open, needs the shared parse - flotilla/guards/shell.py segments)*
- **A continued push with the override in the environment records the override twice**: the push guard judges the
  typed and the joined reading, and `evaluate` says a doubled finding once but the record is written by the guard.
  The push guard's git and origin calls run twice for such a line too. *(checked 2026-11-10 on 0.31.2: P -
  flotilla/guards/run.py evaluate, flotilla/guards/push.py)*
- **`$A""rig open` on the judged path, with nothing in the line naming flotilla**: the hook's text check passes it
  on since 0.31.2, and the person guard then says nothing - a variable program whose value ends in a blank, inside
  the stated ceiling. On a failure path the same line is asked of the person. *(checked 2026-11-10 on 0.31.2: P -
  flotilla/guards/person.py check)*
- **A variable program with the bare word `attention` as an argument is refused** (`$GREP attention notes.md`), the
  trade already made for `rig` and `work`. *(checked 2026-11-10 on 0.31.2: P - flotilla/guards/person.py check)*
- **The person guard refuses a line whose words hold an extglob sign inside quotes**, for any word whose basename is
  `flotilla` - a path handed to `grep` is enough (`grep -n "x*(" /src/flotilla`): `EXPANDS` is looked for as text,
  quotes not read. A false refusal, met while reading the guards. *(checked 2026-11-10 on 0.31.2: open -
  flotilla/guards/person.py check)*

## From the reviews of 0.31.4 (2026-11-11, a push nothing judged has no override)

Two review rounds; what is left never lets through what a guard refuses - it only refuses with a vaguer word.

- **A timed-out `git ls-remote` closes a feature-branch push that a failed one lets pass**: `origin_trunk` catches
  `Unknown` alone around its first ask of origin, so a timeout leaves the trunk untold and the push is closed with no
  override, while a non-zero exit falls back on the tree's trunk and the branch push passes. A timeout read as
  `Unknown` there would decide as the docstring says. *(checked 2026-11-11 on 0.31.4: open - flotilla/guards/push.py
  origin_trunk)*
- **The closed door's words when origin could not be asked**: it always says "could not be read from origin", also
  when `git worktree list` fell (`_own_repository`), and for a timeout gives no way forward ("run it again", or the
  person's way); the comment on the `elif where is None` branch now also covers doors that land nothing. *(checked
  2026-11-11 on 0.31.4: cosmetic - flotilla/guards/push.py guard)*
- **`_when_untold` reads `guards` outside a try**: a `guards = "x"` on origin raises out of `guard()`; it is refused
  by the failure rule, with no override, so only the words differ. *(checked 2026-11-11 on 0.31.4: cosmetic -
  flotilla/guards/push.py _when_untold)*
- **`guard status` cannot tell an old pre-push hook from a new one**: it looks for the marker line only, so a hook
  installed before 0.31.4 keeps letting `FLOTILLA_GATE_OVERRIDE` through when flotilla is missing until `flotilla
  guard install` runs again (the changelog says so). *(checked 2026-11-11 on 0.31.4: open - flotilla/guards/githooks.py
  status)*
- **The tests of a landing door whose origin could not be asked run with no caller**: `who=None` and no fleet whose
  merges the person authorizes, the shape the hole had in production; the code is unconditional. Also the README's
  "at the head of a push" leaves out the override read from the environment. *(checked 2026-11-11 on 0.31.4:
  cosmetic - tests/test_guards_push.py, README.md push receipt)*

## From the reviews of 0.31.3 (2026-11-11, a shell's script names its lane run; tiers that read the history)

Three review rounds; what is left changes only how well a run is estimated, never what a receipt or a guard allows.

- **A shell's script is found by a reading of its options, not by bash's parser**: `bash - x.sh` is named `bash -`
  (a bare `-` ends the options, as `--` does); `bash -version x.sh` and `-help` run no script but are named by it;
  fish's options that take a value (`-C`, `-d`, `--init-command`) name the value; `debug` is missing from
  SHELL_LONG, and the one-dash long options are read for every shell, though only bash has them.
  *(checked 2026-11-11 on 0.31.3: P - flotilla/lane/signature.py _shell_script)*
- **A temporary script's made-up name folds only for the default patterns**: a mktemp template (`probe.XXXXXX.sh`)
  or a tempfile prefix (`probek3j_9x2a.py`) makes a new signature every run, and a real name of the default shape
  (`tmp_rebuild.sh`, `tmp.background.sh`) folds with the made-up ones. The program step is not folded at all, which
  is harmless while the normalised step answers first. *(checked 2026-11-11 on 0.31.3: P -
  flotilla/lane/signature.py _temporary, RANDOM_STEM)*
- **A heavy run's wait reason still carries the room of the moment** when free memory is below the heavy room
  (`min(free, room)`), so its waiter reprints it as free memory moves - as before 0.31.3. *(checked 2026-11-11 on
  0.31.3: P - flotilla/lane/policy.py _fits)*

## From the reviews of 0.31.1 (2026-11-10, adopt follows the heir, the base after a rebase)

Deferred as cosmetic or as consistent with what `land` and the push guard already trust.

- **Where the shown base quietly stays the recorded one** (fails safe, the reader reads more): a row whose commits
  reached trunk with no later ledger move (a person merged the PR, `ship` not yet run), a direct-mode row landed
  locally before the claim and pushed after it, a fleet with more than 50 rows moved since the claim, or more than
  300 commits skipped. Whether the field meets these often enough to matter is not measured. *(open, measure -
  flotilla/ledger/reading.py `_read_by_others`)*
- **The accounting counts a queued row without a verdict as read** (`review.depth = none`, or `main-only` for a
  non-main post): its whole range is "read", so commits of another row stacked under it, once on trunk, are skipped
  in that row's shown base. `land` and the push guard trust it the same way; `rules_tree` is not passed either, as
  in `land`. *(checked 2026-11-10 on 0.31.1: P, within land's trust - flotilla/ledger/batch.py Accounting)*
- **Test gaps:** `test_only_rows_moved_since_the_claim_are_asked_about_a_rebase` leaves out a released row, which
  the accounting already asked nothing of, so it proves the filter but not the saving on closed reviewed rows; the
  50-row cap has no test. *(checked 2026-11-10 on 0.31.1: P - tests/test_ledger_cli.py)*
- **A suite run through the lane hides the lane from its tests, not the tests from the lane** (conftest
  `a_suite_in_the_lane_is_not_a_nested_run`). Real `SCOPE` slots a test makes start beside the suite's scope, and
  the tests' children no longer carry the suite's booking, so where the lane freezes by signal (macOS, no user
  systemd) it does not stop them with the suite, nor find detached ones as its leftovers. And the lane may still
  freeze or cap the suite mid-test: a timing test that fails only in the lane suspects that first. Related to the
  leftover `flotilla-lane-*.scope` units above. *(checked 2026-11-10 on 0.31.1: P - tests/conftest.py)*

## From the field test of the lane daemon, batches 1-3 (2026-11-07..08, 0.30.1 and lane/daemon c5baaff)

Two demo fleets on one machine (8 cores, 15 GB), batch 1 on 0.30.1, then batch 1 again and batches 2-3 on the lane
daemon. The findings the person took into work are fixed on their own branches; these two were deferred.

- **A run held at once may print no "waits because" line.** The main's push receipt for one row printed none; whether
  a run admitted without waiting should say so, or the line went missing, was not checked. *(open, measure)*
- **Claude Code `plugin update` keeps a stale cache of a same-version local plugin** (development setup, not
  flotilla): a reinstall was needed to load the changed code. Worth a line in the development notes. *(open, P)*
- **Reconcile's line openings are kept by hand in four places** (`ledger/delivery.py` `reconcile` f-strings,
  `ledger/commands.py` `LINE_OPENINGS`, `reconcile_exit` and the `shipped ` parse in `run_ledger_command`): a
  reworded line would make its branch appear twice in the output, and no test depends on `_subject`'s answer for
  every line shape delivery produces (reviews of f762170 and 4cd760c). One owner of the openings, or a test over
  every line delivery produces. *(checked 2026-11-09 on lane/daemon: still P -
  flotilla/ledger/commands.py `_subject`)*
- **A directory marketplace loads from its folder, whatever the install record says** (`readFromFolder` in
  `claude plugin list`; measured 2026-11-09 with `claude -p --debug`, Claude Code 2.1.319): `loads_all` reads the
  record's version, so for a directory marketplace the release a seat loads can differ from the one named. Predates
  item 5. *(open, measure - flotilla/core/claude_state.py `loads_all`)*
- **With no `enabledPlugins` naming flotilla anywhere, Claude Code loads nothing**, while `loads_all` falls back
  to the install records and names a release (measured 2026-11-09, Claude Code 2.1.319, review of 7e6a9b8, F2).
  Whether older Claude Code enabled an installed plugin implicitly was not measured, so the fallback stays.
  *(open, measure - flotilla/core/claude_state.py `loads_all`)*
- **A seat held on a start-up question, after it is answered or declined** (review of 14c5ff8): measured only
  while the dialog stands. Whether a seat whose dialog was declined and which then exited keeps showing the dialog
  in `claude logs` - which would read it as held for good, and keep its tree from the collector - needs a real
  Claude Code. `/flotilla:update`'s report also reads the plain census: a project whose every seat is held shows no
  fleet, so `--stop`/`--defer` is not asked there; such a seat has loaded no release yet. Other readers of the
  plain census still read a held seat as gone (review of 3f5bd79): a handover wait on one is refused or flagged
  (ledger/handover.py `_whom`, `_gone`), and ledger/outside.py's liveness; `adopt` of its reserved row is refused
  anyway. A `--recommended --dry-run` asks the fleet's census up to five times, each paying a second `claude
  agents` and a `claude logs` per held seat; the `--recommended` call into sizing has no test of its own.
  *(open, measure - flotilla/core/census.py `held_at_start`, flotilla/update.py `report`)*
- **The test suite can leave empty `flotilla-lane-b1-*.scope` units behind.** After the field test eight such
  user units stood with no process in them and were stopped by hand (2026-11-09). `b1` is a booking id only the
  tests use, so they came from suite runs, not from the fleets; a whole lane test run, watched, left none. Several
  suites were killed mid-run that week; whether a killed run is what leaves them was not checked.
  *(open, measure - flotilla/lane/freeze.py `UNIT_PREFIX` scope, its teardown)*
- **`/flotilla:update` still reads ownership by record in two places** (review of dfa07ea): after a project's own
  record is updated, `apply` drops its deferral although the project may ride the user's copy, still held - the
  "update waits" reminder is lost, no copy moves (`flotilla/update.py` `apply`, the project branch); and `user_held`
  counts only projects with a working fleet, so `--defer` of a project with no fleet riding the user's copy says
  "its fleet keeps working" while the user's copy is updated (`plan`; older, extended by dfa07ea to a project whose
  own record follows the user's). *(checked 2026-11-09 on lane/daemon: still B, low - flotilla/update.py)*
- **Wording of the copy a session loads** (review of dfa07ea): `rides_user_copy` answers True when no copy loads, so
  a fleet whose enabled id has no record here is shown "(on the user's copy)" and its docstring overstates; the
  `copy_in_use` docstring still calls the rule unmeasured, and its fix line "bring the project's install up" does
  not make the project's copy load when the user's record comes first. *(checked 2026-11-09 on lane/daemon: still
  P - flotilla/core/claude_state.py)*

## From the field test of two demo fleets on one lane (2026-11-06, 0.29.3)

Two four-seat fleets (demo-a, demo-b), five small rows each, one lane (`lane_capacity = 1`, `lane_share =
"fair"`, 8 cores, 15 GB), watched for 9 h with the lane journal, a sampler and the orchestrators asked every 20 min.
Both backlogs landed (b in ~1 h 20 min, a in ~1 h 45 min); no stalls, no orphaned rows, no wrong waits; load stayed
below 4.4 of 8, 9 GB free, the fleets' own processes 4-17 % CPU. Whole test, 83 bookings: 13 short runs waited ~76 min
for ~100 s of running, heavy runs waited up to 13 min on an idle machine; no light run turned out heavy, the slot
never stood empty while a heavy run waited, fair alternation held. Neither orchestrator saw the lane cost from its
seat ("normal queueing"); only the journal across both projects shows it. The lane items come first: the person asked
for them first.

**The lane (priority).**

- **Heaviness is counted in seconds, not in what a run takes.** A run over 60 s is heavy whatever it uses, so the one
  slot was held, turn after turn, by 200 s slow-tier runs at about 1 core, 0.23 busy and 73 MB, while 7 cores and 9 GB
  stood free; the slow tier runs about three times a row (handover receipt, reviewer, push receipt). Heavy waits of
  150-798 s each, all of the heavy waiting in the test. The demo's slow tier is a sleep; a network-bound or e2e suite
  looks the same. Asked: a long run that is measured small (cores and memory within the light bounds) takes what the
  heavy runs leave, as a light run does, and only a run that needs the machine takes the slot. *(closed in 0.30.0:
  heavy is a fifth of the memory the lane can give, never a duration - flotilla/lane/policy.py `is_heavy`)*
- **A receipt tier's measurement does not teach `lane run` of the same command.** A tier's sample is kept only under
  `tier:<project>:<name>`; a `lane run` ladder is `exact/norm/prog/project`, so the same `pytest tests/unit -q`,
  measured 0.28 s / 0.78 cores in a receipt four minutes earlier, waited as a 4-core prior run. 7 first runs waited
  161-377 s each (~24 min) for 0.0-0.3 s of running, until each command had its own history. Asked: a tier's sample
  is also filed under its command's `exact`/`norm` signatures. *(cost gone in 0.30.0: a command never measured is
  admitted by the memory free now at this machine's first-run percentile, not as a 4-core prior; the cross-filing
  itself is still not done - flotilla/lane/estimate.py `history`)*
- **A command with a new path is never measured, so always heavy.** The review skill's injected-regression script has
  a per-row path (`.../jobs/<id>/tmp/mutants-r12.sh`); each waited behind the receipts as a "project prior" run: 5
  scripts, ~40 min of waiting for ~55 s of running. Asked: the path normalised in the signature (as hashes and numbers
  already are), or an unmeasured run that is short on probation - started beside the holder and stopped back into the
  queue if it outgrows the bounds. *(closed in 0.30.0: an unmeasured run is admitted by free memory at the first-run
  percentile, and is frozen rather than queued if a higher run needs its room - flotilla/lane/admission.py
  `default_mb`)*
- **A narrowed variant of a measured light command is heavy.** `pytest tests/unit -q -k long_punctuated` waited 596 s
  as heavy while `flotilla lane` printed its estimate as "0 s, 1.02 cores (10 runs (prog match))". A `prog:` match is
  never light on purpose (a full suite with a new flag must not ride on single-file runs, final review of 0.28.0), but
  the cheap direction - a `-k` filter or one test file of a command measured light - pays as the risky one. Asked: a
  variant that only narrows (adds `-k`, a test path, `-x`) of an `exact`/`norm` command measured light is light; and
  `flotilla lane` says why a waiter with a light-looking estimate is not light. *(closed in 0.30.0: light and heavy
  are memory, and `flotilla lane` prints each run's memory and its reason to wait - flotilla/lane/commands.py
  `value_of`)*
- **Push receipts queue behind the reviewer's own runs** (demo-a sender): ~10 min of queueing per push plus the
  200 s slow tier, ~15 min a push. Asked by the seat: a push receipt ranks before a reviewer's checks in the queue.
  Weigh against the items above - with heaviness by resources most of this queue goes away. *(closed in 0.30.0: a
  push receipt's type is 1.0 against a reviewer's 0.5 in the priority - flotilla/lane/policy.py `TYPES`)*
- **`budget for light runs: 0 of 12 cores` reads as "none left"**; it means none in use (the test's watcher misread
  it so). Say "0 of 12 cores in use". *(closed in 0.30.0: the line is gone; the lane says "heavy runs: 0 of 12 cores
  in use" - flotilla/lane/commands.py `_status_by_value`)*

- **A stop by signal whose group leader is gone leaves the rest of its group stopped.** When a dead holder's frozen
  command group has lost its leader, the lane reads it as gone and clears the booking, but other members of the group
  may still stand stopped; `killpg(group, SIGCONT)` would reach them. Found by the review of 0.30.1's fixes.
  *(open: flotilla/lane/book.py `_thaw_command`)*

**The person's road.**

- **A collector pass the classifier refuses has no road to the person.** With `skip_classifier_for_checked = false`
  (the default), auto mode refuses the orchestrator's `fleet clean --daily` ("Irreversible Deletion"). demo-b
  recorded a wait on its reserved row, which reached the inbox; demo-a wrote "run it with `!`" three times in its
  own transcript and recorded nothing, so its inbox said "nothing waits on you" while 10 `send/` branches and a dead
  tree waited. Whether the person hears depends on the seat's choice. Asked: the inbox lists a due pass the
  orchestrator could not run, or the arrangement routes the pass to the person from the start when the opt-in is off.
  *(checked 2026-11-06 on 0.29.3: still B - flotilla/gc/due.py:15, flotilla/watch/fleet.py:246, flotilla/watch/inbox.py)*
- **A standing item is re-announced after every ship.** demo-b's collector wait was re-raised and relayed by the
  sender after each of five ships, and every hook line repeated "2 standing item(s), said before and unchanged" for
  3 h. Once the item is with the person it is said once. *(checked 2026-11-06 on 0.29.3: P - flotilla/hooks.py:275)*
- **`flotilla inbox` cuts an item mid-word** ("by demo-b-orchestrator 2...") and does not say how to answer it;
  `inbox --help` names no answering command; the full text is only in `work show`. *(checked 2026-11-06 on 0.29.3: P
  - flotilla/watch/inbox.py)*
- **No ledger place for a reviewer's side-notes** ("noted, not returned": kelvin below absolute zero, `parse("1e999
  km")` -> inf). Four such notes in demo-b lived only in letters; a decision pending on the person has no row.
  *(open, design)*

**The ledger.**

- **`--requires` / `--after` refuse a delivered row by branch name**: "no such row or open branch" once the row has
  shipped and closed; by id it works. A work order written up front cannot know which rows close first; a delivered
  row should count as satisfied, or the refusal should say "closed - name it by id". Both fleets, both flags.
  *(checked 2026-11-06 on 0.29.3: still B - flotilla/ledger/core.py:225)*
- **The reviewer's lane runs are not recorded**: the skill tells the reviewer to `git switch --detach <tip>`, then
  `lane run --for <branch>` notes "the tree is on no branch" and records nothing - demo-b, every row. demo-a's
  reviewer was recorded (it stayed on the branch). The skill and the lane disagree. *(checked 2026-11-06 on 0.29.3:
  still B - skills/flotilla/SKILL.md:36, flotilla/lane/commands.py:353)*
- **No overlap warning at claim**: parallel rows all appended to one test file; two conflicted, found by the reviewer
  with `git merge-tree`, at the cost of a rebase and a ~200 s re-receipt. *(open, design - flotilla/ledger/gitq.py:82
  can already answer)*
- **The merge of two rows is covered only by the push receipt**: kelvin merged over a trunk that gained cli; no
  verdict read the combination and no vouch was asked. Fine for disjoint files. *(open, design)*
- **No default reader with one reviewer**: `work hand` prints no letter and leaves the reader unnamed until the
  orchestrator assigns; with one reviewer and `review.depth = every` that is a round trip per row. *(open, P)*

**Wording.**

- **The roster says "blocked on <row>" for a `--requires` link** while the seat builds another row (both fleets), and
  **"idle" for a seat whose push receipt waits in the lane** (~6 min; the orchestrator messaged the sender to learn
  it). Say "after <row>" and "in the lane". *(checked 2026-11-06 on 0.29.3: P - flotilla/ledger/report.py:42,
  flotilla/ledger/views.py:131)*
- **`work wait` on the orchestrator's reserved row echoes the row line only**, not that a wait was recorded.
  *(checked 2026-11-06 on 0.29.3: P - flotilla/ledger/commands.py)*
- **`work adopt` prints "not freed: <tree> holds `no branch` now"** - the orchestrator could not tell whether that was a
  problem. *(checked 2026-11-06 on 0.29.3: P - flotilla/ledger/steering.py:48)*
- **"not onboarded: no .flotilla/project.toml" names no remedy** for a branch cut before the onboarding commit (merge
  or rebase trunk); it stalled a row for a day. *(checked 2026-11-06 on 0.29.3: P - flotilla/ledger/commands.py:59,
  94)*
- **The trunk merge reads "Merge branch 'feat/kelvin' into fleet/sender-2"** when the sender merges in its home tree
  and pushes `HEAD:main`; demo-a's sender wrote "into main". Pass `-m "Merge branch '<row>' into <trunk>"`.
  *(checked 2026-11-06 on 0.29.3: P - flotilla/ledger/delivery.py)*

## From alpha' field notes, second part (2026-11-02..04, on 0.24-0.26) and the demo onboarding (2026-11-05, 0.29.2)

- **A superseded shipped row has no honest end** (alpha item 19). Shipped rows whose content a later row replaced
  can leave only by `walked` (untrue) or `broke` (files a fix row nobody wants); `release` and `close` are refused from
  shipped. alpha parked r252, r245, r253, r270 with a hold until the person. Asked: a "superseded by <row|commit>"
  end from shipped, the person's or the orchestrator's move, with why. *(checked 2026-11-06 on 0.29.2: open, design -
  flotilla/ledger/transitions.py, "shipped")*
- **`rig --put/--get` only inside the tree collides with a receipt in the same tree** (alpha item 20). A rig kit
  must be staged in the seat's tree; a queued receipt refuses any untracked file there, so the seat stops its receipt
  to run the kit. Asked: put/get from a seat-private directory outside the tree, or a kit directory the receipt
  ignores. *(checked 2026-11-06 on 0.29.2: still B - flotilla/rig/transfer.py:71, 145)*
- **A receipt tier that runs on the rig** (alpha item 18; the same wish as item 15.5 above): one named tier (a GPU
  smoke) run on a rented machine, its verdict carried back and trusted like a local tier. *(open, design)*
- **`rig run` could name the machine's image beside the profile's** (alpha item 17, a wish): "machine mN on image
  X, the project profile names Y", for a run that meets a warm machine raised by another project. *(open, P)*
- **The publish refusal of a project with no profile could name `/flotilla:organize`** (demo onboarding). *(checked
  2026-11-06 on 0.29.2: P)*
- **A tier that writes an untracked file blocks publish and receipts** (demo onboarding: `uv run` wrote `uv.lock`);
  the way out was `.git/info/exclude`, found by hand. *(checked 2026-11-06 on 0.29.2: B, measure)*
- **A torn `machine.toml` is still rewritten from nothing by `onboard machine`**, losing `lane_share` and every
  `rig_*` key, while `rig enable` names that command as the repair (review of 0.29.3). Refuse as `set_person_keys`
  does, or keep a copy and say so. *(checked 2026-11-06 on 0.29.3: still B - flotilla/onboard/machine.py
  write_machine, flotilla/rig/commands.py `_enable`)*
- **A kept `machine.toml` value the TOML writer cannot write** (a date, a nested array) makes `onboard machine` and
  `set_person_keys` end in a traceback; the file is left intact (review of 0.29.3). *(checked 2026-11-06 on 0.29.3:
  P - flotilla/onboard/tomlw.py)*
- **What the state-directory refusal does not see** (review of 0.29.3): a flotilla child started from a test (it
  inherits the marker and is let through, by design), a write by a direct path, and a person whose real directory is
  `FLOTILLA_STATE_DIR`. The suite's tripwire covers only `machine.toml` and only runs that load tests/conftest.py.
  *(checked 2026-11-06 on 0.29.3: S, within design - flotilla/core/paths.py, tests/conftest.py)*
- **Tier names from detection repeat a command word** ("pytest", "pytest-2") when two tiers run pytest (demo
  onboarding). *(checked 2026-11-06 on 0.29.2: P - flotilla/onboard/detect)*

## From the person-inbox build and its reviews (2026-11-03, 0.26.0)

- **The wrapped check of the person's moves reads text, and its gaps are logged, not chased** (decision 245, the
  same as the organize guard's). Four security reviews of the 0.26.2 check each found more ways around a reader of
  command text; it was fixed for what an overeager model would plausibly type (option spellings, code on standard
  input, a move built in pieces, a path to the CLI, the package imported), and stops there. Known and left: a script
  the session writes and then runs; `eval`; `ssh host '...'`; interpreters not in its list (`deno`, `osascript`); a
  move spelled out of pieces of words (`'appr' + 'ove'`). *(checked 2026-11-03 on 0.26.2: still S, within decision 245
  - flotilla/guards/person.py, wrapped_check)*
- **The person guard reads a quoted heredoc's body lines as shell lines** (`check` with `bodies=True`), so a data line
  that holds a backtick or `$(` and names `flotilla rig` as plain text - a commit message quoting "the `--get` of
  flotilla rig run" - is refused as "this rig line holds a command substitution"; the same line naming `flotilla
  work` passes, and `heredoc_check` itself skips a quoted delimiter (corrected 2026-11-04 by the review of 0.28.0;
  first seen while building 0.26.2). A body is code only when the command it feeds is an interpreter. *(checked 2026-11-03 on 0.26.2: still P - flotilla/guards/person.py, heredoc_check)*
- **The person guard refuses `python3 -c "..."` that names a flotilla path** as "a variable or a substitution" (seen
  by the alpha orchestrator and while building 0.26.0 on heredocs naming the CLI). *(checked 2026-11-03 on 0.26.0:
  still P - flotilla/guards/person.py:303)*
- Deferred from the 0.26.0 reviews (P): an inbox row's age resets on unrelated moves (`inbox.py`, `since` is the
  row's last move); putting a row off loses its first-shown time (the key changes); a wait on a reserved row is listed
  but cannot be put off (`steering.hold`); the hook writes the shown file twice at Stop (`settle`, then `mark`)
  without a lock; a census session waiting at its prompt flickers between samples; the shown file is not under
  `guards/files.py`; the wakeup prompt shape is not measured (CronCreate and `/loop` firings were, on 2.1.317).
  *(checked 2026-11-04 on 0.28.0: still P)*

## From gamma's field notes (2026-11-03..05, on 0.26.0)

- **The collector names an unrelated file as the last copy of an empty one.** `fleet down` printed "the last copy of
  clients/core/.../Gamma.BCD1E3CF.Up2Date is now in .../.remember/logs/hook-errors.log": an empty file matched
  another plugin's empty log in the main checkout. Nothing was moved there; the wording says it was. The rule behind
  it trusts any file of the main checkout as a twin, untracked and ignored ones of other tools included, which can
  be rewritten or removed at any time (flotilla/gc/twins.py `find`). Planned for 0.29.1.
- **`work wait --on "the person"` is refused** once the person has made a move on the ledger (an approve, a take-in):
  `_known_names` counts the mover "the person" as a session, which is not alive (flotilla/ledger/handover.py:150),
  though the flotilla skill and `watch/ask.py` prescribe exactly that form. Planned for 0.29.1.
- **A receipt whose tier exited unknown was accepted for a hand-over** (main 13, r64, d18999b: the seat restarted
  while the run was detached). A run with no recorded exit has no verdict; refuse it for a hand-over, or mark it.
- **The sender cannot remove its own `send/*` merge branches** in a direct-push project: no move deletes them, and one
  was removed with `git branch -D` by hand. Suggested: `work land` deletes the local merge branch once the push is
  confirmed.
- **Post texts say "the person's yes" where `merge_authorized_by = "sender"`;** a seat read it as merges needing the
  person and raised a false alarm. The letters should follow the profile's value.
- **`work reconcile` right after a push said "nothing is queued or landed"** while two rows sat in the send queue.
  If it means "nothing to reconcile with origin", say that, with the count still queued.

## From the review of 0.28.0 against everything found since 0.24.0 (2026-11-04, 0.28.1)

- **The person's inbox does not list the collector's questions** (park or keep a released branch, stop or keep a dead
  seat's processes, letting archived media expire): `inbox._item_entry` maps permission questions, rig requests and
  waiting sessions only; these reach the person through `fleet clean` reports and `/flotilla:doctor`.
  *(open: flotilla/watch/inbox.py)*
- **The guard asks about a script's or a module's arguments that name flotilla** (`python3 -m flotilla work show`,
  `flotilla lane run -- python -m pytest -k flotilla`, flotilla run by path through `python3`): in a background seat
  nobody answers, so the seat stands still. Narrowing it was built and withdrawn in 0.28.1 - three review rounds found
  code handed as text passing as such arguments (a wrapper's interpreter-named word choosing the option table, `fish
  -C`, `python -m timeit`, `node --import`, `perl -M`). If it is done again, it needs an allow-list of exact readable
  forms (flotilla's own CLI by path or `-m flotilla`, with no other interpreter on the line), never a reading of
  options. Low harm today: the shipped skills run `bin/flotilla` directly. *(open: flotilla/guards/person.py
  `wrapped_check`)*
- **An unshare can still let one run start in the same look.** The share is asked at the top of each look of a
  waiting rig run, so an unshare landing between that check and `start_run` lets the run start; the head run can also
  spend up to 100 s raising a machine before it looks again. *(open: flotilla/rig/run.py `take_room`)*
- **A fallen-back light run can lose a freed place to a later heavy run:** heavy waiters skip light waiters for order,
  so who wins a place freed while both wait is wake timing. *(open: flotilla/lane/book.py `_path`, the `lights` set)*
- **Untested though they hold:** the orchestrator's prompt still carries `collector` and `disk` after 0.26.0's
  filter (`hooks._prompt`); `tree cut` and `tree switch` keep every repeated `--requires` and `--after`.
  *(open: tests)*

## From the final review of the lane's light runs (2026-11-04, 0.28.0)

- **One light run that does not fit holds up every light run behind it while a heavy run holds the lane.** Light
  runs go strictly by arrival (pinned by a test); since 0.28.1 a refused light run falls back to the count, so on a
  free lane nobody is stranded, but beside a heavy holder a 5 GB light run waiting for memory keeps one-file tests
  behind it, and an expired wait keeping its place makes the holdup survive retries. Decide on purpose whether a
  later light run that fits may pass. *(open: flotilla/lane/book.py `_light`)*

## From the final review of the rig queue, part A (2026-11-03, 0.27.0)

- **The ceiling exception can outlast one pass when the service misbehaves.** A draining machine whose destroy the
  service took stays out of the count until it is unlisted or goes STUCK - up to three passes (~15 min) if the
  service keeps listing it; when it then goes STUCK, the next session's machine already exists, so ceiling + 1
  machines bill until someone acts on the STUCK alarm. Follows from the design's rule (section A5).
  *(open: flotilla/rig/journal.py `_counts`)*
- **`test_a_run_stopped_before_its_command_fetches_nothing` cannot tell "fetch skipped" from "fetch found
  nothing"**, and its SIGTERM races the command's start. It guards the 120 s hang only.
  *(open: tests/test_rig_run.py)*

## From two field fleets, alpha and gamma (2026-10-31..11-01, reported on 0.21.0 and 0.23.2)

Order, the person's call (2026-11-01): the lane's fixes first (0.24.1), then the lane's modes (0.25.0), then the
rig queue (0.26.0).

Sources: the alpha orchestrator's feedback (`alpha-keep/flotilla-feedback-2026-11-01.md`, sixteen items; 13-16
added 2026-11-02 and checked that day on 0.24.1) and the gamma orchestrator's lane report (lane
journal figures: runs under 60 s waited a median 30.6 min for a median 6.8 s run). Checked against main at a50daae
(0.24.0 code) where a file is named.

**The lane**
- **Budget admission and overtaking for every run, if light runs beside one heavy run (0.28.0) are not enough** - stages 2-3 of the lane spec (budget admission by cores and memory, the head's
  shadow, a starvation guard, printed reasons), opt-in per machine, FIFO the default, gated by `RULE` for mixed
  versions. The spec's precondition (a week of field measurements) is met: 24 000 journal lines from two projects.
  *(open, design: lane spec stages 2-3)*
- **Mode 2's `slots` variant** - each project with live seats guaranteed part of the capacity, an idle slot lent only
  within the memory floor. 0.25.0 built `fair`; the person's call (2026-11-02): `slots` later, if needed. *(open,
  design: docs/specs/2026-11-02-project-share-design.md)*
- **Seats run tests and simulations outside the lane despite the guard's warning** (load 18-23 on 8 cores, smoke
  timeouts in five receipts on 10-31). The person's call (2026-11-01): the guard keeps warning, and such a run is
  counted as the seat's own booking - it takes a place in the lane, attributed to the seat, instead of being an
  anonymous foreign run. *(open, design: flotilla/lane, the Bash guard)*
- **Heavy tiers starve each other at a capacity above 1** (alpha item 13: two receipts' software-rendered smoke
  tiers at once, load 17-26 on 8 cores; five handovers blocked on 11-01 by smoke reds from load, not code, and the
  only remedy was capacity 1, which slows every light run). Asked: a tier's cost - declared (`cores`, `exclusive`) or
  measured (CPU-seconds per wall-second, which the lane records already) - and admission by the sum of costs against
  the machine's cores, unbooked runs counted by their measured load; a tier that times out under a load far above
  the core count recorded as starved, not red. The admission is mode 1 below (the lane spec's budget by cores and
  memory); the per-tier declaration and the starved verdict are not in the spec yet. 0.28.0 removes the cause at
  capacity 1: light runs fill what one heavy run leaves, so capacity 2 is no longer needed for short runs to move.
  Left: the declared cost and the starved verdict, if capacity above 1 comes back. *(open, design: lane spec
  stages 2-3, flotilla/lane/run.py)*
- **The orchestrator cannot reorder the lane, and a waiter cannot see when it will start** (alpha item 16, 11-02
  02:52: 21 waiters at capacity 1, five gamma runs of 21-38 s behind about 2.5 h of 11-min receipts;
  letting them pass meant re-booking eight waiters at the back). Asked besides mode 1 and seniority above: `lane bump
  <booking> --why`, recorded, limited to the orchestrators of the projects sharing the lane; each waiter's expected
  start from the estimates ahead of it, in `flotilla lane` and before booking. 0.28.0 gives heavy waiters their
  expected start in `flotilla lane` and lets light runs pass without a bump; left: `lane bump`, and the start said
  before booking. *(open, design)*
- **An unknown estimate (`? s`) for another project's rows**, and a full receipt rerun after every trunk merge (an
  affected-tests tier for hand and review, the full tier only for the push receipt). *(open, design)*

**The ledger**
- **Hand notes a stack on an unaccepted row but does not say it is not required** (alpha item 10,
  r956: 75 such commits, its dependency lost to a replaced `--requires`). 0.24.1 keeps every `--requires`, shows them
  and adds `work requires --add`; the warning at hand is still to do - `_stacked_on` already names such rows, so the
  hand's line could say "builds on `<branch>`, not required: `work requires <branch> --add <it>`".
  A dependency whose tip moved after the stack is not seen either (`_stacked_on` asks the row's current tip).
  *(checked 2026-11-04 on 0.28.0: still P - flotilla/ledger/handover.py `_stacked_on`, ledger/commands.py:625)*
- **`work adopt` cannot free a branch a dead seat's tree holds with untracked files** ("not freed: 3 uncommitted").
  With only untracked dirt, detach the dead tree's HEAD (the files stay), or print the one-line command.
  *(open: flotilla/ledger/steering.py:47)*

**Receipts, guards and the profile** (gamma, the lane report's later sections, 2026-11-01..02)
- **`flotilla onboard check` does not print `[rig]` problems.** `disk_gb = "thirty"`, `max_run_seconds = -5` and an
  unknown key gave no output and exit 0; `rig/profile.read` keeps `problems` for such values and nothing shows them
  there. *(checked 2026-11-02 on 0.24.1: still P - flotilla/onboard/check.py:10, flotilla/rig/profile.py)*
- **`work inbatch` cannot be used in direct mode.** It requires the commit on the local trunk, which is the main
  checkout's HEAD that no seat may move, so a sender that resolved a conflict on its batch branch (1591cf7 on
  `send/sim-math-rng`) was refused and used `work vouch --commit` by the reviewer instead. Asked: accept a commit on
  the sender's batch branch, or name `vouch` in the refusal and in the sender's post. *(checked 2026-11-02 on 0.24.1:
  still B - flotilla/ledger/outside.py:55)*

**The rig**
- **Part B of the rig queue: a seat books before it is ready, and is told its turn** (alpha item 12; part A in
  0.27.0 gave exclusive runs, a `rig run` that stands in line for the session and says its place and start, the line
  on `flotilla rig` and the "idle while runs wait" item; part B is designed in `docs/specs/2026-11-03-rig-queue-design.md`
  section 4, planned after A is seen in the field). The person, 2026-11-01: a seat that cannot see the queue sits
  idle waiting for its turn on a paid machine. The order lived in the orchestrator's letters; the machine idled ~40 min on s76 and 15 min on s74
  because the next "go" was never sent, ~1 h across s73-s76. Asked: a rig queue per session - `rig book` with an
  estimate and exclusive/shared, `rig queue` showing the order, who is ahead of the caller and the time left in the
  session; the machine handed to the next seat by flotilla itself when a run ends (a letter, the orchestrator
  copied); a run out of turn refused; readiness (`rig ready`, `rig pass`) so a seat still running its checks keeps
  its place; a run that does not fit the session's end told before it starts; the unserved queue carried to the
  next session; `watch` naming "rig idle with N waiting"; actual minutes recorded against estimates.
  *(open, designed: part B)*
- **A deep local lane does not point at an open rig session, and a rig session is not shared by design** (alpha
  item 15.1-3, 11-01 23:19-23:30: ~20 local waiters, about 2 h, while rig session s79 was paid for and mostly idle;
  once allowed, CPU probes finished there in seconds - 40 s against 1 h+ in the queue). Asked: when a lane run would
  wait more than N minutes by the lane's estimates and a rig session is open, say so with the rig command for the same
  job, and with none open raise an `offload` watch item for the orchestrator; run classes on the rig (`--cpu`, shared,
  may run beside another `--cpu` run; `--exclusive` for frames, audio, benches), the rig queue above with a capacity
  per class; the owner's exclusive runs first. 0.25.0 built the session's projects, the refusal and spend per
  project. *(open, design: flotilla/rig/run.py, the rig queue)*
- **A rig run does not use the toolchain the receipts use** (alpha item 15.4). The default image
  (`mcr.microsoft.com/playwright:v1.64.0-noble`) carries node 24; the project's receipts run on node 22.22.1, node 24
  changed one of its pins, so a CPU result on the rig can differ from the local receipt over the same revision.
  Asked: the profile pins the runtime a rig run uses (`[rig] node = "22.22.1"`). Today the only ways are a custom
  image the person allows (`flotilla rig allow-image`) or installing node in `[rig] setup_command` by hand, and
  neither is checked: nothing says the run's runtime differs from the receipt's. A pin, installed before setup and
  printed with the run's verdict, and a run that says so when the version it met is not the one pinned.
  *(checked 2026-11-02 on 0.24.1: [rig] has image, gpu, disk_gb, setup_command, max_run_seconds, get_types and no
  runtime - flotilla/rig/profile.py, flotilla/rig/settings.py:35)*
- **A tier proven green on the rig does not count for a handover receipt** (alpha item 15.5). Asked, as
  an option: a tier green on the rig over the same files and the same pinned runtime counts for the handover
  receipt; the push receipt stays local and full. Receipts already reuse a green over the same files
  (`receipts._green_over`, keyed by `gitq.files_of`), but only greens run on this machine. Needs the runtime pin
  above first (otherwise "the same toolchain" cannot be asked), and a design for trusting a result that came back
  from a rented machine: which tiers may, what the receipt records as its source, and that a seat cannot hand a
  rig run's green over files other than the ones it ran over. *(open, design: flotilla/ledger/receipts.py:124, :145)*

## From the marketplace pin's third review (2026-10-31, 0.23.3)

- **The pin check reads its inputs as given, and four odd ones go unflagged.**
  - Several `flotilla@X` entries enabled at once: the check takes the first alphabetically and says nothing. With a
    pinned `flotilla@zz` and an unpinned `flotilla@aa`, only `aa`'s default branch is reported.
  - A repository declared without an owner (`"repo": "flotilla"`) yields a fix command that is not a valid GitHub
    source.
  - A non-string or space-padded `ref` goes into the text as-is (`#['v1']`, "not a vX.Y.Z release tag").
  - The pin fix names the running copy's version, so a checkout ahead of its tag suggests a tag that does not exist;
    the pointer to CHANGELOG.md partly covers this.

  Nothing crashes. `claude plugin marketplace add` writes none of these shapes, so each needs a hand-edited setting
  or a dev checkout. *(checked 2026-10-31 on 0.23.3: still P - flotilla/core/claude_state.py:181, :229)*

## From watching the alpha fleet on 0.20.0 (2026-10-20)

- **Garbage collection, stage 2: the archive and the person's word.** Design sections 6 (rules 2 to 5: a twin
  elsewhere by sha256, identical copies across removed trees, unique ignored data to the archive, global excludes
  as the person's), 7 (`<project>-gc-archive/`, the manifest, `flotilla gc restore`, small told after and large
  asked before) and 9 (questions with ids grouped by piece of work, answers recorded through the question door and
  applied only by the collector, a guard against removing fleet trees, branches or the archive by hand, letters to
  the orchestrator). Until then stage 1 keeps such trees, named. *(checked 2026-10-28 on 0.23.0: built - twins,
  the archive with restore, questions and apply, the guard; letters to the orchestrator wait for stage 3's
  occasions, since every pass in 0.23.0 runs in a command the orchestrator itself runs)*
- **No room counts only the 2 GB floor, not a rig download in flight.** The design's "or under what a rig download
  in flight still needs" has nothing to read: no record of a download's remaining size exists, so a large
  `rig --get` can still fill the disk past the floor. *(open, design: flotilla/gc/disk.py)*
- **A held dead seat on a machine without /proc is asked, not judged.** On macOS `leftovers.working_in` returns
  None, so every dead seat there becomes a retire-or-keep question; reading each process's cwd with `lsof -d cwd`
  would let it be retired or assessed as on Linux. *(open: flotilla/fleet/leftovers.py, flotilla/gc/dead.py)*
- **A user install of flotilla loads in place of a project's own.** gamma installed 0.23.1 for the
  project and its session ran the user's 0.21.0 (2026-10-29); the khrushchevka fleet installed 0.13.0 and ran the
  user's 0.10.2 (2026-10-12), unnoticed. Claude Code documents precedence for `enabledPlugins` only, not for which
  of two installs of one id loads; both cases fit both "user wins" and "the first matching record in
  installed_plugins.json wins", and telling them apart means reordering the records on a live machine. Since
  0.23.2 `doctor` and the session start name a mismatch and `rig` names the cost of its advice; the cause stays
  Claude Code's. Only the copy that runs can say so: a user install older than 0.23.2 is silent, so on this
  machine nothing is said until the user install (0.21.0, which alpha runs) is brought up. Idea: ask Claude Code
  (a `/feedback` report) which copy should load. *(checked 2026-10-29 on 0.23.2: B, detected not prevented -
  flotilla/core/claude_state.py:75)*
- **`flotilla gc restore` trusts the archive's records.** The archive is a folder beside the project that any
  session can write, so a forged entry could have `restore` put a `.claude/settings.json` or an `.envrc` into a tree,
  or apply a forged patch (push security review of daef13a, findings 2 and 3). Left as is within decision 245: a
  session able to forge an entry can write the same file into the same tree itself, so `restore` gives it nothing it
  lacks; refusing `.claude/` would break restoring the person's own `settings.local.json`, which the archive takes as
  personal. Revisit if restore ever runs with more rights than a session, or if entries gain a signature a session
  cannot reach. *(checked 2026-10-29 on 0.23.1: still S, within decision 245 - flotilla/gc/archive.py restore)*
- **Seat trees pile up: 89 beside the main checkout, and `fleet clean` would remove 7.** A plan run (`flotilla fleet
  clean`, no `--yes`) kept 82 trees. 69 of them for one reason: "<tip> is not on origin's trunk, and no shipped row
  carries it". Their tips sit on branches that live on origin unmerged: `research/thunder-model-v2` holds three
  kept trees' tips, `feat/cloud-two-stream` holds another. The work is not at risk: the remote branch holds it. Only
  the tree is stale, a detached checkout of a seat long gone. Other reasons the plan gave: 31 trees with "ignored
  files that nothing makes again" (`gpu-checks-out/`, `figure3d-in/`, `spire3d-in/`, build outputs of the project
  that flotilla does not know), 24 with commits only the reflog reaches (a real doubt), and 9 with uncommitted
  files. Branches: 248 deletable, 305 kept, many of those as "flotilla did not make it", including the sender's own
  `batch/*` branches. Ideas: a detached tree whose tip a branch on origin reaches holds nothing of its own beyond
  its reflog, so it can go while the branch stays; the profile names the project's own rebuildable outputs; a batch
  branch is recognised as the fleet's. The person's rule (decision 206) still stands: a doubt keeps the tree.
  *(checked 2026-10-27 on 0.22.0: mostly fixed by gc stage 1 - stale detached trees, reflog-only commits, batch
  branches and folded caches go; unique ignored data waits for stage 2 - flotilla/gc/)*
- **"Waits on the person" holds questions the person already answered through the orchestrator.** `watch` lists 111
  open rows with `waiting_on` the person, the oldest from 2026-10-04. A sender's `wait` set 75 of them and a main
  seat's set 36. The person says some were answered in the orchestrator's session. Nothing then clears the row: only
  a move by the seat that waits clears it, and that seat is gone. Some notes are not questions at all but parked
  work ("deferred by the person's word: ... do not touch", written in Russian), which is a hold, not a wait. Ideas: the orchestrator records the
  person's answer on the row (a move that clears `waiting_on` and keeps the answer as evidence); `watch` puts a wait
  older than N days whose seat is gone into its own line ("waits on an answer nobody will read"); parked work is
  moved to `hold`. *(seen 2026-10-20 on 0.20.0: B - flotilla/watch/fleet.py:81)*
- **The person guard reads a quoted heredoc's body as shell, and refuses Python subscripts in it.** In a session
  outside any fleet, `python3 - <<'EOF'` with a script body was refused: "the program, command or move here is a
  variable or a substitution". The body lines `cur["at"] = r["at"]` and `setter[(r["move"], r.get("post"))] += 1`
  each read as a command whose program word is a glob, with `r["at"]` taken as a glob that may expand to `rig`.
  `evaluate` judges every line with `bodies=True` on purpose, since a body piped to `bash` is shell. A quoted
  delimiter fed to an interpreter other than a shell is data, though, as `heredoc_check` already says. Workaround: the
  script as a file. Idea: skip a quoted heredoc's body when the program word is not a shell (`python3`, `node`,
  `jq`), or judge such a body only for `flotilla` words. *(seen 2026-10-20 on 0.20.0: P - flotilla/guards/run.py:34,
  flotilla/guards/person.py:176)*
- **The person guard takes a path argument named `flotilla` for flotilla itself.** `find
  /home/user/.local/state/flotilla -name '*epsilon*'` was refused with the same "variable or a substitution" text.
  `_reads` treats any word whose basename is `flotilla` as a flotilla run, so the path argument counts. Its first
  non-option word after it, the quoted `'*epsilon*'`, is then read as the command, and `_opaque` refuses it as a glob.
  The same line with `-name epsilon`, or with `cd` into the directory and `find .`, passes. Any path to the plugin
  repo or its state dir followed by a glob is caught alike. Idea: only the program word of a segment (or the word
  after `-m`, `uv run`, `exec`) runs flotilla; an argument does not. A brace after such a path is refused too
  (`git -C <repo>/flotilla rev-parse 'v0.20.0^{commit}'`: "holds what bash expands"), and so is a heredoc script
  whose body names `flotilla work wait` beside braces (2026-10-27, fixing the alpha field report). *(seen
  2026-10-20 on 0.20.0: P - flotilla/guards/person.py:35, flotilla/guards/person.py:152)*
- **`flotilla status` in alpha did not finish in 60 s.** `watch --once` takes 3.6 s and `fleet` takes 1.1 s on the
  same ledger: 9259 lines, 5.5 MB. Not yet established where the time goes. *(seen 2026-10-20 on 0.20.0: measure)*

## From the alpha field report (2026-10-24..27, 0.20.0)

- **Accept races a moving trunk; files rebuilt from others make it worse** (A6). r379 bounced three times: clean when
  read, conflicting by the time the tiers finished, because almost every research row adds to `src/data/credits.ts`
  and the then-tracked `CREDITS.md`. The project's own row (CREDITS untracked and generated, credits sorted into
  parts) eased it; the person deferred a flotilla change until that is seen to fall short. Ideas: the profile names
  rebuilt files with their command, accept checks mergeability without them and land rebuilds them after the merge;
  or accept binds to the read sha and the trunk merge becomes a reviewed vouch. *(seen 2026-10-26 on 0.20.0: open,
  design - flotilla/ledger/reading.py)*
- **A pipeline inside a lane run reads green over a red test** (alpha memory `lane-green-pipeline`). `flotilla lane
  run -- sh -c 'npx vitest ... | tail -40'` recorded green: the exit is tail's, inside a shell flotilla does not read.
  The skill says so; the run does not. Idea: warn when a lane command is `sh -c` / `bash -c` whose script ends in a
  pipe without `pipefail`. *(seen 2026-10-01 on 0.7.x: P - flotilla/lane/run.py)*
- **Code paths ask about trunk's work merged into a branch.** Since the final review of 0.21.0 (C1) nothing is
  excluded by a local ref, so a docs branch that merged trunk is asked about the code trunk brought, unless the
  landing diff alone is used. Idea: exclude below the last shipped row's merge, which `ship` verified on origin.
  *(seen 2026-10-27 on 0.21.0: P - flotilla/ledger/batch.py)*
- **Existing projects get the code paths only by hand.** Onboarding offers `approve_paths` to a new project; a
  project onboarded before 0.21.0 keeps its `merge_authorized_by` until a project row adds the paths. Idea: `onboard
  check` names the detected paths where the person authorizes merges and the profile names none. *(seen 2026-10-27
  on 0.21.0: P - flotilla/onboard/check.py)*

## From the organize final review (2026-10-20, 0.20.0)

- **The organize text guard: further ways around it are logged here, not fixed one at a time** (decision 245). The
  guard reads the command's text, so `sh -c`, gh aliases and spellings not yet thought of stay past it (`CEILING` in
  `flotilla/guards/__init__.py`). The guarantee is the person's recorded answer carried out by `flotilla organize
  apply`. Known: a chain member or a row's `closes` can be closed by branch name, not number, past the guard;
  `store.numbers` ignores `stack:N` members; whether `gh issue close <n>` closes a pull request is not measured.
  *(checked 2026-10-20 on 0.20.0: still S - flotilla/guards/organize.py)*
- **Other trees get `git status` before the person's yes for that tree**, which may refresh their index.
  *(checked 2026-10-20 on 0.20.0: still P - flotilla/organize/trees.py)*
- **A stack only partly the person's falls through as a plain chain.** `queue` later refuses it as stacked, and no
  item offers the unstack. *(checked 2026-10-20 on 0.20.0: still B - flotilla/organize/walk.py)*
- **Split leftovers.** Split stops on an entry whose paths equal HEAD (an empty commit), and a dirty submodule path
  raises `IsADirectoryError`. Either leaves a worktree in the state dir; nothing is lost.
  *(checked 2026-10-20 on 0.20.0: still B - flotilla/organize/split.py)*
- **`data["trees"]` is written and never read**, so a tree's yes is never forgotten.
  *(checked 2026-10-20 on 0.20.0: still P - flotilla/organize/store.py)*

## From the khrushchevka demo fleet (2026-10-12, 0.13.0 beside another fleet on 0.10.2)

- **A rig run wrapped in the lane holds this machine for a job that runs elsewhere.** A reviewer ran
  `flotilla lane run -- flotilla rig run ...`. Its rig run waited 10 minutes for cores on the rented machine, which
  the other fleet's runs held, and all that time it held the local lane, on which the other fleet's local runs
  waited: each fleet waited on the other. The run itself took 28.8 s on the machine. Nothing refused the wrap, and
  other seats had done the same before with no harm seen. Why they wrapped it is not established (the lane guard does
  not name `rig run`; the posts say long runs go through the lane). Idea: `lane run` refuses a command that is
  `flotilla rig run`, or the posts say a rig run is not a local long run.
  *(seen 2026-10-12 on 0.13.0: B - flotilla/lane/commands.py:264)*
- **`doctor` does not name a parent directory's `.mcp.json`**, which makes every new session ask "New MCP server
  found in this project" at start. Since 0.31.0 `spawn` and `fleet` name the question once a seat is held on it;
  `doctor` could say so before any seat is raised. *(checked 2026-11-09 on 0.31.0: still P - flotilla/doctor.py)*
- **Before the profile reaches trunk, a commit of one's own on trunk cannot be pushed by the door.** `receipt run
  --purpose push` refused ("the project profile could not be read, so CI on this machine could not be asked"), and
  `onboard publish` refuses while trunk is ahead of origin. The way through was to park the commit on a branch,
  publish, then merge it back. Idea: `publish` names that way, or the receipt runs on the tree's own profile as the
  other guards do before onboarding reaches trunk.
  *(seen 2026-10-12 on 0.13.0: P - flotilla/lane/machine.py:321)*

## Rented machines (rig), after 0.9.0

- **A test that fails under the full suite's load:** `tests/test_rig_remote.py::test_a_run_sharing_the_machine_marks_its_measure_shared`
  failed once in a full run on 0.13.0's branch (the second run, expected alone, read `shared=1`) and passed 10 of 10
  alone, on main and on the branch. The sampler reads this host's process table, so a neighbour's process or a
  lingering sampler may count as sharing. *(2026-10-10: open, cause not established)*
- **From the final review of 0.13.0 (account credit)** (minor, deferred): a reading is appended on every pass even
  when unchanged (about 12 records an hour per session); the reading's time is shown as HH:MM only; a provider
  changed mid-session (disable, enable another) would mix two accounts' credit - not reachable while vast is the
  only provider; whether vast's `credit` lags its billing is unmeasured. *(2026-10-10: open)*
- **From the plan reviews of 0.11.0 (packing), not built:** EASY backfill (a later run may use a senior's room if it
  ends before the senior can start - today a senior's reservation can idle cores until its `--wait`); memory as PSS
  (`smaps_rollup`) plus `/dev/shm` instead of summed RSS; setup-phase measurements kept with the command's; a per-run
  disk estimate checked against the machine's free disk; OOM kills read from `memory.events` instead of exit 137;
  peak cores per sampling window instead of the run's mean; the journal grows without bound (history is folded on
  every poll). *(checked 2026-10-09 on 0.11.0: open)*
- **From the final review of 0.9.0** (minor, deferred): a `rig up` cut before create leaves a REQUESTED machine that
  stalls later calls for 15 minutes; the budget pre-check in `rig up` is outside the lock (bounded by the machine
  ceiling and the 90 % drain); a seat's `--why` reaches the orchestrator's model in fleet lines and `rig` (never a
  pasted line); an adopted machine's `created` is the adoption time, undercounting the floor by one call. *(checked 2026-10-07 on 0.9.0: still P -
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

## From watching the alpha fleet on 0.7.1 (2026-10-03, night; corrected 2026-10-04)

- **The way in is too heavy for a first try.** Posts, seats, lane, receipts, a ledger of ten states, 13 skills and a
  540-line README stand between a newcomer and "one session writes, another reviews". A light mode - author and reviewer
  only, no sender, no lane - that starts in minutes, and a README whose opening says only that. *(checked 2026-10-04 on
  0.7.8: open, design - needs a spec)*

## From the review of 0.7.9 (2026-10-04)

- Onboarding a repository whose only workflows are symlinks now records no CI and reads no job ids from them; it matters
  only if GitHub Actions runs a symlinked workflow, which nobody measured. *(checked 2026-10-04 on 0.7.9: measure)*

## From the alpha orchestrator's notes and the review of 0.7.17 (2026-10-06)

- Trial merge at accept: conflicts between groups of changes surface only at release; `git merge-tree` against trunk
  and the other accepted rows at `accept` would show them at once. *(open, design)*
- `moved` after `accept` needs the reader's `--agreed-by`; a pure rebase (same patch ids) could pass without it.
  *(open, design)*
- The letter after `assign` is sent by hand and forgotten; a hook could require it in the same turn. *(open, design)*
- A guard against `git reset --hard` / force-push on a branch with an accepted revision (alpha: a seat reset its
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

## From the alpha update to 0.7.1 (2026-10-03)


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

- Adopted work follows its heir to its home tree when the gone seat's tree no longer holds the branch, so the
  collector no longer reads the live heir as working there (alpha item 22) (0.31.1).
- `work show` and the reader's letter name the base a rebase moved the work onto, and the one it moved from; the
  recorded base, which the person's approval counts from, stays (0.31.1).
- A seat Claude Code holds on a start-up question is seen and named: `spawn`, `fleet`, `watch` and the inbox say
  "has not started" with the question and `claude attach <id>`, and every count of raised seats includes it (0.31.0).
- A test that asks `state_dir()` in pytest's own process is refused the real directory, wherever its file lives
  (alpha item 21: the rig tests' `world` wrote `machine.toml` as `rig`, `rig_provider`; the same road wrote the live
  rig journal), and re-measuring the machine keeps every key it does not measure (`lane_share` was lost) (0.29.3).
- Waiting lane processes burn ~20 % CPU each (alpha items 14, 14b): found closed, a waiter measured at 0.5 % CPU
  over 60 s on 0.29.2, on a fresh journal (2026-11-05).

- The review of 0.28.0 against everything found since 0.24.0: a light run never waits on an empty lane; woken waiters reuse their last look at the machine; `rig share`/`unshare` are the person's moves to the guard; an unshared project's waiting rig run leaves the line; a script's or module's arguments are not asked about (the reading fails closed); a second Ctrl-C during a rig cleanup is held off; a heavy waiter is told only what it waits for (0.28.1).

- The lane: light runs (measured <= 60 s, <= 2 cores) take what one heavy run leaves and no place; an expired wait keeps its place; a waiter is handed the lane within a second; `flotilla lane` shows the budget and heavy runs' expected starts (0.28.0; alpha items 13 and 16).

- The rig queue, part A: exclusive runs, a rig run that stands in line for its session and says its place and start, the line on `flotilla rig`, a watch item when a machine idles while runs wait, a stopped run that brings back its `--get`, a closed session's machine destroyed no longer blocking the ceiling (0.27.0).

- A person's move handed to an interpreter as text is refused, flotilla in such code asks the person; the approval check reads origin's profile (0.26.2).

- The lane journal is compacted past 2 MB, the rest archived; a booking's number is past the highest (0.26.1).

- 0.25.0: the lane's fair share across projects (`lane_share = "fair"`, rule 2, older seats never left waiting for a
  newer one); a rig session serves the projects the person shares it with, refuses the others before anything is
  spent, and shows each project's spend; a stopped rig run's verdict no longer lost to a signal as its renewer starts.
- 0.24.2: a waiting lane run folds only the journal lines appended since its last poll, and `visible` passes printable
  ASCII as it is (alpha item 14); a receipt runs and requires the tiers its revision's own profile adds; a tree
  profile that does not parse leaves the guards on trunk's rules (gamma).
- 0.24.1: field fixes from alpha and gamma - a lane capacity change reaches queued waiters; `flotilla
  lane` names the foreign runs holding slots, each waiter's reason, and sweeps dead bookings when shown; every
  `--requires` kept and shown; `work requires --add/--drop` on the record; a wait on a short name finds the live
  session.
- 0.24.0: garbage collection stage 3 - the daily pass and its occasions, dead seats (stop or keep for a held one),
  a low disk's shrinking and no-room gate, the outputs letter, released branches parked on the person's word, the
  archive's month (carried out only by the person's `gc apply`).
- 0.10.3: rig measurements fixed for packing (CPU of helpers, the machine's GPU peak, shared marked); a run's leftovers end with it; gone runs swept once; sampler bound to its run; atomic --get placement; rename and directory-put gate fixes.
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
