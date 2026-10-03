# Field Fixes, Part 4: Setting Up a Fleet — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the first fleet in a new project come up without hand edits: an install path, checks that catch a
missing plugin or an untrusted directory before `spawn`, onboarding that asks about the judge and the model for
every project, tier names a person can read, a spawn that explains its numbers, and a way to see that the hooks
fire.

**Architecture:** Seven small, independent changes. The flotilla repository publishes its own marketplace
manifest, and the README says how to install. A new `flotilla/core/claude_state.py` reads two facts Claude Code
keeps: whether the plugin is enabled in a directory (`claude plugin list --json`, run there) and whether a
directory is trusted (`~/.claude.json`, `projects[<path>].hasTrustDialogAccepted`). `doctor` reports both, and
`spawn` refuses on either. Onboarding asks the judge question of every project, offers "trunk on origin" as the
deployed build, and takes a model name. Tier names come from the command. `spawn --dry-run` says where its
numbering continues from. Every hook records when it last ran, and `flotilla guard status` shows it per session.

**Tech Stack:** Python 3.11+ stdlib, git, pytest via `uv`.

**Spec:** the field test `docs/field-tests/2026-09-27-snake-fleet.md` — findings F1–F7 and F11 and the triage with
The person of 2026-09-28, group 7 ("Setup"); `docs/specs/2026-09-22-flotilla-design.md` sections 5 (onboarding), 7.2
(spawn) and 8 (hooks).

## Decisions (the person's triage, 2026-09-28, and the executor's)

1. **The flotilla repository is its own marketplace** (F1): `.claude-plugin/marketplace.json` lists one plugin,
   `flotilla`, sourced from the repository root, so `claude plugin marketplace add TensusDS/flotilla` then
   `claude plugin install flotilla@flotilla --scope project` installs it. The README carries those two commands.
