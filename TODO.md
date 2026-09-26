# TODO

Deferred findings, kept here until a plan takes them. Each names where it came from.

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
