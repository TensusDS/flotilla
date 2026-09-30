# Field Fixes, Part 6 (Track A): Direct-Push Delivery Without Dead Ends — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make direct-push delivery honest and passable: a merge the reader read counts as read, `land` checks what
the landed merge brought whatever state the main checkout is in, unread batch work is vouched for by a reader
rather than by the sender in the reader's name, and every refusal in the delivery half has a legal next move.

**Architecture:** Six tasks in `batch.py`, `delivery.py`, `reading.py`, `handover.py`, `outside.py`,
`transitions.py`, the sender and reviewer posts. Track B (views, watch, judge) runs in parallel in another
worktree and does not touch these files; the only shared file is `transitions.py`, where Track B adds one edge
(`shipped → unbroke`) — merge conflicts there are one line.

**Tech Stack:** Python 3.11+ stdlib, git, pytest via `uv`.

**Spec:** `docs/field-tests/2026-09-29-twosuns-fleet.md` — H20, H21, H24, H26c, H28, H29, H33, H35, H37, H43;
`docs/specs/2026-09-22-flotilla-design.md` sections 6.2, 6.3.

## Decisions (the planner's, 2026-09-30; Max approved fixing the findings)

1. **A merge inside a reviewed row's read range is read** (H20): `batch.Accounting.account` looks a commit up in
   the read ranges before judging it as a merge. A conflict resolved by hand in a merge the reader read is exactly
   what the reader saw in the diff of the range.
2. **`land` always counts what the landed merge brought over its first parent** (H29, H35): the commits of
   `commit ^commit^1`, whether the merge is on the local trunk or only on origin; on the local trunk it
   additionally counts what the push would carry (`trunk_head ^origin`), as now. The state of the main checkout
   can no longer turn the check off.
3. **Batch work is vouched for by its reader** (H21, H24): a new annotation move `vouch <branch> --commit <sha>`
   made by a session whose post may `accept` (and may not `land`), not the row's owner. It records the commit in
   the row's `vouched` list; `Accounting` counts vouched commits as read wherever they are — local trunk or origin.
   The sender asks the reader for it with a letter; it never writes a reader's name itself. `inbatch` stays for
   work born in a batch that is not pushed yet.
4. **The sender can send a queued or accepted row back** (H26c): a move `return <branch> --why` by the sender,
   `queued|accepted → fixing`, clears the verdict; the owner merges trunk, and hands again for a read.
5. **`moved` needs the reader's agreement only while that reader is alive** (H28): if the reader who took the row
   is not in the census, `moved` is allowed without `--agreed-by`; the row goes back to `handed` with no reader, and
   the orchestrator assigns one. The evidence names the reader who was gone.
6. **`accept` refuses a tip that does not merge with trunk** (H43): `git merge-tree --write-tree <trunk> <tip>`
   exit 1 → refused: "the tip does not merge with trunk <sha>: return it with `fix`". A question git cannot answer
   does not refuse.