2. **`doctor` and `spawn` check the two things a background session needs** (F2, F3): the plugin enabled in the
   main checkout, and the main checkout trusted. `doctor` reports them (not inside a hook: a hook firing proves the
   plugin runs, and a hook's budget is seconds); `spawn` refuses when either is known to be missing, naming the one
   command that fixes it, and warns when it could not tell. `spawn --dry-run` prints the same as warnings.
3. **Every project is asked about the judge** (F4). A surface chosen means a judge in the fleet with
   `judge.required = true`; a project with no deployment found gets "trunk on origin" as its deployed build:
   `git ls-remote origin refs/heads/<trunk>`, whose first word is the revision `walked` compares with.
4. **The model question takes a model name** (F5): `fleet.model` may be `one`, `reviewer-strongest`, or a model
   name (`sonnet`, `opus`, `haiku`, or one typed as Other), which every post without its own `model` runs on.
5. **A typed tier is named after its command** (F6): the first word that names a known test runner (`pytest`,
   `vitest`, `jest`, `playwright`, `cargo`, `go`, `npm`, `pnpm`, `yarn`, `make`, `tox`, `nox`, `ruff`, `mypy`), or
   else the command's first word; a clash gets `-2`, `-3`.
6. **`spawn --dry-run` says where each post's numbering continues from** (F7): the highest number taken, and
   whose — a session alive on this machine (names are machine-wide addresses) or a name spawn issued before.
7. **Every hook records when it last ran for the session** (F11), in the state directory, and
   `flotilla guard status` lists, for each live session of this project, when each hook last fired. The Bash
   guard records only when a command reached the guards (its fast path stays free of writes).

## Global Constraints

- flotilla is English only (`tools/check_no_cyrillic.py`); Linux and macOS; Python 3.11+ standard library only.
- A hook never crashes or traps a session: a failure to record is ignored, never raised.
- flotilla never writes the user's settings: it reads `~/.claude.json` and `claude plugin list`, and names the
  command a person runs.
- Every `flotilla <cmd>` a template or skill names must exist.
- Tests run three ways before a task is done: default, `--python 3.11`, `GIT_CONFIG_GLOBAL=/dev/null`; plus
  `python3 tools/check_no_cyrillic.py` and `claude plugin validate .`.
- The suite command: `uv run --with pytest python -m pytest -p no:cacheprovider tests/` — without `-q`. Read the
  `N passed` line. A test that could wait on a real clock passes fakes for every clock the code reads.

## Review Focus

1. **`claude` answers `plugin list` with something that is not the expected JSON, or not at all** → "could not
   tell", a warning, never a refusal and never a crash (Task 2, `test_an_unreadable_plugin_list_is_unknown`).
2. **`~/.claude.json` missing, unreadable, or with no entry for the path** → unknown, not "untrusted"
   (Task 2, `test_trust_is_unknown_without_the_file_or_the_entry`).
3. **A profile written before this change** (`fleet.model = "one"`, no `judge`) → spawns exactly as before
   (Task 4, `test_one_for_all_still_inherits`).
4. **Two typed tiers whose commands start with the same runner** → two distinct names (Task 5,
   `test_two_pytest_tiers_get_distinct_names`).
5. **A state directory that cannot be written** → the hook still answers, and `guard status` says "never" rather
   than failing (Task 7, `test_a_hook_that_cannot_record_still_answers`).

---

### Task 1: The repository is its own marketplace (F1)

**Files:**
- Create: `.claude-plugin/marketplace.json`
- Modify: `README.md` (a new section "Install", after "Requirements")
- Test: `tests/test_manifest.py`

- [ ] **Step 1: Write the failing test** (append to `tests/test_manifest.py`; read its top for `ROOT`)

```python
def test_the_repository_is_its_own_marketplace():
    import json
    market = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
    plugin = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert market["name"] == "flotilla" and market["owner"]["name"]
    assert [(p["name"], p["source"]) for p in market["plugins"]] == [(plugin["name"], "./")]


def test_the_readme_says_how_to_install():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "claude plugin marketplace add TensusDS/flotilla" in text
    assert "claude plugin install flotilla@flotilla --scope project" in text
```

- [ ] **Step 2: Run it to see it fail** — `FileNotFoundError` for `marketplace.json`.

- [ ] **Step 3: Write `.claude-plugin/marketplace.json`**

```json
{
  "name": "flotilla",
  "owner": {"name": "TensusDS"},
  "description": "flotilla: coordinate independent peer Claude Code sessions.",
  "plugins": [
    {
      "name": "flotilla",
      "source": "./",
      "description": "Named posts, per-session worktrees, an evidence-backed work ledger, a machine lane and command guards."
    }
  ]
}
```

- [ ] **Step 4: README** — after "## Requirements":

```markdown
## Install

flotilla is its own marketplace. In the project a fleet will work on:

    claude plugin marketplace add TensusDS/flotilla
    claude plugin install flotilla@flotilla --scope project

`--scope project` records it in the project's `.claude/settings.json`, so every session started in the main
checkout — the ones `flotilla spawn` raises included — loads flotilla. Then run `flotilla doctor` there.
```

- [ ] **Step 5: Validate and run** — `claude plugin validate .` → passed; the two tests pass; full suite.

- [ ] **Step 6: Live check, cleaned up after.** In a scratch repository under the session's scratchpad (a
  `git init` with one commit), run `claude plugin marketplace add /home/user/workspace/flotilla --scope project`,
  then `claude plugin install flotilla@flotilla --scope project`, then `claude plugin list --json` there: the entry
  `flotilla@flotilla` is `enabled: true`. Clean up: `claude plugin uninstall flotilla@flotilla --scope project`,
  `claude plugin marketplace remove flotilla`, delete the scratch repository. Record what was run and seen in the
  ledger. If the local add is refused, record the refusal verbatim and stop the task for the person: the manifest then
  needs another shape.

- [ ] **Step 7: Commit**

```bash
git add .claude-plugin/marketplace.json README.md tests/test_manifest.py
git commit -m "feat(plugin): the repository is its own marketplace, and the README says how to install

A fleet in a new project had no install path: spawn's sessions would have had no skills or hooks (F1)."
```

---

### Task 2: `doctor` and `spawn` check the plugin and the trust (F2, F3)

**Files:**
- Create: `flotilla/core/claude_state.py`
- Modify: `flotilla/doctor.py` (`collect` gains `setup: bool = True`), `flotilla/hooks.py` (`_session_start`
  passes `setup=False`), `flotilla/fleet/spawn.py` (`plan`), `flotilla/fleet/commands.py` (`_spawn` dry run)
- Test: create `tests/test_claude_state.py`; modify `tests/test_doctor.py`, `tests/test_fleet_spawn.py`

