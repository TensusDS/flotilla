# Changelog

Every release is a tagged commit on GitHub. flotilla is pre-1.0: a minor version may change an interface. Why
each change was made is in [docs/specs/2026-09-22-decisions-log.md](docs/specs/2026-09-22-decisions-log.md).

## Unreleased

- **Claude Code only, said and enforced.** The command line moves from `scripts/flotilla` to `bin/flotilla`, which
  Claude Code puts on the Bash tool's PATH; claude.ai chat and Cowork do not install a plugin with `bin/`. Every
  skill's `compatibility` says Claude Code only.
- **Skipping auto mode's classifier is opt-in.** flotilla answers "allow" for its own checked commands and the
  sender's checked push only with `[permissions] skip_classifier_for_checked = true` in the profile on trunk; off
  unless set. Not recommended for people new to Claude Code or auto mode.
  Onboarding writes the key, off; the opt-in counts only as origin's trunk carries it (flotilla asks origin), and a
  session is told once when only the missing opt-in keeps a command from the allow.
- **A permission question keeps the call it asked about only while it waits.** The command or file content is
  dropped as soon as the asking session has its answer or stops waiting.
- **No session transcript is read.** How a session was started comes from Claude Code's session registry.
- Privacy policy ([PRIVACY.md](PRIVACY.md)), support ([SUPPORT.md](SUPPORT.md)) and security reporting
  ([SECURITY.md](SECURITY.md)); the plugin manifest links them for the directory listing.

## 0.6.11 - 2026-10-03

Closes the findings of a security scan of 0.6.10 and of the review of those fixes: the sender's push allow reads
its rules at the revision origin names and counts only moves made under them; a repository a command merely names
is asked of origin only safely; a branch name a shell would expand never reaches the person's approve command.

## 0.6.10 - 2026-10-03

Auto mode lets flotilla's own moves and the sender's checked push past the classifier.

## 0.6.0 to 0.6.9 - 2026-10-01 to 2026-10-03

- 0.6.9: a direct commit on trunk can be read and closed; orphaned work names who can take it.
- 0.6.8: a fleet nobody leads is a question, not a silence; receipts measure tiers.
- 0.6.7: seats rise only once the leading session's name shows; known limitations named.
- 0.6.4 to 0.6.6: a seat's tree and branch go when their work is surely on trunk, never a live seat's.
- 0.6.2, 0.6.3: the lane spent carefully - nothing run twice, a ceiling, a stop, set-up trees.
- 0.6.1: auto mode by default; flotilla's own commands never wait on the person.
- 0.6.0: seat names carry the project, and your own session leads without a rename.

Earlier releases: the tags before `v0.6.0`.
