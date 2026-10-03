# The hook allow, hardened (0.6.10) - plan

The person chose (2026-10-03, 03:39): finish the PreToolUse allow past auto mode's classifier for a full release.
Three reviews of 0.6.10 found criticals each time; this plan closes the third review's findings and states the
threat line. Executed inline; one final review of the whole range; nothing is pushed without the person's yes.

## Measured on Claude Code 2.1.288 (headless probes, scratch repos)

- A PreToolUse `allow` lets through a call the auto-mode classifier refused (a force push to main).
- A PreToolUse `deny` wins over a permission allow rule; a user's `deny` rule wins over a hook's `allow`.
- A PreToolUse `allow` with `updatedInput` runs the rewritten command, not the original.
- The hook input carries `permission_mode` ("auto", "default", ...).
- `export` in one Bash call does not reach the next: a seat cannot prepare GIT_DIR / GIT_* for a later call.

## The threat line

A misled session (prompt injection, a bad idea) - not a determined program running as the person (README, Security
model). Every command a seat types still meets the classifier, except the allowed ones. So the allowed command must
be safe given anything earlier commands may have changed in the repository's own config, refs and hooks - but
flotilla trusts the person's global and system git config, and files outside the repository (state dir, plugin).

## Tasks

1. **Person guard** (`guards/person.py`): look at every flotilla occurrence in a segment and every word after every
   `work`; a word that equals or fnmatches `approve` is approve; a flotilla segment holding `{`, `$'`, `$"`, `@(`,
   `+(`, `!(`, `?(`, `*(` or a line-continuation backslash is refused as a possible approve (bash expands those).
2. **Own commands** (`broker/decide.py`, `guards/run.py`): `own_command(payload, root=None)` - a `--root` (parsed) is
   passed only when it is a checkout of the same repository as `root` (same git common dir); outside a project no
   allow. The hook allow additionally leaves out `lane` (bare `lane` may run the profile's `ci.queue_command`).
3. **Sender's push** (`guards/run.py`): allow by rewriting, never by passing the typed command:
   - the typed command stays exactly `git -C <absolute tree> push origin HEAD:<trunk>`;
   - the caller (payload `session_id`, asked of the census) is the owner of the open row whose home the tree is, and
     that owner's post may land;
   - the repository's own config (local and worktree scope, `git config --list --show-scope`) holds only keys from an
     allowlist (core format keys, remote.origin.url/fetch, branch.*.remote/merge, user.*, extensions.worktreeconfig);
     anything else - pushurl, receivepack, uploadpack, sshCommand, hooksPath, credential.helper, include, url.* - and
     the classifier decides;
   - only then `ls-remote` (no repo config left that runs code) gives origin's trunk as the accounting base;
   - the rewritten command pins everything: `git -C <tree> -c core.hooksPath=<flotilla's empty hooks dir>
     push <origin URL> <checked sha>:refs/heads/<trunk>` - no repo hook runs (reference-transaction, pre-push),
     and HEAD moving after the check pushes nothing else.
4. **Tests** for every bypass the three reviews reproduced, each guard seen red by injection.
5. **Docs**: decision 219 and README restated; field test; the remaining known limit named (rules read from the
   local remote-tracking ref - pre-existing, outside the allow).
6. **Final review** of 6d4ccd5..HEAD on the most capable model; one fix pass; full suite; version 0.6.10; tag;
   wait for the person.
