# TODO

Deferred findings, kept here until a plan takes them. Each names where it came from.

## From the broker plan (2026-09-27)

- Measure with a live fleet: an orchestrator woken by `flotilla permit next --wait` finishing in the
  background, and the AskUserQuestion round trip back to a background session's hook.

## From the guards final review (2026-09-27)

- `flotilla guard check` with an override in the command writes a bypass record although nothing ran; a real override
  push is recorded twice (command guard and pre-push). Record only when the command runs.
- A session whose working directory is not onboarded does not judge `git -C <onboarded> push` (spec 8: hooks are
  silent outside a project); the pre-push hook covers it. Plan decision 8's "another flotilla project" holds only
  from inside one.
- The line-number guard refuses addresses that cannot go stale: GNU `0,/re/`, `1,/re/`, `1i\`, and a digit after `;`
  inside a substitution with spaces (`s/foo; 2 items/bar/`).
- `git checkout -- $(git diff --name-only)` passes silently; paths from a substitution are unknown and should warn.
- `git switch -f` / `--discard-changes` are not guarded and not named in the ceiling.
- `diff --numstat` without `-z`: a quoted non-ASCII path never matches a reservation pattern.
- The line printed for `core.hooksPath` has no missing-link branch, so a pre-commit added there fails closed.
- The budget test asserts 1 s, not ~200 ms (measured: 0.0 / 24 / 52 ms in process, ~89 ms process start).
- `find_project` and the `guard_hook` import sit outside any try in `run_hook`: a crash there lets a push through.
- `guard install` ignores a failed `refresh_link`.
- A symlinked workflow file is counted by onboarding and skipped by `workflow_at`: permanent drift.
- `gh pr merge --disable-auto`, and merges into a base other than trunk, ask for a receipt though nothing lands.

## From the guards plan (2026-09-27)

- The guard's time, measured on this machine: 0.0 ms for a plain command, 24 ms for a read one, 52 ms for a push,
  plus ~89 ms process start (the test bounds it at 1 s for CI). (A PreToolUse deny reaching a background session:
  measured 2026-09-27, decisions log entry 70.)

## From the watchers final review (2026-09-27)

- The hook-level throttle test does not guard the digest's age exclusion: both renders read "(2 h)"; cross an hour.
- Every Stop and prompt pays for the census and the ledger before deciding; check the Stop payload before gathering,
  and read the census once at session start (the doctor reads it too).
- A census error whose text varies (stderr) re-prints on every prompt; hook exceptions are not throttled at all.
- `HOOK_CHECK_TIMEOUT` is defined in both `hooks.py` and `watch/context.py`; no test pins the 10 s prompt/stop budget.
- The README says `[watch] stop_guard = false` in `.flotilla/project.toml`; the profile is read from trunk, say so.
- `queue_command`: a command that is not found (shell exit 127) or not a string is an unknown the lane waits on;
  a lasting unknown would refuse at once.
- The claimed-work summary line is appended last and is the first cut past 20 items.
- The breaks log is never pruned; `stop_hook_active` set by another plugin's Stop hook is recorded as a flotilla break.
- `watch --once` has no test for the ledger-could-not-be-read exit 2; an exception in its gather exits 1, not 2.
- `flotilla/ledger/events.py` lacks a final newline.

## From the watchers plan (2026-09-27)

- The watchers read a background session's `state` only; the census also gives `status` (`busy`, `idle`,
  `waiting`), and `waiting` is a session stuck on a permission prompt, not an idle one (entry 70). Say so in the
  dropped-ball item. (Stop continuation and the session id: measured 2026-09-27, entry 70.)

## From the lane final review (2026-09-27)

- On the `ps` path a failed second CPU sample reads as "the run ended".
- `gh run list --limit 10` can miss an older in-progress run behind ten newer ones; ask by status instead.
- A `run` annotation resets `updated_at`, so `status --stalled` never shows a row whose owner keeps re-running tests.
- A hand holder's own `pytest` counts twice (the booking and a foreign run) when `lane_capacity` is 2 or more.
- An invalid `lane_capacity` in `machine.toml` falls back to 1 silently.

## From the spawn and posts final review (2026-09-27)

- The one-copy check is check-then-act: two `flotilla spawn -s 1` at the same moment could raise two senders.
- A dry run without the census skips the one-copy check, and its warning speaks only of names.
- The dry-run command is not shell-quoted; print it with `shlex.join`.
- `fleet.model = "reviewer-strongest"` silently overrides a post's explicit `model`.
- A post file on trunk can set `permission_mode: bypassPermissions`; consider a profile opt-in for it.
- Every session announces itself to every peer at start: N·(N−1) letters that carry only state (spec-level).
- `git status --porcelain` counts an untracked directory as one entry, so "1 uncommitted file" can undercount.
- In direct-push mode the sender cannot check out trunk in its home tree (trunk is checked out in the main
  checkout); its land flow needs a design in the guards or lane part.
- Measure on a live background session (the billed e2e smoke): spawning from inside a session's Bash, and
  `${CLAUDE_PLUGIN_ROOT}` in `allowed-tools`. (`claude stop <id>` removes the session from the census at once; by
  name it fails: entry 70; `retire` already passes the id.)
- `claude --bg` refuses a directory whose trust was never accepted, and trust is not inherited from a parent
  (entry 70): `doctor` and `spawn` should say so before launching, not after.

## From the ledger part B final review (2026-09-26)

- GitHub unreachable at the PR step exits 2 ("refused"), not 3 ("not yet"): an instrument that could not ask says
  unknown.
- `_fetch` ignores its own failure, so "(fetched)" can be untrue; a None from `_on_origin` also reads "does not have".
- A PR closed without merging advises "queue it again", which is not legal from `queued`; say release and re-claim.
- The `vanished` finding fires for shipped or walked rows whose branch was cleaned up, and for auto-deleted PR
  branches, advising moves that are not legal there.
- `reconcile` always exits 0, and its parser has no `--skip-event` although a `pre-shipped` refusal advises it.
- A `pre-` script that calls a `flotilla work` move deadlocks on the ledger lock until the 30 s timeout; say so in
  `docs/events/schema.json`'s documentation.
- An accepted row the brief holds back still counts as read when its commits ride in with another row's land.
- In the brief, an accepted row whose branch git cannot resolve reads as ready.

## From the ledger part A final review (2026-09-24)

- An unknown base is recorded as `""`; record it as unknown, so a reader can tell "not asked" from "git could not say".
- `_stacked_on` reads `None` (git could not answer) as "not stacked"; it should be a third outcome.
- The `fetch` in `tree cut` fails silently; say so, and name the trunk revision the tree was cut from.
- `--reviewed` accepts a branch name; require a revision (a sha prefix), since a name moves.
- An unknown owner post counts as "not main" in the transition rules; refuse instead.
- `receipt show` always exits 0; exit non-zero when no purpose has a green receipt.
- `check_receipt` never compares the sha stored inside the receipt with the one it was asked about.
- `--agreed-by` on `moved` is only the owner's word; part B may ask the named reader.
- Test gaps: `assign` with the census unreachable; `hand`/`moved` with an unresolvable tip; malformed `may` forms in a
  post; the race test does not assert the holder's name; `--requires` resolving a released row by id.
- Not judged by the reviewer, open for part B: the order of checks inside a move; separate clones sharing one ledger;
  `landed` has no release edge; a purpose with no tiers counts as valid; the strict frontmatter parser; letters are
  printed, not delivered.

## From the onboarding final review (2026-09-23)

- The machine profile does not record arch, git version or Claude Code version.
- The questionnaire does not ask whether two full test runs fit on the machine at once.
- The onboard skill does not state that background isolation stays on.