Record as decisions 130–135 in `docs/specs/2026-09-22-decisions-log.md` ("planner's decision, field fixes part 6
track A, 2026-09-30").

## Global Constraints

- English only (`tools/check_no_cyrillic.py`); Linux and macOS; Python 3.11+ stdlib only.
- The suite, three ways: `uv run --with pytest python -m pytest -p no:cacheprovider tests/` (no `-q`), with
  `--python 3.11`, with `GIT_CONFIG_GLOBAL=/dev/null`; `claude plugin validate .` and
  `claude plugin validate .claude-plugin/plugin.json`.
- Post templates whose text changes get `template_version` + 1.
- Worktree `/home/max/workspace/flotilla-part6a`, branch `fix/part6-delivery` from `main`.

## Review Focus

1. **A commit vouched for by a session that authored it** (the row's owner, or a sender) → refused (Task 3).
2. **A merge read by the reader, then the branch moved and the new merge was not read** → the new merge is not in
   the read range; `land` refuses it (Task 1).
3. **`land` of a merge that is on the local trunk and on origin at once** (the main checkout pulled) → the merge's
   own commits are still counted (Task 2, the H29 regression test).
4. **`return` of a row by a session that is not the sender, or from `handed`** → refused (Task 4).
5. **`moved` when the reader is alive but a census cannot be asked** → refused as before (doubt errs to refusal;
   Task 5).

---

### Task 1: a read merge is read (H20)
Files: `flotilla/ledger/batch.py`; tests `tests/test_ledger_batch.py`, `tests/test_ledger_delivery.py`.
- [ ] Test: a branch that merges trunk with a hand-resolved conflict, then is accepted over its tip, lands in
  direct mode with no refusal (the exact twosuns shape: merge commit = the verdict revision; and merge commit
  inside the range with a commit after it).
- [ ] Test: the same branch, moved after the verdict to a new conflicting merge → refused, naming the new merge.
- [ ] Implement: in `Accounting.account`, before `if merge:`, return the `self.read` answer when `full` is in it.
- [ ] Suite; commit `fix(ledger): a merge inside the range the reader read counts as read`.

### Task 2: `land` counts the landed merge whatever the main checkout holds (H29)
Files: `flotilla/ledger/delivery.py`; test `tests/test_ledger_delivery.py`.
- [ ] Test (the H29 regression): push a merge carrying an unread commit, pull the local trunk to origin, `land` →
  refused naming the unread commit. Before the fix this lands.
- [ ] Test: the reviewed-only merge, local trunk pulled → lands.
- [ ] Implement: `own = batch.unaccounted(ledger, s.rows, origin_head or trunk_head, since=commit^1)` restricted to
  `commit ^commit^1` (use `outgoing(ledger, commit, since=first_parent)`); on the local trunk also the existing
  `trunk_head ^origin`; `loose` is their union, in order, without duplicates.
- [ ] Suite; commit `fix(ledger): land checks what the landed merge brought, pulled or not`.

### Task 3: `vouch` — batch work is vouched for by its reader (H21, H24)
Files: `flotilla/ledger/model.py` (`vouched: list`), `flotilla/ledger/outside.py` (new `vouch`),
`flotilla/ledger/batch.py` (`Accounting` reads `vouched`), `transitions.py` (`ANNOTATIONS` + "vouch"),
`posts.py` (`MOVES`), `cli.py`, `commands.py`, `templates/posts/reviewer.md` (`may` + a bullet),
`templates/posts/sender.md` (ask the reader to vouch; never name a reader yourself); tests
`tests/test_ledger_outside.py`, `tests/test_ledger_delivery.py`.
- [ ] Tests: a reviewer vouches for the sender's resolution merge on a queued row → `land` passes, local or origin;
  the owner / a sender / a session that may land cannot vouch; an unknown commit is refused; vouching does not
  change the row's state.
- [ ] Implement; letter to the sender optional (the existing letters mechanism if a vouch changes who moves — it
  does not, so none).
- [ ] Suite; commit `feat(ledger): a reader vouches for batch work; the sender never writes a reader's name`.

### Task 4: `return` — the sender sends a queued or accepted row back (H26c)
Files: `transitions.py` (`queued`, `accepted` gain `"return": "fixing"`), `delivery.py` (new `send_back`),
`posts.py`, `cli.py`, `commands.py`, `templates/posts/sender.md`; tests `tests/test_ledger_delivery.py`,
`tests/test_ledger_transitions.py`.
- [ ] Tests: sender returns a queued row with `--why` → `fixing`, verdict cleared, the owner's move; refused
  without `--why`, refused for a non-sender, refused from `handed`; the owner can hand it again.
- [ ] Implement; the letter goes to the owner (as `fix` letters do).
- [ ] Suite; commit `feat(ledger): the sender returns a row that no longer merges`.

### Task 5: `moved` without a reader who is gone (H28)
Files: `flotilla/ledger/handover.py` (`moved`); tests `tests/test_ledger_handover.py`.
- [ ] Tests: reader not in the census → `moved` passes without `--agreed-by`, row `handed`, reader "", taken
  False, evidence `reader_gone`; reader live → refused as before; census unavailable → refused as before.
- [ ] Implement with `ledger.live_names()` (it raises `MoveRefused` when the census cannot be asked — keep the
  refusal).
- [ ] Suite; commit `fix(ledger): a moved tip goes back to reading when its reader is gone`.

### Task 6: `accept` refuses a tip that does not merge with trunk (H43)
Files: `flotilla/ledger/reading.py` (`accept`), `flotilla/ledger/gitq.py` (`merges_cleanly(root, a, b, *, run)
-> bool | None`), `templates/posts/reviewer.md`; tests `tests/test_ledger_reading.py`.
- [ ] Tests: conflicting tip → refused with "return it with `fix`"; clean → accepted; git unable to answer →
  accepted (no false refusal).
- [ ] Suite; commit `fix(ledger): accept refuses a tip that does not merge with trunk`.

### Task 7: decisions 130–135 and the whole-branch review
- [ ] Decisions log entries; commit.
- [ ] Final review of the branch by a fresh reviewer on the most capable model; one fix pass; merge with Track B;
  suite three ways; push under Max's standing ok for flotilla; report what shipped.
