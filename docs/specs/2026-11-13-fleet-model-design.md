# The fleet's model: named by the profile, borrowed for a task, and a seat stopped on a usage limit (design)

Date: 2026-11-13. Asked by the person after a field fleet stopped for hours on 2026-11-13; the scenarios in section 3
are theirs.

## 1. What went wrong (field fleet, 2026-11-13, flotilla 0.31.0)

- The project profile said `[fleet] model = "one"`. By `fleet/launch.py` (`FLEET_MODEL_WORDS`, `model_for`) that
  means no `--model` flag: a seat runs whatever Claude Code starts with.
- Days earlier the person had asked the orchestrator for a session on a stronger, costlier model (Fable) for one
  analysis. The switch landed in the user's Claude Code settings (`~/.claude/settings.json`, `"model": "fable"`):
  Claude Code's `/model` saves its choice as the default for new sessions unless the person presses `s` in the
  picker. Nobody switched it back.
- Every seat raised after that ran on Fable. Neither the person nor the orchestrator knew.
- At 17:05-18:17 UTC every background seat hit the model's weekly limit ("You've hit your Fable limit · resets Nov
  18, 6pm (UTC)"). `flotilla fleet` showed them `alive (blocked)` or `alive (idle)`; `flotilla watch` raised only
  `mover_gone` and "not working (census: idle)". Letters were delivered, woke the seats, and were answered with the
  limit line. The person's "yes" to land a batch at 17:31 was never acted on, and nothing in the ledger said why.
  The orchestrator replaced two seats it read as gone; the replacements inherited the same model and stopped at
  once.
- The cause was found only by reading each seat's screen by hand, at the person's prompt.

The workaround: the person set the model back in Claude Code's settings, retired every limited seat, spawned new
ones, moved each row with `work adopt --from/--to`, and re-sent the letters by hand.

## 2. Intent

The model a fleet runs on is the fleet's decision, made by the person and written in the project's profile - never
an accident of what Claude Code last saved. A costlier model is a tool the person lends to one task, and it goes
back when the task is done. A seat that cannot work because its model's limit is spent says so, loudly, and is not
mistaken for an idle or dead one.

The person's rule for where the model comes from: a single Claude Code session outside a fleet takes Claude Code's
own setting; work of the fleet takes the model the plugin's settings name as the default.

## 3. The person's scenarios

**A - a stronger model borrowed for a task.** The profile names Opus; the fleet runs on it. The person asks the
orchestrator for a session on Fable, or to "switch" one, to work on a given problem. The orchestrator, asking the
person what for when the context does not say, raises a new seat on Fable and gives it the person's words about the
problem, its own context, and what to do, research or check. When the task is done, the orchestrator closes that
seat. Nothing inherits Fable silently: a session it raises for its task, and its subagents, run on the default model
or lower. ("Switch" is the same move: the existing seat keeps its work on the default model; nothing is switched in
place - the person did not need the conversation carried over.)

**B - the costlier model is the default.** The profile names Fable; the fleet runs on it. Nothing is switched and
nothing is said. Sessions raised by other sessions may run on lower models when their posts say so.

**C - a manual override.** The profile names Opus; the person sets Fable on their own session by hand (`/model`).
That is in effect an override, and Claude Code saves it as its default for new sessions. The person then gets, once
an hour, a note from the orchestrator: the fleet's work runs on a model other than its default. The note offers
three ways out: make it the default (the profile changes), mute these notes, or switch back to the default. It is a
soft guard against forgetting, not a refusal.

## 4. Goals, non-goals, anti-goals

Goals:
1. **Pinned.** Every seat flotilla raises gets `--model` on its command line: its post's own model, else the
   strongest model for an accepting post under `reviewer-strongest`, else the profile's `[fleet] model`. A change
   to Claude Code's saved default never reaches a seat.
2. **The default is named.** `[fleet] model` names a model. "Inherit what Claude Code runs" stops being a fleet
   setting.
