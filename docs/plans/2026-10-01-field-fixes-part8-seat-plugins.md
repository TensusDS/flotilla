# Field Fixes, Part 8 (Track B): Each Post Starts Only the Plugins It Needs, and Part 7's Minors — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A seat stops paying for MCP servers its post never uses, and the smaller defects part 7's reviews deferred
are closed.

**Architecture:** `flotilla spawn` already launches every seat with the main checkout as its working directory and
its tree by `--add-dir` (`flotilla/fleet/launch.py`, `argv`). It will add `--settings '<json>'` with an
`enabledPlugins` map that turns off every user-enabled plugin that brings MCP servers and that the post does not
declare. A post declares what it keeps in a new frontmatter key `plugins:` (a list of plugin ids). Settings given by
`--settings` sit above user, project and local files, so the person's own `.claude/settings.local.json` in the main
checkout no longer leaks into seats. Track A (helper sessions) runs in parallel in another worktree and touches
`flotilla/fleet/spawn.py` and `launch.py` for a new kind of seat: keep your edits to `launch.argv` and a new module.

**Tech Stack:** Python 3.11+ stdlib, git, pytest via `uv`, the `claude` CLI.

**Spec:** `docs/field-tests/2026-09-29-twosuns-fleet.md` — H49 (MCP memory), H7 (a `<tree>-<n>` session in a seat
tree: in the twosuns run it was the `security-guidance` plugin's hook starting a headless `claude`), and part 7's
deferred minors (Task 5).

## Decisions (the planner's, 2026-10-01)

1. **Measure before building** (Task 1). The claim "`--settings` with `enabledPlugins: false` stops that plugin's MCP
   servers from starting in a `--bg` session" is tested on a real session before any code relies on it. Also measured:
   whether `--strict-mcp-config` without `--mcp-config` starts no plugin MCP servers at all. The chosen mechanism is
   whichever the measurement shows works; if neither does, stop and report.
2. **A post keeps only the plugins it declares, among those that bring MCP servers.** New post key `plugins:` (list
   of plugin ids, default empty). Plugins without MCP servers (skills, commands, hooks only) are left alone:
   they cost no resident process, and turning off a hook plugin changes behaviour the person chose. flotilla itself is
   never turned off.
3. **Which plugins bring MCP servers** is read from `claude plugin list --json` (enabled plugins, their ids and install
   paths) and each plugin's `.mcp.json` or `mcpServers` in `.claude-plugin/plugin.json`. A plugin whose files cannot be
   read is left enabled (doubt keeps behaviour).
4. **Defaults:** the judge template declares `plugins: [playwright@claude-plugins-official]`; every other template
   declares none. The project may edit its posts.
5. **`flotilla spawn --dry-run` prints, per seat, the plugins it will turn off**, so the person sees what a seat will
   not have.
6. **H7:** `flotilla fleet` and `watch` label a live session whose working directory is a seat tree but whose name is
   no post as "not a fleet session (started in <tree>)", and do not count it anywhere a seat is counted.

Decisions 153–158 in the decisions log (Track A holds 159 and up).

## Global Constraints

- English only; Linux and macOS; stdlib only; the suite three ways (`uv run --with pytest python -m pytest -p
  no:cacheprovider tests/`, with `--python 3.11`, with `GIT_CONFIG_GLOBAL=/dev/null`), both `claude plugin validate`
  commands, `tools/check_no_cyrillic.py`, and `tools/check_version.py` (it will say the plugin changed since
  v0.2.0: do not bump — the coordinator bumps at merge).
- **Memory:** Task 1 raises one real background session. Before raising it, read `MemAvailable` from `/proc/meminfo`;
  if it is under 3000 MB, wait (poll every 60 s, up to 30 minutes) and say so in your report; stop the probe session
  (`claude stop <id>`) as soon as it is measured, and remove any worktree you made.
- The probe session must start in a trusted directory: `/home/user/workspace` is trusted in `~/.claude.json`, a new
  worktree is not. Do not edit `~/.claude.json`.
- Worktree `/home/user/workspace/flotilla-part8b`, branch `fix/part8-seat-plugins` from `main`. Never touch the main
  checkout. Do not merge, push, tag or delete branches.

## Review Focus

1. **A plugin enabled only at project scope in the target repo, not at user scope** → still turned off for a post
   that does not declare it (it brings MCP servers either way) (Task 3).
2. **`claude plugin list --json` failing or timing out at spawn** → spawn goes on with no `--settings` and warns
   "plugin set not narrowed" (never refuses a spawn over it) (Task 3).
3. **A post declaring a plugin that is not installed** → a warning naming it; nothing else changes (Task 3).
4. **A plugin id with characters that need quoting in JSON** → `--settings` is one argv element produced by
   `json.dumps`, never a shell string (Task 3).
5. **The judge post edited by a project to drop `plugins:`** → the judge gets no browser MCP, and `spawn --dry-run`
   shows that (Task 4).

---

### Task 1: measure the mechanism on a real session

- [ ] Write `tools/probe_seat_plugins.py` (not part of the plugin; kept for the record): it raises one `claude --bg`
  session from `/home/user/workspace` with `--add-dir <a temp dir>` and a trivial prompt, three times in turn —
  (a) no extra flags, (b) `--settings '{"enabledPlugins": {"<id>": false, ...}}'` for every enabled plugin with MCP
  servers except flotilla, (c) `--strict-mcp-config` alone — and for each counts the session's descendant processes
  whose command line names an MCP server (walk `/proc/*/stat` ppid from the session pid in the census), then stops it.
  Print a table: variant, MCP processes, their RSS sum.
- [ ] Run it under the memory rule above. Record the table in `docs/specs/2026-09-22-decisions-log.md` under decision
  153 as the measurement behind it. **If (b) does not reduce the MCP processes, and (c) does not either, stop and
  report — do not build Tasks 2–4 on a mechanism that was not seen working.**
- [ ] Commit `tools: a probe that measures which MCP servers a seat starts`.

### Task 2: which plugins bring MCP servers

**Files:** Create `flotilla/fleet/plugins.py`; tests `tests/test_fleet_plugins.py` (fixtures: a fake `claude plugin
list --json` output and temp plugin dirs with and without `.mcp.json` / `mcpServers`).

- [ ] Tests first: `mcp_plugins(run, cwd)` returns the enabled plugin ids that bring MCP servers; a plugin whose
  install path is missing is omitted (doubt keeps it enabled); `claude` failing → `None`.
- [ ] Implement; suite; commit `feat(fleet): tell which enabled plugins bring MCP servers`.

### Task 3: spawn narrows each seat's plugin set

**Files:** `flotilla/posts.py` (the `plugins` key on `Post`), `flotilla/fleet/launch.py` (`argv` gains
`settings_json`), `flotilla/fleet/spawn.py` (compute once per spawn, pass per seat), `templates/posts/judge.md`
(`plugins:` and `template_version` + 1); tests `tests/test_posts.py`, `tests/test_fleet_launch.py`,
`tests/test_fleet_spawn.py`.

- [ ] Tests first for Review Focus 1–4 and: a reviewer seat's argv carries `--settings` with every MCP plugin false
  except none; a judge seat keeps playwright; flotilla is never in the map; the map is valid JSON in one argv element.
- [ ] Implement (the mechanism Task 1 chose); suite; commit `feat(fleet): each post starts only the MCP plugins it declares`.

### Task 4: the dry run shows it; H7 labelled

**Files:** `flotilla/fleet/commands.py` (dry-run lines), `flotilla/fleet/retire.py` (`fleet_view`),
`flotilla/watch/fleet.py` — **only** the place that counts sessions (coordinate: Track A adds a helper seat kind;
touch no other function there); tests alongside.

- [ ] Tests first: dry run prints "turns off: <ids>" per seat; a live session named `<tree-dir>-<n>` with cwd in a
  seat tree and no post is listed as "not a fleet session" and not counted as a seat.
- [ ] Implement; suite; commit `feat(fleet): the dry run names the plugins a seat will not start; strangers in seat trees are named`.

### Task 5: part 7's minors

- [ ] Each with a test that fails first: the lane guard does not warn on text inside `git commit -m` (quoted or
  multi-line), nor on `pytest --version` / `npx playwright install`; a non-numeric `lane.memory_floor_mb` or
  `fleet.memory_floor_mb` falls back to the default and says so, never crashes (also when memory is unknown);
  `check_version` without git installed prints a message, not a traceback; `spawn`'s floor check counts the seats it
  is about to raise (free minus n × `fleet.seat_cost_mb`, default 800) rather than free memory once.
- [ ] Commit `fix: part 7's review minors`.

### Task 6: decisions 153–158 and a whole-branch review

- [ ] Log entries; one fresh read-only review on the most capable model with this plan's Review Focus; fix its
  Critical and Important findings test-first in one pass.

## Finish

Report to the coordinator: branch, tip, commits, the three `N passed` lines, the Task 1 table, the review's findings
and what was done with each, every `Ruling:`.