**Interfaces:**
- Produces:
  - `claude_state.plugin_enabled(cwd: Path, *, run=subprocess.run, timeout: float = 30) -> bool | None` — `True`
    when an entry whose id starts with `flotilla@` is `enabled` there, `False` when none is, `None` when it could
    not tell;
  - `claude_state.trusted(path: Path, *, home: Path | None = None) -> bool | None` — `hasTrustDialogAccepted` of
    `projects[str(path.resolve())]` in `<home>/.claude.json`; `None` when the file, the entry or the key is missing;
  - `claude_state.setup_problems(main: Path, *, run, home=None) -> tuple[list[str], list[str]]` — (refusals,
    warnings), each naming the fix.

- [ ] **Step 1: Write the failing tests** (`tests/test_claude_state.py`)

```python
import json
import subprocess
from pathlib import Path

from flotilla.core import claude_state


def answering(stdout, code=0):
    def run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, code, stdout, "")
    return run


def listing(*entries):
    return json.dumps([{"id": pid, "enabled": on, "scope": "project"} for pid, on in entries])


def test_the_plugin_is_enabled_here():
    run = answering(listing(("other@x", True), ("flotilla@flotilla", True)))
    assert claude_state.plugin_enabled(Path("/p"), run=run) is True


def test_the_plugin_is_installed_but_not_enabled_here():
    assert claude_state.plugin_enabled(Path("/p"), run=answering(listing(("flotilla@local", False)))) is False
    assert claude_state.plugin_enabled(Path("/p"), run=answering(listing(("other@x", True)))) is False


def test_an_unreadable_plugin_list_is_unknown():
    assert claude_state.plugin_enabled(Path("/p"), run=answering("not json")) is None
    assert claude_state.plugin_enabled(Path("/p"), run=answering("", code=1)) is None
    def missing(cmd, **kwargs):
        raise FileNotFoundError("claude")
    assert claude_state.plugin_enabled(Path("/p"), run=missing) is None


def test_trust_is_read_from_claude_json(tmp_path):
    (tmp_path / ".claude.json").write_text(json.dumps({"projects": {
        str((tmp_path / "repo").resolve()): {"hasTrustDialogAccepted": True},
        str((tmp_path / "other").resolve()): {"hasTrustDialogAccepted": False}}}), encoding="utf-8")
    assert claude_state.trusted(tmp_path / "repo", home=tmp_path) is True
    assert claude_state.trusted(tmp_path / "other", home=tmp_path) is False


def test_trust_is_unknown_without_the_file_or_the_entry(tmp_path):
    assert claude_state.trusted(tmp_path / "repo", home=tmp_path) is None
    (tmp_path / ".claude.json").write_text("{broken", encoding="utf-8")
    assert claude_state.trusted(tmp_path / "repo", home=tmp_path) is None
    (tmp_path / ".claude.json").write_text(json.dumps({"projects": {}}), encoding="utf-8")
    assert claude_state.trusted(tmp_path / "repo", home=tmp_path) is None


def test_setup_problems_refuse_what_is_known_missing_and_warn_on_unknown(tmp_path):
    refusals, warnings = claude_state.setup_problems(tmp_path, run=answering(listing(("flotilla@x", False))),
                                                     home=tmp_path)
    assert any("claude plugin install flotilla@flotilla --scope project" in line for line in refusals)
    assert any("could not tell whether" in line and "trusted" in line for line in warnings)
```

Append to `tests/test_fleet_spawn.py` (read its top for the fake and world helpers; the names below stand for them):

```python
def test_spawn_refuses_when_the_plugin_is_not_enabled_in_the_main_checkout(tmp_path, monkeypatch):
    from flotilla.core import claude_state
    monkeypatch.setattr(claude_state, "setup_problems",
                        lambda main, **kw: (["flotilla is not enabled in /x: run ..."], []))
    ledger, fake, store = spawn_world(tmp_path)
    with pytest.raises(spawn.SpawnRefused, match="not enabled"):
        spawn.plan(ledger, {"main": 1}, census=fake.census, store=store, reserve=False)
```

Append to `tests/test_doctor.py`:

```python
def test_doctor_reports_the_plugin_and_the_trust_outside_a_hook(tmp_path, monkeypatch):
    from flotilla.core import claude_state
    monkeypatch.setattr(claude_state, "setup_problems", lambda main, **kw: (["not enabled here"], ["trust unknown"]))
    # onboard tmp_path as the other doctor tests do, then:
    found = doctor.collect(cwd=tmp_path, run=..., which=..., read=lambda: [])
    assert any(f.status == "fail" and "not enabled here" in f.text for f in found)
    assert not any("not enabled here" in f.text for f in doctor.collect(cwd=tmp_path, run=..., which=...,
                                                                        read=lambda: [], setup=False))
```