3. **An update that changes a setting's meaning talks to the person.** After an update, a decision the new release
   needs from the person is asked by the orchestrator at its start, plainly saying what changed; `spawn` raises
   nothing while a decision that blocks it is open. The first such decision is the fleet's default model (section
   5.2).
4. **Borrowed for a task.** The orchestrator has a supported move to raise a seat on another model, tied to a task.
   The seat is marked as borrowed in the census and `flotilla fleet`; its helpers and subagents run on the default
   model or lower; when its task is handed back and it is still up, `watch` reminds the orchestrator to close it.
5. **The override is noticed.** When the work of the fleet - the orchestrator's own session included - runs on a
   model other than the default, the person is told once an hour with the three ways out of scenario C.
6. **Limited is a state.** A seat stopped on a usage limit is shown as `limited until <time>` (or `limited, reset
   unknown`) in the census, `flotilla fleet` and `flotilla status`; `watch` raises it as its own item, and all seats
   limited at once as one item that blocks the person's work; rows whose next move belongs to a limited seat are
   marked; the orchestrator is told not to replace a limited seat on the same model.

Non-goals (`TODO.md`, each its own release):
- choosing a model per task on the fly: the plugin describes classes of task, their weight and the model each is
  pinned to, and the orchestrator picks for a seat's task (the person's idea, 2026-11-13);
- moving a whole fleet to another model in one command, keeping its rows and letters (the field report's 4th ask);
  with seats pinned it is needed only when the default model itself is limited, and retire, spawn and `work adopt`
  do it by hand today;
- switching a running seat's model in place (Claude Code offers no supported way from outside a session).

Anti-goals - it fails, even if it works, if:
- flotilla writes Claude Code's own settings (`~/.claude/settings.json` or a project's `.claude/settings*.json`) to
  switch a model back: the person does that, or flotilla prints the line for them;
- flotilla reads another session's transcript to learn its model or its limit (the plugin policy, stated in
  `fleet/strangers.py`: a transcript holds the conversation);
- a seat raises a seat on a costlier model than the default by its own choice, unseen by the person;
- a fleet that names its model and never borrows another behaves differently, apart from the `--model` flag now
  always passed;
- `spawn` stands still with no line saying what it waits for and the exact answer that frees it.

## 5. Design

### 5.1 The model on every launch

`launch.model_for` returns a model always: the post's own (`model` other than `inherit`); else `STRONGEST` for a post
that may `accept` under `reviewer-strongest`; else the profile's default. `argv` always passes it. The dry run prints
the same command, built by `argv` rather than by a copy of its logic (`fleet/commands.py` keeps one today that can
drift). `flotilla fleet` and the census show each seat's model, as recorded at its raise (5.4).

