---
name: onboard
description: Set up flotilla in a repository — measure this machine, show what the repository already declares (test commands, CI, releases), take the recommended settings or ask the few questions only a person can answer, run each test tier once, write .flotilla/project.toml, publish it to trunk, and offer to raise the fleet with this session as its orchestrator. Use when someone asks to onboard, set up, configure or initialize flotilla, to prepare a repository for a fleet of Claude Code sessions, or to re-check an existing flotilla profile for drift.
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/scripts/flotilla onboard check), Bash(${CLAUDE_PLUGIN_ROOT}/scripts/flotilla onboard machine), Bash(${CLAUDE_PLUGIN_ROOT}/scripts/flotilla onboard detect), Bash(${CLAUDE_PLUGIN_ROOT}/scripts/flotilla onboard next)
disable-model-invocation: true
---

# Onboard a repository to flotilla

flotilla stays inactive in a repository until `.flotilla/project.toml` is on origin's trunk. This skill takes the
person from nothing to a working fleet. The command line decides which questions apply and checks every answer;
your job is to ask, faithfully, and to report what the commands print.

Run every command from the repository root. The CLI is `${CLAUDE_PLUGIN_ROOT}/scripts/flotilla`.

`answer`, `quick`, `write`, `publish` and `reset` are not pre-approved: Claude Code asks the person before each one
runs, on purpose - a `tiers` answer is a shell command that `write` runs. They are refused from a background
session: onboarding is the person's.

## 0. Is the repository onboarded already?

If `.flotilla/project.toml` exists, do not start over. Run `flotilla onboard check` and report what it prints (exit
3 means some things could not be verified, not that they match). Then ask the person whether to keep the profile or
re-onboard; only on a clear request to replace it continue below, and use `write --force` in step 4.

## 1. Measure the machine

Run `flotilla onboard machine`. If it prints `fail  python3`, stop and tell the person exactly what it printed:
hooks run through the `python3` on PATH, and nothing else works until that is 3.11 or newer.

## 2. Show what was found

Run `flotilla onboard detect`. Summarize it in a few lines: trunk, test commands and where each came from, CI and
whether its jobs came from a real push run or only from workflow files ("unverified"), and any notes.

## 3. Quick or custom

Ask the person with AskUserQuestion, header "Setup", question "How do you want to set flotilla up?":

- **"Quick (Recommended)"** - "flotilla takes what it found and the recommended answer to every other question:
  every branch reviewed, every permission asked of you, every guard on. You confirm the result once."
- **"Custom"** - "Answer each question yourself: how work reaches trunk, review, permissions, tests, CI, models."

**Quick:** run `flotilla onboard quick`. It records the recommended answer to every question that applies and prints
one line per answer. Go to step 4.

**Custom:** loop until `next` says `done`:

1. Run `flotilla onboard next`. If `done` is true, go to step 4.
2. Ask the returned questions with AskUserQuestion, in one call: use each question's `question`, `header`,
   `options` (label and description) and `multi_select` exactly as given. Do not add, drop or reorder options.
3. Record each answer with `flotilla onboard answer <id> <value>`: use the option's `value`, not its label. For a
   multi-select question pass every chosen value. When the person typed their own text (Other) and the question
   has `free_text`, pass the text itself as the value.
4. If `answer` refuses a value, show the person the refusal and ask that question again.

Answers are stored between sessions; `flotilla onboard reset` forgets them.

## 4. Write the profile

Run `flotilla onboard write`. First it prints the whole profile it will write, then every shell command in it (each
test tier, the gate, the revision command), and a mark, and does nothing else.

- **Quick:** show the person the answers `quick` printed, as a short list, and every shell command exactly as
  `write` printed it; offer the whole profile if they want to read it.
- **Custom:** show the profile and the commands exactly as printed.

Ask whether it is right. Only when the person says yes, run `flotilla onboard write --confirm <mark>` with the same
other options. That runs each test tier once, then writes the file. A changed answer changes the mark.

- If a tier is not green, it prints the last lines and writes nothing. Show those lines and ask the person whether
  the command is wrong (then record a corrected `tiers` answer and run `write` again) or the project is red right
  now (then run `write --keep-unmeasured`).
- If the profile already exists, it refuses. Re-onboard with `--force` only when the person asks to replace it.

## 5. Publish it

Every session reads its rules from origin's trunk, so the profile does nothing until it is there. Ask the person:
"Commit the profile and push it to trunk now?" On yes, run `flotilla onboard publish`: it commits `.flotilla/` (and
`.claude/settings.json` if it changed), runs the tiers over that commit - the receipt a guarded push asks for - and
pushes trunk. Report what it printed. If it says the tree has the person's own uncommitted changes, or that trunk only
takes pull requests, say so and what to do; do not push another way.

Then mention once: the git hooks that back the guards are installed with `/flotilla:guard`, one yes each.

## 6. Raise the fleet

Ask with AskUserQuestion, header "Fleet", question "Raise the fleet now?":

- **"This session leads it (Recommended)"** - "This session becomes the orchestrator: you talk to the fleet here,
  answer its permission questions and approve its merges here. The other sessions start in the background."
- **"A background orchestrator"** - "Every session starts in the background; you attach to the orchestrator to talk."
- **"Not now"** - "Raise it later with /flotilla:spawn."

**This session leads it:**

1. Run `flotilla spawn --lead`. It reserves the orchestrator's name for this session and prints it
   (`orchestrator N`); a note may come first.
2. Tell the person: "Type `/rename orchestrator N` (the name above) as your next message - only you can rename this
   session." Wait for it. Then run `flotilla fleet` - or `claude agents --json` - and check that this session now
   carries that name; if not, say what you see and stop.
3. Run `flotilla spawn --fill`: it raises the rest of the profile's default composition, not a second orchestrator.
   Report each seat it raised.
4. From now on you hold the orchestrator post: read `.flotilla/posts/orchestrator.md` on trunk and follow its
   instructions, and use the `flotilla:flotilla` skill for every ledger move. Tell the person: "Tell me what to
   build." Permission questions from the fleet reach you; put each to the person with `/flotilla:permit`. Where the
   person authorizes merges, show them each approve command and let them type it with `!` in front; your own Bash
   call of `flotilla work approve` is refused, by design.

**A background orchestrator:** run `flotilla spawn --default` and give the person the orchestrator's
`claude attach <id>` from what it printed.

**Not now:** tell the person that `/flotilla:spawn` raises the fleet when they are ready.

## 7. Confirm

Run `flotilla onboard check` and report its findings, if any. Tell the person the profile is theirs to edit; a
change takes effect once it is on origin's trunk.
