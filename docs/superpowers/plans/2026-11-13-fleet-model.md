# The fleet's model - Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** a fleet runs on the model its profile names, borrows another for one task at a time, says when it runs on
another by hand, and shows a seat stopped on a usage limit as limited.

**Architecture:** four releases, each its own branch, fresh review, fix pass and full suite through the lane, in this
order: (1) pinned model and the owed decision - the incident's cause; (2) the limited state - the incident's
silence; (3) borrowed seats; (4) the override note. One module, `flotilla/fleet/model.py`, owns every model rule
(names, families, where the default comes from, the local note); launch, spawn, hooks, watch and doctor ask it.

**Tech Stack:** Python >= 3.11, stdlib only; pytest through `uv run --with pytest`; the full suite through `bin/flotilla
lane run --for <branch> -- uv run --with pytest python -m pytest -p no:cacheprovider tests/`.

**Spec:** `docs/specs/2026-11-13-fleet-model-design.md` (approved 2026-11-13). Executors read it first.

## Global Constraints

- English only in code, docs, commits; no private names, no people's names, no home directories in tracked files.
- flotilla never writes Claude Code's own settings (`~/.claude/settings.json`, a project's `.claude/settings*.json`):
  it reads the user settings' `model` key, nothing more.
- flotilla never reads another session's transcript (`fleet/strangers.py` policy).
- The person's moves (`fleet model <name>`, `fleet model --mute`) are refused from any session's tool call: the
  guard in `flotilla/guards/person.py`, as for `attention`, plus `caller.person_refusal` in the command.
- Every refusal names what it waits for and the exact line that frees it.
- A fleet that names its model and never borrows behaves as before, apart from `--model` always passed.
- Each release: version bump (`.claude-plugin/plugin.json`, `pyproject.toml`, `flotilla/__init__.py`, README pins),
  CHANGELOG section, a decision in `docs/specs/2026-09-22-decisions-log.md`, README where behaviour shows.

## Review Focus

1. A profile on trunk still saying `model = "one"` while the person already answered on this machine: `spawn` takes
   the local note and says so; once trunk names a model, trunk wins. (Task 1.1, `test_trunk_wins_once_it_names_one`.)
2. A post file whose `model` is a full id or a variant (`claude-opus-5-5`, `opus[1m]`) against a default alias: the
   same family is not an override or a borrow. (Task 1.1, `test_family_matches_ids_and_variants`.)
3. A seat raised by an older flotilla (no model in its `reserve` evidence): shown `model unknown`, never an error.
   (Task 1.3, `test_seat_without_recorded_model_reads_unknown`.)
4. A `StopFailure` payload without a reason or a reset time: no record is guessed; the seat stays as the census says.
   (Task 2.1, `test_payload_without_reason_records_nothing`.)
5. The borrowed seat's "close it" item must not fire while its task is still open, nor twice. (Task 3.2,
   `test_close_item_only_after_done`.)

---

## Release 1 (0.32.0): the model is named, pinned, and asked for after the update

### Task 1.1: `flotilla/fleet/model.py` - names, families, the default and the local note

**Files:**
- Create: `flotilla/fleet/model.py`
- Test: `tests/test_fleet_model.py`

**Interfaces:**
- Produces:
  - `LEGACY = ("one", "reviewer-strongest", "")` - words that name no model.
  - `class ModelUnnamed(LaunchError)` (from `flotilla.fleet.launch`) - message names the line `! flotilla fleet
    model <name>`.
  - `family(name: str) -> str` - lowercased; `[...]` suffix dropped; a full id `claude-<family>-...` gives
    `<family>`; `opusplan` gives `opus`; otherwise the name.
  - `same(a: str, b: str) -> bool` - two full ids (`claude-` prefix) compare exactly; else `family(a) == family(b)`.
  - `NOTE = "fleet-model.json"` in `paths.state_dir()`: `{"<main checkout>": {"model": str, "replaced": str, "at":
    iso}}`.
  - `note_for(state: Path, main: Path) -> dict | None`, `write_note(state, main, model: str, replaced: str, now)`,
    `drop_note(state, main)`.
  - `default(profile: dict, *, state: Path, main: Path) -> tuple[str, str]` - `(model, source)`; source
    `"profile"` or `"local note (not on trunk yet)"`; `("", "")` when none. Trunk's `[fleet] model` outside
    `LEGACY` wins when it differs from the note's `replaced` (the note is then dropped); else the note.
  - `reviewers(profile: dict) -> str` - `[fleet] reviewers`, else `"opus"` when `[fleet] model ==
    "reviewer-strongest"`, else `""`.
  - `resolved(profile: dict, *, state: Path, main: Path) -> dict` - a copy whose `[fleet] model` is the default and
    `[fleet] reviewers` the reviewers' model; raises `ModelUnnamed` when there is no default.