(Fill `run=` and `which=` with the fakes the neighbouring doctor tests use; a `Finding`'s fields are those of
`flotilla/doctor.py` — read it and use its names.)

- [ ] **Step 2: Run them to see them fail** — no module `flotilla.core.claude_state`; `collect` has no `setup`.

- [ ] **Step 3: Implement `flotilla/core/claude_state.py`**

```python
"""Two facts Claude Code keeps that a background session depends on (field test F2, F3).

Whether the plugin is enabled in a directory is asked of `claude plugin list --json` run there: it reports
`enabled` for the directory it runs in (measured 2026-09-29). Whether a directory is trusted is read from
`~/.claude.json`, `projects[<path>].hasTrustDialogAccepted`: `claude --bg` refuses an untrusted directory, and
trust is not inherited from a parent (decisions log, entry 70). Nothing here writes Claude Code's files.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

INSTALL = "claude plugin install flotilla@flotilla --scope project"


def plugin_enabled(cwd: Path, *, run=subprocess.run, timeout: float = 30) -> bool | None:
    try:
        done = run(["claude", "plugin", "list", "--json"], cwd=str(cwd), capture_output=True, text=True,
                   check=False, timeout=timeout)
        entries = json.loads(done.stdout or "") if done.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None
    if not isinstance(entries, list):
        return None
    return any(isinstance(e, dict) and str(e.get("id", "")).startswith("flotilla@") and e.get("enabled") is True
               for e in entries)


def trusted(path: Path, *, home: Path | None = None) -> bool | None:
    try:
        data = json.loads(((home or Path.home()) / ".claude.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    entry = (data.get("projects") or {}).get(str(Path(path).resolve())) if isinstance(data, dict) else None
    value = entry.get("hasTrustDialogAccepted") if isinstance(entry, dict) else None
    return value if isinstance(value, bool) else None


def setup_problems(main: Path, *, run=subprocess.run, home: Path | None = None) -> tuple[list[str], list[str]]:
    refusals, warnings = [], []
    enabled = plugin_enabled(main, run=run)
    if enabled is False:
        refusals.append(f"flotilla is not enabled in {main}, so the sessions spawn raises there would have no "
                        f"flotilla skills or hooks: run `{INSTALL}` there")
    elif enabled is None:
        warnings.append(f"could not tell whether flotilla is enabled in {main} (`claude plugin list` did not "
                        "answer)")
    trust = trusted(main, home=home)
    if trust is False:
        refusals.append(f"{main} is not trusted, and `claude --bg` refuses an untrusted directory: run `claude` "
                        "there once and accept the trust dialog")
    elif trust is None:
        warnings.append(f"could not tell whether {main} is trusted (no entry in ~/.claude.json): if spawn fails, "
                        "run `claude` there once and accept the trust dialog")
    return refusals, warnings
```

- [ ] **Step 4: Wire it**

`doctor.collect(..., setup: bool = True)`: when the project is onboarded and `setup` is true, call
`claude_state.setup_problems(project_root, run=run)` and append a `fail` finding per refusal and a `warn` finding
per warning, under the check name `setup`. `hooks._session_start` calls `doctor.collect(..., setup=False)`.

`spawn.plan`, before issuing names:

```python
    from flotilla.core import claude_state
    refusals, setup_warnings = claude_state.setup_problems(main, run=ledger.run)
    if refusals:
        raise SpawnRefused("; ".join(refusals) + "; nothing was raised")
```

and return `setup_warnings` with the composition warnings (`compose.warnings(...) + setup_warnings`).
`_spawn --dry-run` prints refusals as warnings instead: wrap the call in `plan` with a `strict: bool = True`
parameter; the dry run passes `strict=False`, and `plan` then adds the refusals to the returned warnings.

- [ ] **Step 5: Run the tests and the full suite** → `N passed`. Existing spawn tests run `plan` against a fake
  `run`: if they now fail on `setup_problems`, have their world patch `claude_state.setup_problems` to
  `lambda main, **kw: ([], [])` (in the fleet test kit, once), and record that as a ruling.

- [ ] **Step 6: Commit**

```bash
git add flotilla/core/claude_state.py flotilla/doctor.py flotilla/hooks.py flotilla/fleet/spawn.py flotilla/fleet/commands.py tests
git commit -m "feat(fleet): doctor and spawn check the plugin is enabled and the checkout trusted

doctor was green and spawn planned seven sessions with the plugin not installed (F2), and claude --bg refused an
untrusted directory (F3). Both are read from Claude Code's own answers; spawn refuses on a known gap, naming the
one command that closes it."
```

