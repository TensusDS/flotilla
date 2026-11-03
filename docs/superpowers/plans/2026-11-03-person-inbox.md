# The person's inbox (0.26.0) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** What waits on the person reaches the person: one queue read from the ledger (`flotilla inbox`), person
waits never folded into "standing", a line on the person's own screen past the model, a recorded put-off, and two
attention modes the person alone sets.

**Architecture:** A pure collector (`flotilla/watch/inbox.py`) turns the context the hooks already gather into
entries of the person's queue. A small state file per project (`flotilla/watch/shown.py`) records what flotilla put
on the person's screen and when the line is due. The prompt hook prints the model's count line to the orchestrator
on every prompt and the person's line as the hook's `systemMessage` when due. The person's mode lives in
`machine.toml`, set by a person-only command.

**Tech Stack:** Python 3.11+ stdlib, pytest. No new dependencies.

**Spec:** `docs/specs/2026-11-03-person-inbox-design.md`

## Global Constraints

- The queue is computed and never stored; only a ledger move removes an entry from it.
- "Shown to the person" is recorded only by flotilla's own display, never by a model's claim; no `work told` move.
- The line and `flotilla inbox` count the same set: the line's numbers come from the same `collect()` call.
- The orchestrator cannot silence the line: no flag, no config key it can reach, no hold hides a count; a hold only
  moves an entry from its group to "put off".
- Attention modes in this release: `all` (default) and `digest` (period 30 minutes by default). Set only by the
  person; refused to a Bash tool call and to a background session.
- Hold semantics for batching and the reading queue do not change.
- Seat templates do not change; the orchestrator's template moves (template_version 23 -> 24).
- `UserPromptSubmit` output stays under 10,000 characters (`render.py`).
- Plugin version 0.26.0 (pyproject, `.claude-plugin/plugin.json`, CHANGELOG); the real twosuns data stays out of the
  repository.

## Review Focus

- A model's `flotilla inbox` in the person's interactive session (it passes `caller.person_refusal`) must record
  nothing as shown - Task 5 test `test_a_tool_call_of_inbox_records_nothing`.
- A prompt the person did not type (a peer's message, a task notification) must not record "shown" - Task 6 test
  `test_a_prompt_that_is_not_the_persons_records_nothing`.
- Approvals: computing "needs the person's yes" runs git per row; a failure must count the row as waiting on the
  person, never drop it - Task 3 test `test_an_approval_that_cannot_be_asked_counts_as_waiting`.
- A hold until the person on a `claimed` or `shipped` row must not let that row into a batch or the reading queue -
  Task 8 test `test_a_put_off_shipped_row_stays_out_of_the_batch`.
- An unreadable or missing `machine.toml` must give mode `all`, never silence - Task 4 test
  `test_an_unreadable_machine_file_means_all`.

---

### Task 1: Measure `systemMessage` on Claude Code 2.1.317

No product code. The build stops here if the line does not reach the screen (spec, "Measured before it is relied
on"), and the channel goes back to the person as a decision.

**Files:**
- Create (scratch, outside the repo): a probe dir with `settings.json` and `probe.py`
- Later recorded in: `flotilla/watch/shown.py` docstring (Task 6)

- [ ] **Step 1: Write the probe hook.** `probe.py` appends its stdin payload as one JSON line to `payloads.jsonl`
  and prints `{"systemMessage": "FLOTILLA-PROBE-LINE", "hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
  "additionalContext": "PROBE-CONTEXT-7731"}}`. `settings.json` declares it as a `UserPromptSubmit` command hook.
- [ ] **Step 2: Interactive session.** In tmux: `claude --settings <probe>/settings.json --model haiku` in the probe
  dir; send "Say every probe word you were given, verbatim, or NONE." `tmux capture-pane -p`.
  Expected: record (a) whether `FLOTILLA-PROBE-LINE` is on screen, (b) whether the reply names `PROBE-CONTEXT-7731`
  (coexistence) and (c) whether it names `FLOTILLA-PROBE-LINE` (the model sees it); (d) grep the session transcript
  under `~/.claude/projects/` for the line.
- [ ] **Step 3: Background session the person attaches to.** Launch one the way `flotilla spawn` does
  (`flotilla/fleet/launch.py`), attach, send the same prompt, capture. Same four records.
- [ ] **Step 4: Prompts that are not the person's.** To the background session: a peer message (SendMessage from
  this session), and a background task's completion. Compare their `payloads.jsonl` lines with the typed prompt's:
  any field or prompt prefix that tells them apart.
- [ ] **Step 5: Decide.** If (a) fails in the interactive session: STOP, report to the person with the candidates
  (`terminalSequence`/window title, SessionStart stderr). Otherwise write the findings into the ledger of this plan
  and carry them into Task 6: the marker that tells a person's prompt (or "none found": record shown only in
  interactive sessions).

