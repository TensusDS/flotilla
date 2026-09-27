---
name: lane
description: Show who holds the machine for long runs - bookings, the queue, unbooked runs computing, CI on this machine - so a person can see why a run waits.
disable-model-invocation: true
allowed-tools: Bash(${CLAUDE_PLUGIN_ROOT}/scripts/flotilla lane), Bash(${CLAUDE_PLUGIN_ROOT}/scripts/flotilla lane --root *)
---

Run `${CLAUDE_PLUGIN_ROOT}/scripts/flotilla lane` from the repository root and show every line it prints: capacity,
who holds the lane and since when, who waits in which order, and the machine's answers. A line marked `unknown`
means a question could not be asked; never report it as free. Do not take, release or sweep anything yourself.
