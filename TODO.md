# TODO

Deferred findings, kept here until a plan takes them. Each names where it came from.

**How this file is kept.** Every open item ends with when it was last checked against the code, on which version,
with what verdict and where: `*(checked <date> on <version>: still [S|B|P] - <file:line>)*` (S a guard lets
something through, B a bug a user meets, P polish, tests or docs), or `open, design`, or `measure` (needs a live
run). A later triage re-checks only items whose named files changed since that version. An item that is done -
fixed by a release, or found fixed - leaves its section for **Done** at the bottom, one line with the version.

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

## From the twosuns update to 0.7.1 (2026-10-03)

- A refusal the person has to resolve must say what to run, not only that something was refused. When auto mode's
  classifier, a permission rule or a flotilla guard refuses a seat something only the person can do - edit
  `.claude/settings*.json`, approve, push with `--no-verify`, install hooks - the seat sends the orchestrator the exact
  command or edit (file, what to remove or change, what to keep) ready to paste with `!`, and how to check it worked;
  the orchestrator relays it verbatim. Seen in the update itself: the classifier refused removing the 0.6.7 allow rules
  from `.claude/settings.local.json` as self-modification, and the person had to ask for the command. People new to
  Claude Code do not know what to ask for. Covers the post templates (orchestrator, main, minor, sender, reviewer,
  judge, helper) and the refusal texts flotilla prints itself. *(checked 2026-10-04 on 0.7.8: still [B] - only the
  approve refusal names its command - guards/person.py:82; templates/posts/)*

## From the broker final review (2026-09-27)

- A background session the census cannot place (census down) is left to its dialog, which for it is the measured hang;
  `spawn` could export a marker (`FLOTILLA_SESSION_KIND=background`) so the hook knows without the census. *(checked
  2026-10-04 on 0.7.8: still [B] - broker/decide.py:193; no FLOTILLA_SESSION_KIND)*
- On some mounts (FUSE, SMB, exFAT) `os.link` raises EPERM/ENOTSUP, not FileExistsError: the hook denies with the error,
  but `permit answer` prints a traceback. Map it to a refusal naming the filesystem. *(checked 2026-10-04 on 0.7.8:
  still [B] - reproduced - broker/queue.py:133-136, broker/commands.py:75)*
- `status: waiting` may also mean prompts other than permissions; a waiting session is reported only when it holds a
  ledger move. *(checked 2026-10-04 on 0.7.8: still [P] - watch/fleet.py:38-39)*
- A background orchestrator counts as live: questions then wait the full budget instead of naming `claude attach`.
  *(checked 2026-10-04 on 0.7.8: still [P] - broker/decide.py:211-212)*
- The skill asks the model to read a typed answer as allow or deny; make "Other" always a deny with the words. *(checked
  2026-10-04 on 0.7.8: still [P] - skills/permit/SKILL.md:16)*
- A reused pid can keep an abandoned question looking alive (bounded by its deadline); `os.kill(0, 0)` on a pid 0.
  *(checked 2026-10-04 on 0.7.8: still [P] - is_alive(0) is True - broker/queue.py:51-58)*
- Staged `.q-*.tmp` / `.a-*.tmp` files are left when a write fails. *(checked 2026-10-04 on 0.7.8: still [P] -
  broker/queue.py:70-72, 86-88)*
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
- `queue_command`: a command that is not found (shell exit 127) or not a string is an unknown the lane waits on; a
  lasting unknown would refuse at once. *(checked 2026-10-04 on 0.7.8: still [B] - a non-string crashes -
  lane/machine.py:142, 149)*
- The claimed-work summary line is appended last and is the first cut past 20 items. *(checked 2026-10-04 on 0.7.8:
  still [P] - watch/render.py:43-48)*
- The breaks log is never pruned; `stop_hook_active` set by another plugin's Stop hook is recorded as a flotilla break.
  *(checked 2026-10-04 on 0.7.8: still [P] - watch/fleet.py:205-206, watch/guard.py:39)*
- `watch --once` has no test for the ledger-could-not-be-read exit 2; an exception in its gather exits 1, not 2.
  *(checked 2026-10-04 on 0.7.8: still [P] - watch/commands.py:42)*
- `flotilla/ledger/events.py` lacks a final newline. *(checked 2026-10-04 on 0.7.8: still [P])*

## From the watchers plan (2026-09-27)


## From the lane final review (2026-09-27)

- On the `ps` path a failed second CPU sample reads as "the run ended". *(checked 2026-10-04 on 0.7.8: still [B] -
  lane/procs.py:135, lane/machine.py:107-108)*
- `gh run list --limit 10` can miss an older in-progress run behind ten newer ones; ask by status instead. *(checked
  2026-10-04 on 0.7.8: still [B] - lane/machine.py:157)*
- A `run` annotation resets `updated_at`, so `status --stalled` never shows a row whose owner keeps re-running tests.
  *(checked 2026-10-04 on 0.7.8: still [P] - ledger/model.py:117)*
- A hand holder's own `pytest` counts twice (the booking and a foreign run) when `lane_capacity` is 2 or more. *(checked
  2026-10-04 on 0.7.8: still [P] - fails closed - lane/book.py:108, lane/acquire.py:66)*
- An invalid `lane_capacity` in `machine.toml` falls back to 1 silently. *(checked 2026-10-04 on 0.7.8: still [P] -
  lane/commands.py:28-30)*

## From the spawn and posts final review (2026-09-27)

- The one-copy check is check-then-act: two `flotilla spawn -s 1` at the same moment could raise two senders. *(checked
  2026-10-04 on 0.7.8: still [B] - fleet/spawn.py:197, 299)*
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