### Task 2: History keeps the post that made each move

**Files:**
- Modify: `flotilla/ledger/model.py:145-147` (the `row.history.append`)
- Test: `tests/test_ledger_model.py` (or the file holding fold tests; create `tests/test_watch_inbox.py` is Task 3)

**Interfaces:**
- Produces: every `Row.history` entry carries `"post": str` ("" for events written before posts were recorded).

- [ ] **Step 1: Failing test** `test_history_names_the_post_of_each_move`: fold two events with `post="sender"` and
  `post=""`; assert `[h["post"] for h in row.history] == ["sender", ""]`.
- [ ] **Step 2: Run** `uv run pytest tests/test_ledger_model.py -k post -q` - Expected: FAIL (KeyError).
- [ ] **Step 3: Implement**: add `"post": event.get("post", "")` to the history entry.
- [ ] **Step 4: Run** - Expected: PASS; then the whole suite, `uv run pytest -q`.
- [ ] **Step 5: Commit** `feat(ledger): a row's history names the post of each move`.

### Task 3: The person's queue, collected

**Files:**
- Create: `flotilla/watch/inbox.py`
- Test: `tests/test_watch_inbox.py`

**Interfaces:**
- Consumes: `Context` (`flotilla/watch/context.py`), `fleet.question_items`, `rig.surface.items`, `views.who_moves`,
  `batch.person_must_approve`, `batch.approved`, history `post` (Task 2).
- Produces:

```python
@dataclass(frozen=True)
class Entry:
    key: str      # stable across prompts: "row:<id>", "approve:<id>", "merge:<id>", "question:<session>:<at>",
                  # "rig:<request id>", "window:<session name>"
    group: str    # one of GROUPS
    branch: str
    why: str      # verbatim, one line (core.text.visible)
    since: str    # ISO time, "" when unknown
    by: str       # who recorded it, "" when flotilla found it
    urgent: bool
    blocker: bool

GROUPS = ("ship", "check", "question", "approve", "merge", "permission", "rig", "window", "other", "put off")
def collect(ctx, *, must_approve=None) -> list[Entry]   # ordered: urgent first, then GROUPS order, then oldest
def counts(entries) -> dict[str, int]                   # per group, plus "urgent", "blocker", "total"
```

Groups (spec, "Decided"): an open row with `waiting_on == "the person"` goes by the post of its last `wait` move -
sender -> `ship`, judge -> `check`, main/minor -> `question`, anything else -> `other`; a row with
`held_until == "the person"` -> `put off`, whatever recorded it (with or without a wait). `approve`: open rows in
`accepted`/`queued` where the profile's `flow.merge_authorized_by == "human"`, `must_approve(row)` is true and
`batch.approved(row)` is false (`must_approve` defaults to `lambda row: batch.person_must_approve(ctx.ledger, row)`;
an exception counts as true). `merge`: `who_moves` returns "the person who merges the PR". `permission`: the broker
queue. `rig`: rig items whose `who` starts with `rig:request:`. `window`: fleet `PERSON` items with no branch (a
census session waiting at its prompt). A row in several groups appears once: put off > approve > merge > its wait's
group. `urgent` = `row.urgent_at` set. `blocker` = urgent, or `permission`/`window`, or a row whose mover (or owner
for a claimed/fixing row) has no other open, non-reserved row that is neither waiting nor held.

- [ ] **Step 1: Failing tests** in `tests/test_watch_inbox.py` (rows via `watchkit.row`, history entries
  `{"move": "wait", "post": ...}`):
  - `test_waits_group_by_the_post_that_recorded_them`: three rows waiting on the person, last wait by `sender`,
    `judge`, `main` -> groups `["ship", "check", "question"]`.
  - `test_a_hold_until_the_person_is_put_off_with_or_without_a_wait`: one held with a wait, one held without ->
    both `put off`, why = `held_why`.
  - `test_a_row_parked_in_words_stays_active`: waiting on the person, note "parked until after the teaser", no hold
    -> group `question`, not `put off`.
  - `test_urgent_comes_first_and_is_a_blocker`.
  - `test_a_wait_is_a_blocker_when_its_session_has_nothing_else`: owner with one row -> blocker; owner with a
    second open claimed row -> not.
  - `test_an_approval_that_cannot_be_asked_counts_as_waiting`: profile human, `must_approve` raises -> entry
    `approve`.
  - `test_permission_questions_and_waiting_windows_are_entries`.
  - `test_counts_match_the_entries`: `counts(collect(ctx))["total"] == len(collect(ctx))`.
