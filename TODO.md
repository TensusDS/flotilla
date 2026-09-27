# TODO

Deferred findings, kept here until a plan takes them. Each names where it came from.

## From the watchers plan (2026-09-27)

- Measure on a live background session: that a Stop block reaches the session as a continuation, and that the
  census sessionId equals the hook's session_id for a background session (measured only for an interactive one).

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
- Measure on a live background session (the billed e2e smoke): spawning from inside a session's Bash, what
  `claude stop` does to `claude agents --json`, and `${CLAUDE_PLUGIN_ROOT}` in `allowed-tools`.

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
