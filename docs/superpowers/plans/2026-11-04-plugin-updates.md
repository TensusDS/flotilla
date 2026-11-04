# Plugin updates (0.29.0) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** a `stable` release channel, a refused or stale pin explained at session start and in doctor, and
`flotilla update` / `/flotilla:update` that moves the machine to the channel and brings every install up, with a
working fleet stood down or its update deferred.

**Architecture:** reading in `flotilla/core/releases.py` (newest release, cached) and `flotilla/core/claude_state.py`
(user entry, findings); acting in `flotilla/update.py` (report, plan, mark, apply); a release-split check in
`flotilla/fleet/spawn.py` used by spawn and helper raise.

**Tech Stack:** Python 3.11 stdlib, pytest; `git ls-remote`, `claude plugin` commands run through an injected `run`.

**Spec:** docs/specs/2026-11-04-plugin-updates-design.md

## Global Constraints

- English only in the repository (tools/check_no_cyrillic.py).
- Never write `known_marketplaces.json` or `installed_plugins.json`; never `marketplace remove`; never uninstall.
- Only `TensusDS/flotilla` on GitHub is rewritten; any other source is reported only.
- `apply` refuses with `caller.person_refusal(...)`; every external call goes through an injected `run` with a
  timeout; tests never call the real `claude` or the network.
- Version 0.29.0 in plugin.json, pyproject.toml, flotilla/__init__.py, README's pinned example.

## Review Focus

1. User settings that do not parse, or hold `extraKnownMarketplaces` of an unexpected shape: report, never write.
2. Offline or slow GitHub: session start says nothing and stays within its budget; doctor says it could not ask.
3. A step of apply failing midway (marketplace add refused): stop before any `plugin update`, name the backup.
4. installed_plugins.json listing directories that no longer exist, or worktrees: left out of the report.
5. A fleet whose seats run on the user copy while a project install exists: the release-split check must compare
   with what a new seat would load, and say nothing when either side is unknown.

---

### Task 1: newest release, cached

**Files:** Create `flotilla/core/releases.py`; Test `tests/test_releases.py`.

**Interfaces:** Produces `Releases(newest: str | None, stable: str | None, asked_at: str, error: str)`,
`ask(run, *, timeout=5.0) -> Releases`, `cached(state: Path, run, *, now, max_age=timedelta(days=1), fresh=False)
-> Releases`.

- [ ] Tests: `ask` parses `ls-remote --tags --refs` + `refs/heads/stable` output (newest by version, not by text;
  ignores non-`vX.Y.Z`); `stable` is the commit the branch names; a non-zero exit or timeout gives `newest=None`
  with `error`; `cached` reuses a file younger than a day, re-asks when older or `fresh=True`, survives a
  corrupt file. RED, then implement (git env: `GIT_TERMINAL_PROMPT=0`, no repository: run with `cwd` = state dir),
  GREEN, commit.

### Task 2: findings in doctor and at session start

**Files:** Modify `flotilla/core/claude_state.py`, `flotilla/doctor.py`, `flotilla/hooks.py`; Test
`tests/test_updates_findings.py`, extend `tests/test_hooks*.py` as needed.

**Interfaces:** Produces `user_entry(env, home) -> dict | None` (the `flotilla` entry of `<config>/settings.json`),
`update_findings(entry: dict | None, project_ref: str, releases: Releases, running: str) -> list[tuple[str, str,
str]]`.

- [ ] Tests, one per spec table row: user pin older than newest -> warn naming both versions, the refusal text and
  `/flotilla:update`; stable without autoUpdate -> info; default branch -> info; no user entry and project pin
  older -> info; newest unknown -> nothing. Session start prints only warn rows and only once a day
  (`updates-said` file). RED, implement, GREEN, commit.

### Task 3: one fleet, one release

**Files:** Modify `flotilla/fleet/spawn.py`, `flotilla/fleet/helpers.py`, `flotilla/fleet/commands.py`; Test
`tests/test_release_split.py`.

**Interfaces:** Produces `release_split(ledger, *, running: str, env=os.environ, home=None) -> str` ("" when the
same or unknown).

- [ ] Tests: project install record at 0.30.0 while running 0.29.0 -> message naming both and `--anyway`;
  no project record, user at another version -> message; equal -> ""; unreadable records -> "". `spawn()` raises
  `SpawnRefused` with it unless `anyway`; `raise_helper` raises `MoveRefused` unless `anyway`. RED, implement,
  GREEN, commit.

### Task 4: `flotilla update` and `apply`

**Files:** Create `flotilla/update.py`; Modify `flotilla/cli.py`, `flotilla/fleet/retire.py` (a deferral line in
`down`'s close), `flotilla/hooks.py` (deferral line at session start in that project); Test `tests/test_update.py`.

**Interfaces:** Produces `report(*, run, census, env, home, releases) -> Report` (channel, installs, pins,
fleets), `plan(report, *, pin: str | None, stop: set[str], defer: set[str]) -> Plan` with `mark`, `apply(plan,
mark, *, run, census, env, home, state) -> list[str]`, `deferred(state, project) -> str`.

- [ ] Tests: the report lists user + existing main-checkout installs only, behind/ok, foreign sources reported not
  planned; a project with live members (census `project.members`) appears under fleets and the plan requires a
  stop-or-defer choice for it; the mark changes with the plan; apply refuses a stale mark, refuses from a background
  caller, backs up settings, writes ref `stable`, runs `claude plugin marketplace add TensusDS/flotilla#stable`,
  writes `autoUpdate: true` back, reads both back, stops before any `plugin update` when the add fails (backup
  named), stands a stopped project's background seats down (interactive seats kept), records deferrals, runs
  `claude plugin update ... --scope user|project|local` per install not deferred, continues past one failure.
  RED, implement, GREEN, commit.

### Task 5: skill, docs, channel check, release

**Files:** Create `skills/update/SKILL.md`; Modify README.md, USER_MANUAL.md, CHANGELOG.md, skills/flotilla/SKILL.md
(command list), `tools/check_version.py` (`--stable`), `.github/workflows/ci.yml` (job on push to `stable`),
version files; Test `tests/test_check_version.py`, `tests/test_version.py` as they pin.

- [ ] `check_version.py --stable` fails unless HEAD is exactly tag `v<plugin version>`; test it. Skill per spec C.
  README install via `#stable`, troubleshooting with the refusal text. Full suite green, final review, merge, tag
  v0.29.0, CI green, create `stable` at the tag.