Where the profile's default comes from:
- `[fleet] model = "<name>"`: that name. The strongest-reviewer choice becomes a named model too: `[fleet] model =
  "reviewer-strongest"` reads as a default to be named (5.2) plus `[fleet] reviewers = "opus"` - the model
  `STRONGEST` gives them today, so no reviewer moves to another model with the update. "The strongest" is no longer
  a moving word: a newer, costlier model reaches reviewers only when the person names it. *(Builder's call, from the
  builder check: "strongest" tracking the newest model would move every such fleet's reviewers onto a
  weekly-limited model unasked - the incident's pattern.)* Reviewers on their named model are the profile's
  policy: neither borrowed nor an override.
- `"one"`, `""`, or no key: no default. `spawn` refuses, saying the fleet's model is not named, which model each
  post would get once it is, and the exact line that names it (5.2).

Onboarding asks for a model by name. Its options name models (the strongest, Opus, Sonnet) and keep free text; "one
model, whichever Claude Code runs" is gone.

### 5.2 Decisions an update owes the person

A small registry in code: each owed decision has an id, a check of the project's profile (owed or not), whether it
blocks `spawn`, the question in plain words saying what changed and why, and the move that answers it. The checks
run:
- in the orchestrator's SessionStart, where each owed decision is put to the session with the instruction to ask
  the person (AskUserQuestion), and repeated with the fleet's items until answered;
- in `flotilla doctor`, as a finding;
- in `spawn`, which refuses while a blocking decision is owed, naming it and the answer's exact line.

The answer is the person's move, refused from a seat's tool call like `attention` and `--lead`, so a seat cannot
answer for the person.

The first decision, `fleet-model`: owed while `[fleet] model` names no model. The question: "flotilla no longer lets
the fleet inherit the model Claude Code last saved: on <date> a fleet ran for hours on a model switched for one
task, until its limit stopped it. Which model should the fleet run on by default?", with the models offered and
the model Claude Code's settings name now shown as information, never preselected.

The default is a setting of the project (the person, 2026-11-13): it lives in the profile, the same for everyone
who works on the project, and changes by a commit. `spawn` reads the profile from origin's trunk, which a fleet
landing work may not let a commit reach for a while; so the answer does two things *(builder's call)*:
1. it writes `[fleet] model` in the main checkout's profile, for the person to land as any profile change (`onboard
   publish`, or the project's flow), and says so;
2. it records the same name in a local, uncommitted note of this machine for this project, which `spawn` reads
   while trunk's profile still names no model, or names the model the note replaced. Once trunk's profile names a
   model of its own, trunk wins and the note is dropped; `spawn` says which source it took the model from.

*(The command's name and form are the builder's call, e.g. `flotilla fleet model <name>`.)*

### 5.3 A seat on a borrowed model

`flotilla spawn <post> --model <name> --for <task>` raises one seat of that post on `<name>`:
- `--for` is required with `--model` and is the task: a row's branch, or a short text when the task is a question
  with no row;
- the seat owns no row of its own: like a helper, it works beside the row it is for (its tree cut from that branch,
  or from trunk for a question) and reports to the orchestrator; the row stays with its owner *(builder's call, from
  scenario A: "switch" leaves the existing seat its work)*;
- it is the orchestrator's move: a seat that is not the orchestrator is refused *(builder's call: by post, through
  `posts.MOVES`)*; the person may run it too;
- the seat's first prompt is the orchestrator's brief (the person's words, its context, what to do), as `helper
  raise --task` takes one today;
- the raise is recorded with the model, `borrowed`, and the task (5.4);
- its subagents are held to the default: the seat's `--settings` carries `CLAUDE_CODE_SUBAGENT_MODEL=<default>` and
  `CLAUDE_CODE_SUBAGENT_MODEL_FORCE=1` (documented by Claude Code; the force needs 2.1.257 or later, and on an older
  Claude Code `spawn` says the cap does not hold);
- a helper it raises (`helper raise`) runs on its post's model or the default, never on the borrowed one;
- while it is up, the person's attention line counts it ("1 seat on fable for <task>") - a line of its own, not an
  entry of the queue that "waits on you" *(builder's call)*;
- when its task is handed back - it told the orchestrator it is done (a ledger move of its own, e.g. `flotilla
  helper done`-like, the builder's to name) or the row it is for is closed - and it is still up, `watch` raises an
  item for the orchestrator: close it (`flotilla retire`). The orchestrator closes it; flotilla never retires it by
  itself (the person's ruling).

A borrowed model at or below the default needs no `--for` and is not "borrowed" *(builder's call: no order between
models is known to flotilla - so in fact every `--model` other than the default is treated as borrowed)*.

### 5.4 What is recorded

The `reserve` event of a seat's row, written before the launch, carries in its `evidence` the `model` and, for a
borrowed seat, `borrowed: true` and `for` *(builder's call, from the builder check: `make_event` refuses fields
outside `ROW_FIELDS`, and `launched` is written only when the census finds the seat in time)*. The census, `flotilla
fleet` and `flotilla status` read the model from there. Seats raised before this release show `model unknown`.

Model names are compared by family - an alias (`opus`), a full id (`claude-opus-5-5`) and a variant (`opus[1m]`)
of one family match - and two full ids of one family are compared exactly *(builder's call)*.

### 5.5 The override noticed (scenario C)

flotilla reads, never writes:
- the `model` key of Claude Code's user settings (the default Claude Code will start new sessions on);
- the model a session reports at its start where Claude Code gives one (the SessionStart hook's `model` field,
  documented as not always present).

When either names a model other than the fleet's default while the person leads the fleet (a live lead), the
orchestrator's session gets a note, and the person's attention line a line of its own, at most once an hour: "the
fleet's default is opus; <where> runs on fable". Without a live lead nothing is said: the person's sessions outside
the fleet take Claude Code's own setting, by their rule *(builder's call, from the builder check: a person who keeps
Claude Code on another model for solo work would otherwise be noted every hour)*. A switch kept to one session (`s`
in the picker) after the session started is not seen: flotilla reads no transcript. Accepted gap; the orchestrator's
post asks it to name the model it runs on when it starts, and the note uses that word too *(builder's call)*. The
three ways out:
1. make it the default: the 5.2 answer with the new name;
2. mute: `flotilla fleet model --mute <hours>` or until the model changes *(builder's call on the form; the mute is
   the person's move)*;
3. switch back: the line to type, `/model opus` in the orchestrator's session (press `s` in the picker to keep it to
   that session), and a note that Claude Code saves `/model` as its default otherwise.

Borrowed seats (5.3) are not an override: they are counted, not noted.

### 5.6 A seat stopped on a usage limit

A seat learns it is limited from its own hook, not from its transcript: Claude Code's `StopFailure` hook fires when a
turn ends on an API error, with the matcher `rate_limit` among its reasons. flotilla registers it; the hook writes a
small record in the state directory: the session, the time, and the reset time when the payload or the error text
carries one. The next turn that ends normally (`Stop`) clears it.

Then:
- the census and `flotilla fleet` show `limited until <time>` (or `limited, reset unknown`) instead of `alive
  (blocked)`/`(idle)`; `flotilla status` marks the rows whose next move is the limited seat's;
- `watch` raises a `limited` item per seat, and one item when every live seat of the fleet is limited - urgent, a
  blocker for the person, since the fleet does nothing until the reset or a change of model;
- `spawn` refuses to raise a seat that would stop at once: on the model a live seat is limited on when the limit
  names a model ("your Fable limit"), on any model when it is the account's (a session or weekly limit naming no
  model), and only warns when the limit's scope cannot be read *(builder's call)*; for a model's limit, `--model`
  on another model is the way past it;
- the orchestrator's post says: a limited seat is not gone; do not retire or replace it on the same model; tell the
  person;
- a letter to a limited seat: the census line of the receiver says it is limited, and the sender's rows waiting on
  it are marked, so "delivered" no longer reads as "acted on".

*(If the `StopFailure` payload proves not to carry the reason or the reset time on the Claude Code versions in use,
the builder says so before building 5.6 on a guess.)*

## 6. What is left to the builder

- names of commands, keys, events and item kinds, and the exact wording of every line;
- how the owed-decision registry is laid out in code, and where the hourly note's clock is kept;
- how a borrowed seat's "task handed back" is read for a question with no row;
- the form of `spawn`'s refusals, as long as each names what it waits for and the line that frees it.

## 7. Testing

- `argv` always carries `--model`: post model, strongest for accepting posts under the strongest-reviewer choice,
  default otherwise; the dry run equals `argv`.
- A profile with `"one"`, `""` or no model: `spawn` refuses with the line; doctor finds it; the orchestrator's start
  puts the question; the answer from a seat's tool call is refused; the person's answer writes the profile and
  `spawn` goes.
- `spawn --model X` without `--for` refused; from a non-orchestrator seat refused; the seat's settings carry the
  subagent cap; its `helper raise` runs on the default; the attention line counts it; `watch` raises "close it" once
  its row is handed over, and not before.
- Claude Code's settings naming another model with the fleet working: one note an hour, not more; mute holds; none
  for a borrowed seat; none when no fleet works.
- A `StopFailure` payload with `rate_limit` writes the record; `Stop` clears it; census, fleet and status show
  limited; one seat raises one item, all seats one blocker; `spawn` on the limited model refused, on another
  allowed.
- A fleet that names its model and never borrows: nothing else changes (the existing suite, plus the flag).