---

### Task 3: Every project is asked about the judge (F4)

**Files:**
- Modify: `flotilla/onboard/questions.py` (the `deploy` question), `flotilla/onboard/profile.py` (the `deploy`
  and `judge` sections)
- Test: `tests/test_onboard_questions.py`, `tests/test_onboard_profile.py`

- [ ] **Step 1: Write the failing tests** (read each file's top for its detection fixture; the names below stand
  for them)

```python
# tests/test_onboard_questions.py
def test_the_judge_question_is_asked_without_a_deployment():
    ids = [q["id"] for q in questions.all_questions(detection_without_deployment(), {})]
    assert "deploy" in ids


# tests/test_onboard_profile.py
def test_a_console_project_gets_a_judge_and_trunk_on_origin_as_its_build():
    data = profile.build(detection_without_deployment(), {**BASE_ANSWERS, "deploy": "cli"})
    assert data["judge"] == {"required": True}
    assert data["deploy"] == {"surface": "cli", "revision_command": "git ls-remote origin refs/heads/main"}


def test_a_found_deployment_leaves_the_revision_command_to_the_person():
    data = profile.build(detection_with_deployment(), {**BASE_ANSWERS, "deploy": "web"})
    assert data["deploy"]["revision_command"] == "" and data["judge"] == {"required": True}


def test_no_judge_writes_neither_section():
    data = profile.build(detection_without_deployment(), {**BASE_ANSWERS, "deploy": "none"})
    assert "judge" not in data and "deploy" not in data
```

Use the real function names of `profile.py` (the builder of `data`) and the fixture helpers the files already
have; write `detection_without_deployment()` / `detection_with_deployment()` in the test file if none exist, as
the detection dicts the neighbouring tests build, with `signals.deployment` false or true.

- [ ] **Step 2: Run them to see them fail.**

- [ ] **Step 3: Implement.** The `deploy` question is asked always; its wording depends on what was found:

```python
    found = bool(signals.get("deployment"))
    out.append(_q("deploy", "Judge", ("A deployment was found. " if found else "")
                  + "What surface does a person use? An acceptance judge walks it once work ships.", [
        ("web", "Web", "The judge walks the human path in a browser."),
        ("cli", "Command line", "The judge walks the human path in a terminal."),
        ("api", "API", "The judge walks the human path with HTTP calls."),
        ("none", "No judge", "No acceptance judge in the fleet."),
    ]))
```

In the profile builder:

```python
    if answers.get("deploy") in ("web", "cli", "api"):
        trunk = data["trunk"]["branch"]
        found = bool((det.get("signals") or {}).get("deployment"))
        data["deploy"] = {"surface": answers["deploy"],
                          "revision_command": "" if found else f"git ls-remote origin refs/heads/{trunk}"}
        data["judge"] = {"required": True}
```

(read how the builder reaches `signals` and the trunk name, and use those.)

- [ ] **Step 4: Run the tests and the full suite** → `N passed`. A test that expected `judge.required = false`
  is updated to the new rule and named in a ruling.

- [ ] **Step 5: Commit**

```bash
git add flotilla/onboard/questions.py flotilla/onboard/profile.py tests
git commit -m "feat(onboard): every project is asked about the judge; a console project's build is trunk on origin

The judge question appeared only with a deployment, so the snake CLI had no judge until written by hand (F4)."
```

---

### Task 4: The model question takes a model name (F5)

**Files:**
- Modify: `flotilla/onboard/questions.py` (the `model` question), `flotilla/fleet/launch.py` (`model_for`)
- Test: `tests/test_fleet_launch.py`, `tests/test_onboard_questions.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_fleet_launch.py (read its top for a post fixture; `post(model=...)` below stands for it)
def test_a_named_fleet_model_runs_every_post_without_its_own():
    assert launch.model_for({"fleet": {"model": "sonnet"}}, post(model="inherit")) == "sonnet"
    assert launch.model_for({"fleet": {"model": "sonnet"}}, post(model="opus")) == "opus"


def test_one_for_all_still_inherits():
    assert launch.model_for({"fleet": {"model": "one"}}, post(model="inherit")) == ""
    assert launch.model_for({}, post(model="inherit")) == ""


# tests/test_onboard_questions.py
def test_the_model_question_offers_model_names_and_takes_one_typed():
    q = next(q for q in questions.all_questions(detection_without_deployment(), {}) if q["id"] == "model")
    assert {"sonnet", "opus"} <= {option["value"] for option in q["options"]} and q["free_text"]
```

