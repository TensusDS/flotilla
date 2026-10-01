# Field test 4: worldcore, flotilla 0.4.3

A fresh project set up the way README.md tells a newcomer, to try 0.4.x on a working fleet before the plugin
directory submission: the person-only moves (onboarding answers and write with `--confirm`, permission answers with
`--mark`, `work approve` where a person authorizes merges), the narrowed MCP set per post, retire's leftover handling.

## Setup

- Project: `worldcore`, a renderer-free physics core for generated worlds, seeded from twosuns `src/core` at
  `ac92dd4` (54 modules, 37 test files green, 458 tests); ten test files mixing core and game wiring set aside in
  `tests/twosuns-wiring/` as task 0. Brief: `docs/brief.md` in that repository.
- Origin: a local bare repository `/home/max/workspace/worldcore-origin.git`.
- Same machine as the running twosuns fleet (about 8.5 GB available at the start).

## Findings

W1. **README drift - installing at project scope does not commit a marketplace entry when the marketplace is already
known to the user.** README says `--scope project` "commits a marketplace entry that points at this repository, so
everyone who opens the project and trusts it installs flotilla from here". In worldcore,
`claude plugin install flotilla@flotilla --scope project` wrote only `{"enabledPlugins": {"flotilla@flotilla": true}}`
to `.claude/settings.json`; the marketplace had been added at user scope first, as the README's own first command
does. A teammate opening the project on another machine gets no marketplace to install from. To check: what a
project-scope install writes on a machine without the marketplace; then the README says what is true, or the install
step adds `extraKnownMarketplaces` to the project settings by hand.

W2. **gap - the composition onboarding suggests has no orchestrator and no sender.** `suggest_composition` writes
`[fleet.default] main = 1, review = 1, judge = 1` (onboard/questions.py), and `spawn` warns about a missing
orchestrator or sender only above `SOLO_ABOVE` sessions (fleet/compose.py). In worldcore the person answered
"ask" for permissions and "a human, per batch" for merges: a fleet raised from that default has nobody to route a
permission question to (each is refused at once), nobody who may `queue`, `land` or `ship`, and nobody to show the
person a batch to approve. README's step 2 says `/flotilla:spawn` with no arguments "raises the composition onboarding
suggested". Fix direction: the default includes an orchestrator whenever permissions are `ask` or merges need a
person, and a sender whenever work reaches trunk through the fleet; spawn refuses, not warns, a fleet that cannot
deliver.

W3. **defect - the model question accepts free text that becomes a broken `--model`.** The person answered the
`model` question in words ("orchestrator and reviewers on Opus, the rest on Sonnet"); the onboarding session saw that
`flotilla` would pass the whole text to `claude --model` and no seat would start, and worked around it by writing
`model: claude-opus-5-5` into orchestrator.md and reviewer.md and `[fleet] model = "claude-sonnet-5-5"`. Nothing in
flotilla refused the answer. Fix direction: validate the answer as a model name or an option value, and offer the
per-post split the person asked for ("strongest for the orchestrator and reviewers") as an option.

W4. **friction - the line-edit guard refuses `sed -i '0,/re/s//x/'`.** The onboarding session's edit was refused as a
line-number edit; `0,/re/` is GNU sed's "up to the first match", addressed by content. The session fell back to its
edit tool, so nothing broke, but the refusal is wrong for that form. Fix direction: read `0,/re/` (and `N,/re/` where
the end is a pattern) as content-addressed, or say in the refusal that a pattern-ended range is allowed in another form.

W5. **friction - the next steps onboarding prints mislead a project with no CI.** The session told the person to
commit `.flotilla/` (but not to push it - rules are read from origin's trunk) and to set a CI gate command "or nothing
checks shipped", for a project whose origin is a local bare repository with no CI at all; the CI question offered
"set it later" but no "there is no CI". Fix direction: the closing message says "commit and push `.flotilla/` to
origin's trunk", and the CI question has a "no CI" answer that the profile and `ship` understand.

## What 0.5.0 does about them

- W1: the README adds the marketplace with `--scope project`, so the project's settings carry the marketplace entry
  the README promises (decision 194).
- W2: the suggested composition always carries an orchestrator, a sender and a main session (decision 190).
- W3: a model answer must be a model name; a sentence is refused with the options named. The per-post split stays
  the existing "strongest reviewer" option; an orchestrator on a stronger model is a post's own `model` line.
- W4: `0,/re/` and `1,/re/` pass the line-edit guard; a counted start (`3,/re/`) is still refused (decision 193).
- W5: quick onboarding answers "no CI" where there is no GitHub CI, and onboarding publishes the profile itself:
  `flotilla onboard publish` commits, runs the tiers over the commit and pushes trunk; `write` names it (decision 190).

Beyond the findings, onboarding was simplified on the person's request: quick or custom, one yes, and an offer to
lead the fleet from the onboarding session (decisions 190-192).
