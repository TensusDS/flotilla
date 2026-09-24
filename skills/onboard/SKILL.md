---
name: onboard
description: Set up flotilla in a repository — measure this machine, show what the repository already declares (test commands, CI, releases), ask the few questions only a person can answer, run each chosen test tier once, and write .flotilla/project.toml. Use when someone asks to onboard, set up, configure or initialize flotilla, to prepare a repository for a fleet of Claude Code sessions, or to re-check an existing flotilla profile for drift.
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/scripts/flotilla onboard *)
---

# Onboard a repository to flotilla

flotilla stays inactive in a repository until `.flotilla/project.toml` exists. This skill produces that file. The
command line decides which questions apply and checks every answer; your job is to ask them, faithfully, and to
report what the commands print.

Run every command from the repository root. The CLI is `${CLAUDE_PLUGIN_ROOT}/scripts/flotilla`.

## 0. Is the repository onboarded already?

If `.flotilla/project.toml` exists, do not start the questionnaire. Run `flotilla onboard check` and report what it
prints (exit 3 means some things could not be verified, not that they match). Then ask the person whether to keep
the profile or re-onboard; only on a clear request to replace it continue below, and use `write --force` in step 4.

## 1. Measure the machine

Run `flotilla onboard machine`. If it prints `fail  python3`, stop and tell the person exactly what it printed:
hooks run through the `python3` on PATH, and nothing else works until that is 3.11 or newer.

## 2. Show what was found

Run `flotilla onboard detect`. Summarize it in a few lines: trunk, test commands and where each came from, CI and
whether its jobs came from a real push run or only from workflow files ("unverified"), and any notes. Do not
decide anything from it — the person confirms it in the questions.

## 3. Ask, one round at a time

Loop:

1. Run `flotilla onboard next`. If `done` is true, go to step 4.
2. Ask the returned questions with AskUserQuestion, in one call: use each question's `question`, `header`,
   `options` (label and description) and `multi_select` exactly as given. Do not add, drop or reorder options.
3. Record each answer with `flotilla onboard answer <id> <value>`: use the option's `value`, not its label. For a
   multi-select question pass every chosen value. When the person typed their own text (Other) and the question
   has `free_text`, pass the text itself as the value.
4. If `answer` refuses a value, show the person the refusal and ask that question again.

Answers are stored between sessions; `flotilla onboard reset` forgets them.

## 4. Write the profile

Run `flotilla onboard write`. It runs each chosen test tier once, then writes the file.

- If a tier is not green, it prints the last lines and writes nothing. Show those lines and ask the person whether
  the command is wrong (then record a corrected `tiers` answer and run `write` again) or the project is red right
  now (then run `write --keep-unmeasured`).
- If the profile already exists, it refuses. Suggest `flotilla onboard check` first; re-onboard with `--force`
  only when the person asks to replace their file.

## 5. Confirm

Run `flotilla onboard check` and report its findings, if any. Tell the person that the file is theirs to edit and
commit, and that guards and git hooks they chose are recorded but installed later by flotilla's guard setup.