(Use the option key name `_q` actually writes — read `_q` first.)

- [ ] **Step 2: Run them to see them fail.**

- [ ] **Step 3: Implement.**

```python
    out.append(_q("model", "Models", "Which model runs the fleet? A post's own `model` still wins.", [
        ("one", "One for all", "Every session uses the model you launch Claude Code with."),
        ("reviewer-strongest", "Strongest reviewer", "Reviewers run on the most capable model; others on yours."),
        ("sonnet", "Sonnet", "Every post runs on Sonnet."),
        ("opus", "Opus", "Every post runs on Opus."),
    ], free_text=True))
```

```python
FLEET_MODEL_WORDS = ("one", "reviewer-strongest", "")


def model_for(profile: dict, post) -> str:
    fleet_model = str((profile.get("fleet") or {}).get("model") or "")
    if fleet_model == "reviewer-strongest" and "accept" in post.may:
        return STRONGEST
    if post.model != "inherit":
        return post.model
    return "" if fleet_model in FLEET_MODEL_WORDS else fleet_model
```

- [ ] **Step 4: Run the tests and the full suite** → `N passed`.

- [ ] **Step 5: Commit**

```bash
git add flotilla/onboard/questions.py flotilla/fleet/launch.py tests
git commit -m "feat(onboard): the fleet's model is one answer, not six post files

Running the snake fleet on Sonnet meant editing model: in every post (F5); fleet.model now names a model every
post without its own runs on."
```

---

### Task 5: A typed tier is named after its command (F6)

**Files:**
- Modify: `flotilla/onboard/profile.py` (`_tiers`)
- Test: `tests/test_onboard_profile.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_a_typed_tier_is_named_after_its_runner():
    tiers = profile._tiers({"tests": []}, ["uv run --with pytest python -m pytest -q"])
    assert [t["name"] for t in tiers] == ["pytest"]


def test_two_pytest_tiers_get_distinct_names():
    tiers = profile._tiers({"tests": []}, ["python -m pytest tests/unit", "python -m pytest tests/e2e"])
    assert [t["name"] for t in tiers] == ["pytest", "pytest-2"]


def test_a_command_with_no_known_runner_is_named_by_its_first_word():
    assert profile._tiers({"tests": []}, ["./check.sh --all"])[0]["name"] == "check.sh"


def test_a_typed_tier_does_not_take_a_detected_tiers_name():
    det = {"tests": [{"name": "pytest", "command": "pytest"}]}
    assert [t["name"] for t in profile._tiers(det, ["pytest", "python -m pytest -x"])] == ["pytest", "pytest-2"]
```

- [ ] **Step 2: Run them to see them fail** — the names are `custom-1`, `custom-2`.

- [ ] **Step 3: Implement**

```python
RUNNERS = ("pytest", "vitest", "jest", "playwright", "cargo", "go", "npm", "pnpm", "yarn", "make", "tox", "nox",
           "ruff", "mypy")


def _tier_name(command: str, taken: set[str]) -> str:
    """A name a person reads in every receipt: the runner the command calls, else its first word (F6)."""
    words = [word.rsplit("/", 1)[-1] for word in command.split()]
    base = next((word for word in words if word in RUNNERS), words[0] if words else "tier") or "tier"
    name, number = base, 1
    while name in taken:
        number += 1
        name = f"{base}-{number}"
    return name
```

and in `_tiers`, keep a `taken` set of names already used (detected tiers added to it as they are taken), and name
a typed command `_tier_name(value, taken)` in place of `custom-{n}`.

- [ ] **Step 4: Run the tests and the full suite** → `N passed`. A test expecting `custom-1` is updated.

- [ ] **Step 5: Commit**

```bash
git add flotilla/onboard/profile.py tests/test_onboard_profile.py
git commit -m "feat(onboard): a typed tier is named after its command

Every receipt and refusal printed custom-1 for the snake's pytest tier (F6)."
```

---

### Task 6: `spawn --dry-run` says where the numbering continues from (F7)

**Files:**
- Modify: `flotilla/fleet/names.py` (`numbered_after`), `flotilla/fleet/commands.py` (`_spawn` dry run)
- Test: `tests/test_fleet_names.py`

