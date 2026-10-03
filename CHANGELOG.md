# Changelog

Every release is a tagged commit on GitHub. flotilla is pre-1.0: a minor version may change an interface. Why
each change was made is in [docs/specs/2026-09-22-decisions-log.md](docs/specs/2026-09-22-decisions-log.md).

## 0.7.1 - 2026-10-03

Closes the findings of a scan of the fleet, lane, onboarding and guard code:
- **A push is judged by origin's trunk.** The push guard, the `pre-push` hook and the person's approval took trunk's
  name from the tree's own profile and fell back to that file when the ref carried none, so an uncommitted edit
  could turn a push to the real trunk into a push "to another branch". Trunk is now origin's default branch (or the
  trunk the profile there names) and the rules are the profile trunk carries; git's history is read with replace
  refs and grafts off; `pre-push` asks the destination git names, after every URL rewrite. A push that lands on
  trunk is refused with no override when its rules cannot be read; a branch push needs nothing of origin.
- **Where the line is.** README and SECURITY.md now say plainly that the push guards check what a session pushes
  and are not a lock on the repository: protect trunk on your Git host.
- **Text another session wrote cannot forge lines.** A lane booking's note, name and `--for`, and a leftover
  process's command line in `retire`, are shown with control characters made visible.

## 0.7.0 - 2026-10-03

- **Claude Code only, said and enforced.** The command line moves from `scripts/flotilla` to `bin/flotilla`, which
  Claude Code puts on the Bash tool's PATH; claude.ai chat and Cowork do not install a plugin with `bin/`. Every
  skill's `compatibility` says Claude Code only.
- **Skipping auto mode's classifier is opt-in.** flotilla answers "allow" for its own checked commands and the
  sender's checked push only with `[permissions] skip_classifier_for_checked = true` in the profile on trunk; off
  unless set. Not recommended for people new to Claude Code or auto mode.
  Onboarding writes the key, off; the opt-in counts only as origin's trunk carries it (flotilla asks origin), and a
  session is told once when only the missing opt-in keeps a command from the allow. The same opt-in governs `ask`
  mode: without it, flotilla's own commands are put to you like any other call.
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