- [ ] **Step 1: Write the failing tests**

```python
def test_family_matches_ids_and_variants():
    assert model.same("opus", "claude-opus-5-5") and model.same("opus[1m]", "opus") and model.same("opusplan", "opus")
    assert not model.same("claude-opus-5-5", "claude-opus-4-1") and not model.same("opus", "fable")

def test_legacy_words_name_no_default(tmp_path):
    for word in ("one", "reviewer-strongest", ""):
        assert model.default({"fleet": {"model": word}}, state=tmp_path, main=tmp_path / "p") == ("", "")
    assert model.default({}, state=tmp_path, main=tmp_path / "p") == ("", "")

def test_the_note_answers_until_trunk_names_one(tmp_path):
    main = tmp_path / "p"
    model.write_note(tmp_path, main, "opus", replaced="one", now=NOW)
    got = model.default({"fleet": {"model": "one"}}, state=tmp_path, main=main)
    assert got == ("opus", "local note (not on trunk yet)")

def test_trunk_wins_once_it_names_one(tmp_path):
    main = tmp_path / "p"
    model.write_note(tmp_path, main, "opus", replaced="sonnet", now=NOW)
    assert model.default({"fleet": {"model": "sonnet"}}, state=tmp_path, main=main)[0] == "opus"   # not landed yet
    assert model.default({"fleet": {"model": "haiku"}}, state=tmp_path, main=main) == ("haiku", "profile")
    assert model.note_for(tmp_path, main) is None

def test_strongest_reviewer_keeps_opus():
    assert model.reviewers({"fleet": {"model": "reviewer-strongest"}}) == "opus"
    assert model.reviewers({"fleet": {"model": "opus", "reviewers": "fable"}}) == "fable"

def test_resolved_refuses_without_a_default(tmp_path):
    with pytest.raises(model.ModelUnnamed, match="flotilla fleet model"):
        model.resolved({"fleet": {"model": "one"}}, state=tmp_path, main=tmp_path / "p")
```

- [ ] **Step 2: Run, expect FAIL** (ImportError):
  `uv run --with pytest python -m pytest -p no:cacheprovider -q tests/test_fleet_model.py`.
- [ ] **Step 3: Implement `flotilla/fleet/model.py`** as the interfaces say; the note is written atomically (tmp +
  `os.replace`), an unreadable note reads as none.
- [ ] **Step 4: Run, expect PASS.**
- [ ] **Step 5: Commit** `feat(fleet): one module for the fleet's model`.

### Task 1.2: every launch carries `--model`; the dry run prints `argv`

**Files:**
- Modify: `flotilla/fleet/launch.py` (`model_for`, `argv`, `FLEET_MODEL_WORDS` removed), `flotilla/fleet/spawn.py`
  (`raise_seat`, `_auto_unavailable`), `flotilla/fleet/commands.py` (dry run, `_spawn`)
- Test: `tests/test_fleet_launch.py`, `tests/test_spawn.py` (the existing files that test these; find them with
  `grep -ln model_for tests`)

**Interfaces:**
- Consumes: `model.resolved`, `model.reviewers`.
- Produces: `launch.model_for(profile: dict, post) -> str` on a resolved profile: post's own model (not `inherit`);
  else `[fleet] reviewers` for a post that may `accept` when set; else `[fleet] model`. Never "".
  `launch.shown(command: list[str]) -> str` - `shlex.join` of the command with the values of `--settings`,
  `--append-system-prompt` and the first prompt replaced by `<...>`.