**Interfaces:**
- Produces: `names.numbered_after(post, *, taken: set[str], live: set[str], store) -> str` — "" when numbering
  starts at 1, else one sentence naming the highest number and whose it is.

- [ ] **Step 1: Write the failing tests** (read the file's top for its post and store fixtures)

```python
def test_numbering_after_a_live_session_elsewhere_says_so(tmp_path):
    said = names.numbered_after(minor_post(), taken={"minor session 37"}, live={"minor session 37"},
                                store=empty_store(tmp_path))
    assert "minor session 37" in said and "alive on this machine" in said and "machine-wide" in said


def test_numbering_after_a_name_spawn_issued_says_so(tmp_path):
    store = empty_store(tmp_path)
    names.next_names(minor_post(), 1, taken=set(), store=store, reserve=True)
    assert "issued by an earlier spawn" in names.numbered_after(minor_post(), taken=set(), live=set(), store=store)


def test_a_fresh_post_says_nothing(tmp_path):
    assert names.numbered_after(minor_post(), taken=set(), live=set(), store=empty_store(tmp_path)) == ""
```

- [ ] **Step 2: Run them to see them fail.**

- [ ] **Step 3: Implement**

```python
def numbered_after(post, *, taken: set[str], live: set[str], store) -> str:
    """Why a post's next number is not 1 (field test F7): names are machine-wide addresses."""
    issued = [record.get("n", 0) for record in store.read(KEY).records if record.get("post") == post.name]
    known = {name: number_of(post, name) or 0 for name in taken}
    top_known = max(known.values(), default=0)
    top_issued = max(issued, default=0)
    if top_known == 0 and top_issued == 0:
        return ""
    if top_known >= top_issued:
        name = max(known, key=known.get)
        where = ("alive on this machine; names are machine-wide addresses" if name in live
                 else "named in this ledger")
        return f"{post.name} numbering continues after {name} ({where})"
    return (f"{post.name} numbering continues after number {top_issued}, issued by an earlier spawn "
            "(an issued number is never reused)")
```

(Read `names.py`: if the store is read through a transaction only, read it the way `next_names` does.)

`_spawn --dry-run`, after the seats are planned, for each post that appears: print
`note: <numbered_after(...)>` when it is not empty, with `taken` and `live` built as `spawn.plan` builds them.

- [ ] **Step 4: Run the tests and the full suite** → `N passed`.

- [ ] **Step 5: Commit**

```bash
git add flotilla/fleet/names.py flotilla/fleet/commands.py tests/test_fleet_names.py
git commit -m "feat(fleet): spawn --dry-run says where each post's numbering continues from

The snake's minor was number 38 because other-project sessions held the lower numbers, which read as a bug (F7)."
```

---

### Task 7: The hooks leave a trace, and `guard status` shows it (F11)

**Files:**
- Create: `flotilla/watch/fired.py`
- Modify: `flotilla/hooks.py` (record after the project is found; the guard records only on the evaluated path),
  `flotilla/guards/commands.py` (`status` prints the hooks per session)
- Test: create `tests/test_watch_fired.py`; modify `tests/test_hooks.py`

**Interfaces:**
- Produces: `fired.record(state_dir: Path, session_id: str, event: str, *, root: Path, at: str) -> None` (never
  raises); `fired.read(state_dir: Path, session_id: str) -> dict[str, str]` (event → iso time; `{}` when none).

- [ ] **Step 1: Write the failing tests** (`tests/test_watch_fired.py`)

```python
from flotilla.watch import fired


def test_a_hook_run_is_recorded_per_session_and_event(tmp_path):
    fired.record(tmp_path, "sid-1", "session-start", root=tmp_path, at="2026-09-29T10:00:00+00:00")
    fired.record(tmp_path, "sid-1", "guard", root=tmp_path, at="2026-09-29T10:05:00+00:00")
    assert fired.read(tmp_path, "sid-1") == {"session-start": "2026-09-29T10:00:00+00:00",
                                             "guard": "2026-09-29T10:05:00+00:00"}
    assert fired.read(tmp_path, "sid-2") == {}


def test_a_hook_that_cannot_record_still_answers(tmp_path):
    blocked = tmp_path / "file"
    blocked.write_text("x", encoding="utf-8")   # a file where the state directory should be
    fired.record(blocked, "sid-1", "stop", root=tmp_path, at="2026-09-29T10:00:00+00:00")   # no exception
    assert fired.read(blocked, "sid-1") == {}


def test_a_session_id_cannot_climb_out_of_the_directory(tmp_path):
    fired.record(tmp_path / "state", "../../escape", "stop", root=tmp_path, at="2026-09-29T10:00:00+00:00")
    assert not (tmp_path / "escape.json").exists()
```

Append to `tests/test_hooks.py`:

```python
def test_session_start_leaves_a_trace(tmp_path, healthy, monkeypatch):
    from flotilla.watch import fired
    monkeypatch.setenv("FLOTILLA_STATE_DIR", str(tmp_path / "state"))
    me = sess("review session 1")
    call("session-start", tmp_path, context(tmp_path, me=me))
    assert "session-start" in fired.read(tmp_path / "state", me.session_id)
```

(`context()` builds the ledger with `state_dir=tmp_path / "state"`; record into `ctx.ledger.state_dir` when there
is a ledger, else `paths.state_dir()`.)

- [ ] **Step 2: Run them to see them fail.**

- [ ] **Step 3: Implement `flotilla/watch/fired.py`**

```python
"""When each hook last ran for a session (field test F11): the guards and the Stop guard print nothing when they
let a command through, so nothing in the session shows they run. One small file per session in the state
directory; a failure to write is ignored, because a hook must never fail for bookkeeping."""

from __future__ import annotations

import json
import re
from pathlib import Path

SAFE = re.compile(r"[^A-Za-z0-9_-]")


def _path(state_dir: Path, session_id: str) -> Path:
    return Path(state_dir) / "hooks" / f"{SAFE.sub('_', session_id) or 'unknown'}.json"


def read(state_dir: Path, session_id: str) -> dict[str, str]:
    try:
        data = json.loads(_path(state_dir, session_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    events = data.get("events") if isinstance(data, dict) else None
    return {k: v for k, v in (events or {}).items() if isinstance(k, str) and isinstance(v, str)}


def record(state_dir: Path, session_id: str, event: str, *, root: Path, at: str) -> None:
    try:
        path = _path(state_dir, session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        events = read(state_dir, session_id)
        events[event] = at
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"root": str(root), "events": events}), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        return
```

`flotilla/hooks.py`: in `run_hook`, once `root` is known and before dispatch, for every event but `guard`:
`fired.record(state, session_id, event, root=root, at=<now iso>)` where `state` is `paths.state_dir()`
(`gather`'s ledger may not exist yet; the state directory is the same). For `guard`, record inside
`guard_hook`'s caller only after `find_project` succeeded (the trigger-word fast path returns before it).

`flotilla/guards/commands.py`, `status`: after the git hook lines, list the live sessions of this project
(census → `project.members`, as in part 3) with `fired.read` for each:
`session <name>: session-start 10:00, prompt 10:04, guard 10:05, stop never, ask never, permission never`;
when the census cannot be asked, say so and list nothing.

- [ ] **Step 4: Run the tests and the full suite** → `N passed`; `test_inactive_path_imports_nothing_heavy` must
  still pass: import `fired` only after the project is found.

- [ ] **Step 5: Commit**

```bash
git add flotilla/watch/fired.py flotilla/hooks.py flotilla/guards/commands.py tests
git commit -m "feat(hooks): every hook leaves a trace, and guard status shows it per session

The guards and the Stop guard print nothing when they let a command through, so a session's record could not
confirm they ran (F11)."
```

---

### Task 8: The record — decisions, spec, findings

**Files:** `docs/specs/2026-09-22-decisions-log.md`, `docs/specs/2026-09-22-flotilla-design.md`,
`docs/field-tests/2026-09-27-snake-fleet.md`

- [ ] **Step 1:** Decisions 107–113 after the last entry, one per decision of this plan, each ending with its
  source (`(the person's triage 2026-09-28)` / `(executor's decision, field fixes part 4, 2026-09-29)`).
- [ ] **Step 2:** Design spec — the onboarding section (judge for every project, trunk on origin, the fleet model,
  tier names), section 7.2 (spawn's two checks and the numbering note), section 8 (the hook trace and
  `guard status`). Read each section first and write where its subject is.
- [ ] **Step 3:** Findings file, after the Part 3 paragraph:

```markdown
Part 4 (setting up a fleet) fixes F1–F7, F11 — plan `docs/plans/2026-09-29-field-fixes-setup.md`.
```

- [ ] **Step 4:** `python3 tools/check_no_cyrillic.py`; commit
  `docs(specs): decisions 107-113 and the spec for setting up a fleet`.
