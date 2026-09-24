---
name: check
description: Report drift between this repository's flotilla profile and the repository as it is now — CI workflows, required jobs, trunk, and test tiers never run green on this machine.
disable-model-invocation: true
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/scripts/flotilla onboard check*)
---

Run `${CLAUDE_PLUGIN_ROOT}/scripts/flotilla onboard check` from the repository root.

- Exit 0 and no output: say the profile matches the repository.
- Findings: list each one as printed, then suggest the smallest fix for each — usually editing
  `.flotilla/project.toml` by hand, or re-onboarding with `/flotilla:onboard` when several things moved at once.
- Exit 3: lines starting with `unknown:` could not be verified (for example `gh` is unavailable, or trunk has no
  push run yet). Say exactly that; never report the profile as matching.
- Exit 2: the project is not onboarded or its profile cannot be read; show the message and suggest
  `/flotilla:onboard`.