- [ ] **Step 2: Run** `uv run pytest tests/test_watch_inbox.py -q` - Expected: FAIL (module not found).
- [ ] **Step 3: Implement** `Entry`, `GROUPS`, `collect`, `counts` in `flotilla/watch/inbox.py`.
- [ ] **Step 4: Run** - Expected: PASS; then `uv run pytest -q`.
- [ ] **Step 5: Commit** `feat(watch): the person's queue, computed from the ledger`.

### Task 4: The attention mode, the person's alone

**Files:**
- Create: `flotilla/watch/attention.py`
- Modify: `flotilla/onboard/machine.py:91` (`PERSON_KEYS` += `"attention"`, `"attention_every"`),
  `flotilla/cli.py` (sub-command `attention`), `flotilla/guards/person.py` (`GUARDED`/the seen moves: `attention`
  with a mode argument)
- Test: `tests/test_watch_attention.py`, `tests/test_guards_person.py` (existing file for person guard tests)

**Interfaces:**
- Produces:

```python
ALL, DIGEST = "all", "digest"
DEFAULT_EVERY = 30   # minutes
def mode(state: Path) -> tuple[str, int]       # (mode, every minutes); anything unreadable -> (ALL, DEFAULT_EVERY)
def run_attention_command(args) -> int          # `flotilla attention` prints; `flotilla attention all|digest
                                                # [--every N]` sets, after caller.person_refusal(...)
```

- [ ] **Step 1: Failing tests**:
  - `test_no_machine_file_means_all`, `test_an_unreadable_machine_file_means_all`, `test_digest_reads_its_period`
    (`attention = "digest"`, `attention_every = 45` -> `("digest", 45)`), `test_a_bad_period_falls_back_to_30`.
  - `test_setting_a_mode_is_refused_when_not_a_person` (monkeypatch `caller.person_refusal` to return a reason ->
    exit 2, file unchanged); `test_the_person_sets_digest` (-> keys written via `set_person_keys`).
  - `test_a_remeasure_keeps_the_mode` (`write_machine` keeps `attention`).
  - person guard: `test_a_tool_call_setting_attention_is_refused` (`flotilla attention digest` -> refuse);
    `test_reading_the_mode_is_not_refused` (`flotilla attention` -> no finding).
- [ ] **Step 2: Run** `uv run pytest tests/test_watch_attention.py tests/test_guards_person.py -q` - Expected: FAIL.
- [ ] **Step 3: Implement** `attention.py`, the CLI parser, `PERSON_KEYS`, the person guard's `attention` move.
- [ ] **Step 4: Run** - Expected: PASS; then `uv run pytest -q`.
- [ ] **Step 5: Commit** `feat(attention): the person picks all or digest; no session can`.

### Task 5: `flotilla inbox` and what counts as shown

**Files:**
- Create: `flotilla/watch/shown.py`, `flotilla/watch/toolcall.py`, `flotilla/watch/inbox_command.py`
- Modify: `flotilla/cli.py` (sub-command `inbox`), `flotilla/hooks.py` (`GUARD_TRIGGERS` += `"inbox"`; `_guard`
  marks a Bash call naming `inbox`)
- Test: `tests/test_watch_shown.py`, `tests/test_watch_inbox_command.py`, `tests/test_hooks.py`

**Interfaces:**
- Consumes: `inbox.collect`, `inbox.counts` (Task 3).
- Produces:

```python
# shown.py - one file per project: <state>/watch/shown/<repo_key hash>.json
def load(state_dir, repo_key: str) -> dict          # {"first": {key: iso}, "line": {"digest", "at", "blockers"},
                                                    #  "prompt_at": iso}
def mark(state_dir, repo_key: str, keys, at: str) -> None   # first-shown time for keys that have none
# toolcall.py
def note(state_dir, session_id: str, at: float) -> None        # the guard saw a Bash call naming inbox
def by_tool(state_dir, session_ids, now: float, window: float = 60.0) -> bool   # consumes a fresh note
# inbox_command.py
def listing(entries, shown_first: dict, now) -> list[str]   # numbered rows, one per entry, grouped, with
                                                           # age, recorded by, "first shown <age> ago" or "never"
