# Security policy

flotilla sits between Claude Code sessions and your repository: its hooks judge every Bash command, can refuse one,
and - only when you opt in - let checked commands past auto mode's classifier. A defect here can matter, so please
report one privately.

## Reporting a vulnerability

Use GitHub's private reporting: **Security → Report a vulnerability** at
<https://github.com/TensusDS/flotilla/security/advisories/new>. Do not open a public issue for it.

Say what you ran, on which flotilla and Claude Code versions, and what happened. Every report is answered within a
week. A confirmed issue is fixed with a test that reproduces it, released as a new version, and credited in
[CHANGELOG.md](CHANGELOG.md) unless you ask otherwise.

## Supported versions

Only the latest release gets fixes. flotilla is pre-1.0; to update, see
[README.md, "Updating, and removing flotilla"](README.md#updating-and-removing-flotilla).

## What flotilla does and does not promise

[README.md, "Security model and its limits"](README.md#security-model-and-its-limits) says what flotilla protects
against and what it leaves to Claude Code, to your permission mode and to you.