- [ ] **Step 1: Failing tests** - `test_argv_always_has_model` (resolved profile, a post with `inherit` -> `--model
  opus`; a post with `model: sonnet` -> `sonnet`; an accepting post under reviewers `opus` and default `sonnet` ->
  `opus`); `test_post_model_beats_reviewers`; `test_dry_run_prints_argv` (the dry run's command line equals
  `launch.shown(launch.argv(...))`); `test_spawn_refuses_without_a_default` (profile `model = "one"`, no note:
  `MoveRefused` naming `! flotilla fleet model <name>` and each post's would-be model is not printed as `""`).
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement.** `raise_seat` resolves `ledger.profile` with `model.resolved(..., state=paths.state_dir(),
  main=main)` before `argv`; `_spawn` resolves once at its top (dry run included) and prints `model: <name> (from
  <source>)`.
- [ ] **Step 4: Run the touched test files and `tests/test_fleet*.py tests/test_spawn*.py tests/test_helpers*.py`,
  expect PASS** (existing tests whose profiles say `one` get a named model in their fixtures - the behaviour they
  pin is otherwise unchanged).
- [ ] **Step 5: Commit** `feat(fleet): every seat is launched with --model`.

### Task 1.3: the model is recorded and shown

**Files:**
- Modify: `flotilla/fleet/spawn.py` (`raise_seat`: `evidence={"model": ...}` on the `reserve` append),
  `flotilla/fleet/retire.py` (`fleet_view`: `model` per item from the row's `reserve` evidence),
  `flotilla/fleet/commands.py` (`_fleet` prints `model <name>` or `model unknown`)
- Test: `tests/test_fleet_retire.py` or the file testing `fleet_view` (`grep -ln fleet_view tests`)

**Interfaces:**
- Produces: `spawn.recorded_model(ledger, row) -> str` - the `model` of the row's `reserve` event evidence, "" when
  none.

- [ ] **Step 1: Failing tests** - `test_reserve_records_the_model`; `test_seat_without_recorded_model_reads_unknown`;
  `test_fleet_lists_the_model`.
- [ ] **Steps 2-4:** run (FAIL), implement, run (PASS).
- [ ] **Step 5: Commit** `feat(fleet): the census of seats names each seat's model`.

### Task 1.4: `flotilla fleet model` - the person names the default

**Files:**
- Modify: `flotilla/cli.py` (`fleet` action choices gain `model`; a positional `name` nargs="?"),
  `flotilla/fleet/commands.py` (`_fleet` dispatch to `_model`), `flotilla/guards/person.py` (refuse `fleet model
  <name>` from a tool call, beside `attention`), `flotilla/onboard/tomlw.py` only if a one-key edit helper is needed
- Test: `tests/test_fleet_model_cmd.py`, `tests/test_guards_person.py`

**Interfaces:**
- Produces: `fleet/commands._model(ledger, args) -> int`. Without a name: prints `model: <name> (from <source>)` or
  `model: not named` and the line. With a name: refuses unless `caller.person_refusal("names the fleet's model")`
  is ""; writes `[fleet] model = "<name>"` in the main checkout's `.flotilla/project.toml` (keeping every other key;
  `reviewer-strongest` becomes the name plus `reviewers = "opus"`), writes the local note with `replaced` = trunk's
  current value, and prints that the profile change must land (`flotilla onboard publish`, or the project's flow).

- [ ] **Step 1: Failing tests** - `test_model_names_and_notes`; `test_model_keeps_other_keys`;
  `test_reviewer_strongest_becomes_named`; `test_model_refused_from_a_background_session` (person_refusal stubbed
  non-empty); guard: `test_fleet_model_from_tool_call_refused` (`flotilla fleet model opus` -> Finding refused;
  `flotilla fleet model` alone allowed).
- [ ] **Steps 2-4:** run (FAIL), implement, run (PASS) incl. `tests/test_guards_person.py`.
- [ ] **Step 5: Commit** `feat(fleet): the person names the fleet's model`.

### Task 1.5: decisions an update owes the person

**Files:**
- Create: `flotilla/onboard/owed.py`
- Modify: `flotilla/watch/fleet.py` (an `owed` item per open decision), `flotilla/watch/inbox.py` (`owed` maps to
  the person's urgent blocker group), `flotilla/doctor.py` (`_owed` finding), `flotilla/fleet/commands.py` (`_spawn`
  refuses while a blocking decision is owed - before counting), `flotilla/onboard/questions.py` and
  `flotilla/onboard/profile.py` (the model question names models: the options `opus`, `sonnet`, `fable` and free
  text; `one` gone; default answer `opus`)
- Test: `tests/test_owed.py`, plus the watch, inbox, doctor and onboarding test files

**Interfaces:**
- Produces: `@dataclass(frozen=True) class Owed: id: str; blocks_spawn: bool; question: str; answer: str` and
  `owed(profile: dict, *, state: Path, main: Path, settings_model: str = "") -> list[Owed]`. First entry, id
  `fleet-model`, owed while `model.default(...) == ("", "")`; its question is the spec's 5.2 text with
  `settings_model` shown as information ("Claude Code's settings name <x> now; that is not preselected"); `answer`
  is `! flotilla fleet model <name>`.

- [ ] **Step 1: Failing tests** - `test_fleet_model_owed_until_named`; `test_owed_item_reaches_orchestrator_and_inbox`
  (urgent, a blocker); `test_doctor_reports_owed`; `test_spawn_refused_while_owed` (message names the answer line);
  `test_onboarding_offers_named_models` (no `one` option; answers write a name).
- [ ] **Steps 2-4:** run (FAIL), implement, run (PASS).
- [ ] **Step 5: Commit** `feat(onboard): decisions an update owes the person, the fleet's model first`.

### Task 1.6: release 0.32.0

- [ ] Version bump, CHANGELOG (what the person sees: every seat on the named model; after the update the orchestrator
  asks for it once; `fleet` names each seat's model), decision 252, README ("Models" in onboarding and posts, the
  `fleet model` line), `TODO.md`: the person's idea of choosing a model per class of task, and the whole-fleet move
  to another model, each with its `*(checked ...)*` line. Orchestrator post template: after an update, put owed
  decisions to the person with AskUserQuestion. Commit, review, fix pass, full suite, merge.

## Release 2 (0.32.1): a seat stopped on a usage limit

### Task 2.1: the `StopFailure` hook records the limit

**Files:** Modify `hooks/hooks.json` (a `StopFailure` entry, matcher `rate_limit`, `hook stop-failure`),
`flotilla/hooks.py` (`EVENTS` gains `stop-failure`; `_stop_failure` writes the record; `_stop` clears it). Create
`flotilla/fleet/limits.py`. Test `tests/test_limits.py`, `tests/test_hooks.py`.

**Interfaces:** `limits.FILE = "limits.json"` in `paths.state_dir()`; `record(state, session_id, *, at, reset: str |
None, scope: str)` with `scope` in `("model:<family>", "account", "unknown")`; `clear(state, session_id)`;
`current(state, now) -> dict[str, Limit]` (records past their reset dropped); `@dataclass Limit(session_id, at,
reset, scope)`; `parse(payload: dict) -> Limit | None` - reason and text from the payload; `None` when it carries no
rate-limit reason.

- [ ] **Step 0: Verify the payload** before writing code: Claude Code's hooks reference (`StopFailure` input fields)
  through the claude-code-guide agent; if neither the reason nor the error text is documented, stop and report to
  the person (spec 5.6) - do not build on a guess.
- [ ] **Step 1: Failing tests** - `test_rate_limit_payload_records`; `test_payload_without_reason_records_nothing`;
  `test_model_limit_text_gives_model_scope` ("You've hit your Fable limit · resets Nov 18, 6pm (UTC)" ->
  `model:fable`, reset parsed); `test_session_limit_text_gives_account_scope`; `test_stop_clears`.
- [ ] **Steps 2-5:** FAIL, implement, PASS, commit `feat(fleet): a seat records the usage limit it stopped on`.

### Task 2.2: limited shown, raised, and respected

**Files:** Modify `flotilla/fleet/retire.py` / `flotilla/fleet/commands.py` (`limited until <time>` / `limited,
reset unknown` instead of `alive (...)`), `flotilla/ledger/views.py` (status marks rows whose next move is a limited
seat's), `flotilla/watch/fleet.py` (`limited` item per seat; one urgent `all-limited` blocker when every live seat
is limited), `flotilla/watch/inbox.py` (mapping), `flotilla/fleet/commands.py` (`_spawn` refuses per spec 5.6 by
scope; warns on `unknown`), orchestrator post template (a limited seat is not gone; do not replace it on the same
model; tell the person). Tests in the matching files.

- [ ] **Step 1: Failing tests** - `test_fleet_shows_limited`; `test_status_marks_rows_of_limited_seat`;
  `test_one_limited_seat_one_item`; `test_all_limited_one_blocker`; `test_spawn_refused_on_limited_model`;
  `test_spawn_refused_on_account_limit`; `test_spawn_warns_on_unknown_scope`.
- [ ] **Steps 2-5:** FAIL, implement, PASS, commit; then the release (version, CHANGELOG, decision, README), review,
  suite, merge.

## Release 3 (0.33.0): a seat on a borrowed model

### Task 3.1: `spawn <post> --model <name> --for <task>`

**Files:** Modify `flotilla/cli.py` (`spawn --model`, `--for`), `flotilla/fleet/commands.py` (`_spawn`: `--model`
needs `--for` and exactly one seat; refused unless the caller holds the orchestrator post or is the person),
`flotilla/fleet/spawn.py` (`raise_seat(..., model: str = "", borrowed_for: str = "")`: evidence `{"model",
"borrowed": True, "for"}`; settings JSON gains `env` `CLAUDE_CODE_SUBAGENT_MODEL=<default>`,
`CLAUDE_CODE_SUBAGENT_MODEL_FORCE=1`; tree cut from the `--for` branch when it names an open row, else trunk; first
prompt = the brief given with `--task`), `flotilla/fleet/plugins.py` (merge `env` into the settings JSON),
`flotilla/fleet/helpers.py` (a helper of a borrowed seat runs on the default). Claude Code older than 2.1.257:
`spawn` warns the subagent cap does not hold (version from `claude_state`).

- [ ] **Step 1: Failing tests** - `test_model_needs_for`; `test_borrow_refused_to_non_orchestrator`;
  `test_borrowed_seat_records_and_caps_subagents` (argv has `--model fable`; settings JSON has both env keys with
  the default); `test_borrowed_seat_owns_no_row` (the row of `--for` keeps its owner);
  `test_helper_of_borrowed_seat_runs_on_default`; `test_old_claude_warns_cap`.
- [ ] **Steps 2-5:** FAIL, implement, PASS, commit.

### Task 3.2: seen while up, closed when done

**Files:** `flotilla/watch/inbox.py` (person line: "N seat(s) on <model> for <task>", its own line, not a queue
entry), `flotilla/fleet/helpers.py` or a new `borrowed done --summary` move reusing `helpers.done`'s letter,
`flotilla/watch/fleet.py` (`borrowed-done` item for the orchestrator once the seat said done or its `--for` row
closed, while the seat lives; once per seat), the orchestrator template (how to borrow: ask what for, brief with the
person's words, its context, what to do; close it when done).

- [ ] **Step 1: Failing tests** - `test_person_line_counts_borrowed`; `test_close_item_only_after_done`;
  `test_close_item_when_for_row_closes`; `test_no_close_item_after_retire`.
- [ ] **Steps 2-5:** FAIL, implement, PASS, commit; release, review, suite, merge.

## Release 4 (0.33.1): the override noticed

### Task 4.1: reading the model the fleet's lead runs on

**Files:** Create `flotilla/fleet/override.py`; modify `flotilla/hooks.py` (`_session_start` stores the payload's
`model` for the session when present; the orchestrator template asks it to run `flotilla lead --model <name>` at its
start, which records the same), `flotilla/fleet/lead.py`.

**Interfaces:** `override.settings_model(home: Path) -> str` (reads `~/.claude/settings.json` `model`, "" on any
error, never writes); `override.lead_model(state, session_id) -> str`; `override.check(default: str, *, lead_live:
bool, settings_model: str, lead_model: str) -> str` - the note text, "" when nothing differs (by `model.same`) or no
lead lives.

- [ ] **Step 1: Failing tests** - `test_no_note_without_live_lead`; `test_note_when_settings_differ`;
  `test_note_when_lead_reports_other`; `test_family_match_is_no_note`; `test_settings_never_written` (file mtime and
  bytes unchanged).
- [ ] **Steps 2-5:** FAIL, implement, PASS, commit.

### Task 4.2: once an hour, three ways out, mute

**Files:** `flotilla/watch/inbox.py` / `flotilla/hooks.py` (`_prompt`: the note to the orchestrator and the person's
line at most hourly, clock in `paths.state_dir()/override.json`), `flotilla/fleet/commands.py` (`fleet model --mute
<hours>`, person-only; a mute ends early when the differing model changes), `flotilla/guards/person.py` (refuse
`--mute` from a tool call). The note's text gives the three ways: `! flotilla fleet model <new>`, `! flotilla fleet
model --mute <hours>`, and `/model <default>` in the orchestrator's session (press `s` to keep it to that session).

- [ ] **Step 1: Failing tests** - `test_note_at_most_hourly`; `test_mute_holds`; `test_mute_ends_when_model_changes`;
  `test_borrowed_seat_is_not_an_override`; `test_mute_from_tool_call_refused`.
- [ ] **Steps 2-5:** FAIL, implement, PASS, commit; release, review, suite, merge.