def run_inbox_command(args) -> int                         # 0 empty, 1 some (as watch --once)
```

The listing records shown (via `mark`) only when `caller.person_refusal("reads the inbox as shown")` is "" and
`toolcall.by_tool(state, [ids of calling_sessions()], now)` is false; it prints its rows either way.

- [ ] **Step 1: Failing tests**:
  - `test_mark_keeps_the_first_time` (mark twice, first time stays).
  - `test_listing_numbers_rows_and_says_never` (two entries, nothing shown -> lines `1.` and `2.`, each with
    "never").
  - `test_the_persons_listing_records_shown` (person_refusal -> "", no note -> `load(...)["first"]` has both keys).
  - `test_a_tool_call_of_inbox_records_nothing`: run the guard hook with `{"tool_input": {"command": "flotilla
    inbox"}, "session_id": "s1"}`, then the command with `calling_sessions` returning a session with id `s1` and
    person_refusal "" -> `first` stays empty.
  - `test_a_background_session_records_nothing` (person_refusal returns a reason).
  - hooks: `test_the_guard_notes_a_call_naming_inbox_and_decides_nothing` (output empty, note written).
- [ ] **Step 2: Run** `uv run pytest tests/test_watch_shown.py tests/test_watch_inbox_command.py tests/test_hooks.py
  -q` - Expected: FAIL.
- [ ] **Step 3: Implement** the three modules, the CLI parser (`flotilla inbox [--root .]`), the guard's note.
- [ ] **Step 4: Run** - Expected: PASS; then `uv run pytest -q`.
- [ ] **Step 5: Commit** `feat(inbox): the person's queue as a listing; only the person's own run counts as shown`.

### Task 6: The prompt hook - the model's count every prompt, the person's line past the model

**Files:**
- Modify: `flotilla/hooks.py:_prompt` (lines ~222-270), `flotilla/watch/shown.py` (`line_due`,
  `remember_line`, `remember_prompt`; docstring carries Task 1's measurement with the version)
- Test: `tests/test_hooks.py`

**Interfaces:**
- Consumes: `inbox.collect/counts` (Task 3), `attention.mode` (Task 4), `shown.mark/load` (Task 5), the person-prompt
  marker from Task 1.
- Produces:

```python
def line_due(state: dict, entries, *, mode: str, every: int, now: dt.datetime) -> bool
def remember_line(state_dir, repo_key: str, entries, now: dt.datetime) -> None
def remember_prompt(state_dir, repo_key: str, now: dt.datetime) -> None
def person_line(entries) -> str     # in inbox.py: "flotilla: 104 wait on you (3 urgent; 3 a yes to ship, 2 checks
                                    # only you can make, 99 put off) - flotilla inbox"; "" when empty
def model_line(entries) -> str      # in inbox.py: the same counts and "answer the person's status questions from
                                    # `flotilla inbox`; never say nothing waits while this count is above zero"
```

Rules: `all` - due when the set of entry keys differs from the line last shown, or the last person prompt was more
than one hour ago; `digest` - due when `every` minutes passed since the line last showed, or a blocker key is not in
the last line's blockers. Empty queue: not due, and the stamp is cleared. One state per project (repo key), not per
session. Entries covered by the queue (`PERSON`, `QUESTION` items and rig requests) leave the orchestrator's
"standing" fold. The model's line is in the orchestrator's context on every prompt, outside `throttle.due`. Output
is the hook's JSON (`systemMessage` + `hookSpecificOutput.additionalContext`) whenever there is a line to show;
otherwise today's plain text. "Shown" (`shown.mark` for the line's keys) is recorded only when the prompt is the
person's per Task 1 (else only in an interactive session).

- [ ] **Step 1: Failing tests** (`call("prompt", ...)` from `tests/test_hooks.py`; parse JSON when present):
  - `test_person_waits_are_counted_on_every_prompt_and_never_folded` - the trace's shape: 20 rows waiting on the
    person (last wait by sender, judge, main), one held until the person, one parked in words; four prompts at 5,
    50, 51 and 200 minutes. Every one: `additionalContext` holds "22 wait on the person" (or the agreed model
    wording) and never "standing item(s)" for those rows. This test fails on 0.25.0.
  - `test_the_persons_line_shows_once_until_the_queue_changes` (mode all; prompt 2 no `systemMessage`; add a row
    -> prompt 3 has it).
  - `test_the_line_comes_back_after_an_hour_away`.
  - `test_digest_waits_for_its_period_but_not_for_a_blocker`.
  - `test_a_seat_gets_the_line_but_no_instruction` (main session prompt: `systemMessage` present, no "status
    questions" text in context).
  - `test_a_prompt_that_is_not_the_persons_records_nothing` (per Task 1's marker; or a background session).
  - `test_the_line_is_one_per_project` (two sessions of one project; second within the hour gets no line).
- [ ] **Step 2: Run** `uv run pytest tests/test_hooks.py -q` - Expected: the new tests FAIL; adjust
  `test_the_orchestrator_is_told_what_is_new_and_the_rest_in_one_line` to the new contract (person waits are counted
  in their own line, other items still fold).
- [ ] **Step 3: Implement** `line_due`, `remember_*`, `person_line`, `model_line`, the new `_prompt`.
- [ ] **Step 4: Run** - Expected: PASS; then `uv run pytest -q`.
- [ ] **Step 5: Commit** `fix(hooks): what waits on the person reaches the person; never folded into standing`.

### Task 7: `watch --once` in counted sections

**Files:**
- Modify: `flotilla/watch/commands.py` (the `--once` printing), `flotilla/watch/render.py` (a `sections(items, now)`
  helper)
- Test: `tests/test_watch_commands.py` (or the file holding `watch --once` tests)

**Interfaces:**
- Produces: `render.sections(items, now) -> list[str]`: headings `waits on the person (N):`, `moves nobody can make
  (N):` (NOBODY), `gone sessions (N):` (deviations `mover_gone`, `wait_gone`, SEATS), `other (N):`; empty sections
  are left out. `--wait` output is unchanged.

- [ ] **Step 1: Failing test** `test_once_prints_counted_sections`: items of kinds person, nobody, deviation
  (`who="mover_gone"`), dropped -> output holds the four headings with `(1)` each, in that order.
- [ ] **Step 2: Run** - Expected: FAIL.
- [ ] **Step 3: Implement** `sections`; `--once` prints it instead of one `attention:` list (exit codes unchanged).
- [ ] **Step 4: Run** - Expected: PASS; then `uv run pytest -q`.
- [ ] **Step 5: Commit** `feat(watch): --once counts the person's waits, moves nobody can make and gone sessions apart`.

### Task 8: Putting off a wait on the person

**Files:**
- Modify: `flotilla/ledger/steering.py:79-105` (`hold`)
- Test: `tests/test_ledger_steering.py` (or the file holding hold tests)

**Interfaces:**
- Produces: `hold(..., until="the person")` accepts any open, non-reserved row whose `waiting_on` is the person, in
  any state; every other hold keeps `HOLDABLE`. The PR-queued refusal stays.

- [ ] **Step 1: Failing tests**: `test_a_judges_check_on_a_shipped_row_can_be_put_off`,
  `test_a_question_on_a_claimed_row_can_be_put_off`, `test_a_hold_until_a_branch_still_needs_a_holdable_state`
  (claimed row, until another branch -> refused as today), `test_a_put_off_shipped_row_stays_out_of_the_batch`
  (`ledgerkit.drive` to shipped; put off; the batch/brief does not list it), `test_putting_off_needs_a_why`.
- [ ] **Step 2: Run** - Expected: the first two FAIL.
- [ ] **Step 3: Implement** the widened check.
- [ ] **Step 4: Run** - Expected: PASS; then `uv run pytest -q`.
- [ ] **Step 5: Commit** `feat(ledger): a wait on the person can be put off in any state, and stays visible`.

### Task 9: Orchestrator template, onboarding, docs, version

**Files:**
- Modify: `templates/posts/orchestrator.md` (rule; `template_version: 24`), `skills/onboard/SKILL.md` (ask the
  mode; hand the person `! flotilla attention <mode>`), `skills/status/SKILL.md` if it answers "what needs me",
  `USER_MANUAL.md`, `README.md` (command list), `CHANGELOG.md`, `pyproject.toml` + `uv.lock` +
  `.claude-plugin/plugin.json` -> 0.26.0, any test that pins the template version or the command list.

Orchestrator rule (exact copy): "When the person asks what needs them or how things stand, run `flotilla inbox` and
answer from it. The count line flotilla gives you each prompt is the queue; never say nothing waits on the person
while it is above zero. Put a wait off only with `flotilla work hold <branch> --until \"the person\" --why \"...\"`;
a wait parked in words is still waiting."

- [ ] **Step 1:** Edit the files; run `uv run pytest -q` - Expected: failures only in tests pinning the version /
  command list; update them.
- [ ] **Step 2: Run** `uv run pytest -q` - Expected: all pass. `uv lock` after the version bump.
- [ ] **Step 3: Commit** `docs: the orchestrator answers from the inbox; onboarding asks the attention mode; 0.26.0`.

Not built (spec, "Left to the builder"): the `watch --wait` wake on "not shown for 24 h" - ruled out for this
release; the line on the person's screen already carries what was never shown.
