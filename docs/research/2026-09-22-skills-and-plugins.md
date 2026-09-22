# Authoring Claude Code skills and plugins (skills + scripts + hooks) for public distribution

Research date: 2026-09-22. Local CLI: Claude Code 2.1.280. Docs on code.claude.com change often and carry
per-version notes (v2.1.1xx–2.1.27x), so treat every version number below as **volatile**.

**Evidence tags**
- **[D]** read directly: the raw doc markdown fetched with `curl <page>.md` (saved in this scratchpad: `skills.md`,
  `plugins.md`, `plugins-ref.md`, `hooks.md`, `hooks-guide.md`, `mkt.md`, `settings-ref.md`), or a local file.
- **[S]** read through WebFetch. A small model summarizes the page, so the wording can be paraphrased.
- **[I]** my own inference.
- **[C]** community source.

Each claim is marked **FACT** (documented behaviour) or **REC** (a recommendation or opinion, with whose it is).

---

## 1. Skill structure

### 1.1 Frontmatter
- FACT [D skills.md]: in Claude Code **every frontmatter field is optional**. Unknown fields are ignored. Invalid YAML
  gives no error: the skill loads with no fields set.
- FACT [D skills.md]: if `description` is missing, the first non-empty markdown line is used instead.
- FACT [D skills.md]: fields Claude Code acts on:
  - naming and triggering: `name`, `description`, `when_to_use`, `paths` (globs that limit auto-invocation to
    matching files);
  - who can invoke: `disable-model-invocation`, `user-invocable`;
  - tools: `allowed-tools` (pre-approves tools for that turn only; restricts nothing), `disallowed-tools`;
  - arguments: `argument-hint`, `arguments`;
  - execution: `context: fork` + `agent`, `background`, `model`, `effort`, `shell`, `hooks`;
  - accepted but ignored: `metadata`, `license`, `compatibility`.
- FACT [D agentskills.io/specification via S]: the portable **Agent Skills spec** is stricter:
  - `name` and `description` are required;
  - `name` is 1–64 chars of `[a-z0-9-]`, has no leading, trailing or double hyphen, and **must match the parent
    directory name**;
  - `description` is 1–1024 chars;
  - `compatibility` is at most 500 chars;
  - `allowed-tools` is "Experimental".
  - Validator: `skills-ref validate ./my-skill`.
- FACT [S platform.claude.com/.../agent-skills/best-practices]: the API/claude.ai surface adds two rules on top of
  that. `name` must not contain XML tags or the reserved words "anthropic" or "claude". `description` must be
  ≤1024 chars with no XML tags.
- REC (Anthropic): to be portable across Claude Code, the API and claude.ai, follow the **strictest** set. Required
  `name` equals the directory name, `description` stays ≤1024 chars, and neither contains "claude" or "anthropic".
  [I — this combines the three sources above]

### 1.2 How the description drives triggering
- FACT [D skills.md]: the listing keeps every skill name, but the descriptions share a **character budget of 1% of
  the model's context window**.
  - When the listing overflows, descriptions are dropped **starting with the least-invoked skills**.
  - Each `description` + `when_to_use` is capped at **1,536 chars** (setting `skillListingMaxDescChars`).
  - Settings that change the budget: `skillListingBudgetFraction`, env `SLASH_COMMAND_TOOL_CHAR_BUDGET`, and
    `skillOverrides: "name-only"`.
  - REC (docs): "put the key use case first".
- FACT [D skills.md]: with `disable-model-invocation: true` the description stays out of context entirely. That skill
  costs nothing per turn and can only be run by typing `/name`.
- REC (Anthropic best-practices [S]):
  - write the description in the third person ("Processes Excel files…", not "I can help…"), because it is
    injected into the system prompt;
  - say both **what** the skill does and **when** to use it, with concrete trigger terms;
  - avoid vague descriptions such as "Helps with documents".
- REC (skill-creator, local `plugins/skill-creator/skills/skill-creator/SKILL.md` [D]): "Claude has a tendency to
  'undertrigger' skills … make the skill descriptions a little bit 'pushy'."
  - Also: "Claude only consults skills for tasks it can't easily handle on its own". Simple one-step queries may
    not trigger a skill even when the description matches perfectly.
  - Put all "when to use" information in the description, not in the body.
- REC (plugin-dev skill-development [D local]): use the phrasing "This skill should be used when the user asks to
  …" and list the literal trigger phrases in quotes. This is the house style of Anthropic's plugin-dev.

### 1.3 Size and progressive disclosure
- FACT/REC: three loading levels. The sources agree on them: engineering blog 2025-10-16 [S], agentskills spec [S],
  Claude Code docs [D].
  1. Metadata is always loaded (about 100 tokens).
  2. The SKILL.md body loads on activation. The spec recommends under 5,000 tokens.
  3. Bundled files load only when read. Scripts are **executed without loading their source**; only their output
     costs tokens.
- REC (all Anthropic sources): **keep SKILL.md under 500 lines** and move detail into reference files. Other forms
  of the same advice:
  - plugin-dev [D local]: a body of "1,500–2,000 words, <3,000";
  - best-practices [S]: add a table of contents to reference files longer than 100 lines;
  - skill-creator [D local]: add one to files longer than 300 lines.
- REC (best-practices [S]): keep references **one level deep** from SKILL.md. Nested references get partial reads
  (`head -100`).
- FACT [D skills.md]: an invoked skill's body **stays in context for the rest of the session**. Consequences:
  - it is not re-read, so write it as standing instructions, not one-time steps;
  - after compaction only the first 5,000 tokens per skill are re-attached, with 25,000 tokens shared across all
    skills.
- Conventional layout (agentskills spec, skill-creator, plugin-dev):
  - `scripts/`: executable code for deterministic or repetitive work;
  - `references/`: documentation that is read on demand;
  - `assets/`: files used in the output (templates, fonts, images).

### 1.4 How a skill references its bundled scripts
- FACT [D skills.md §substitutions]: these placeholders are substituted in **both** the skill's markdown and the
  Bash rules of `allowed-tools`:
  - `${CLAUDE_SKILL_DIR}` resolves to the skill's own directory. In a plugin this is the skill's subdirectory, not
    the plugin root.
  - `${CLAUDE_PLUGIN_ROOT}` and `${CLAUDE_PLUGIN_DATA}` are available in plugin skills only.
  - `${CLAUDE_PROJECT_DIR}` is also available.
  - Using the same variable in both places "lets a skill run a bundled script without a permission prompt".
    Documented pattern:
    `allowed-tools: Bash(${CLAUDE_SKILL_DIR}/scripts/render.sh *)` together with
    "Run `${CLAUDE_SKILL_DIR}/scripts/render.sh <csv-file>`".
- FACT [D plugins-ref.md L765]: **`CLAUDE_PLUGIN_ROOT`, `CLAUDE_PLUGIN_DATA` and `CLAUDE_PROJECT_DIR` are NOT
  present in the environment of commands Claude runs through the Bash tool.** They exist as real environment
  variables only for hooks, MCP servers and LSP servers. In skill content they are text placeholders replaced at
  load time.
  - Design consequence [I]: a script invoked from a skill must not read `$CLAUDE_PLUGIN_ROOT` from its environment.
    Pass it as an argument, or derive it from `$0` / `__file__`.
- FACT [D skills.md]: `` !`cmd` `` and ```` ```! ```` blocks run **before** the model sees the skill. Rules:
  - they are checked against permission rules;
  - a non-zero exit **aborts the whole invocation**, except exit 1 from grep/diff-like commands; the documented fix
    is to append `|| true`;
  - each command has a 2-minute timeout;
  - `disableSkillShellExecution` turns them off;
  - they never run for skills synced from claude.ai.
- REC (best-practices [S]):
  - "Solve, don't defer": scripts handle their own errors and use no unexplained constants;
  - say explicitly whether Claude should **execute** a script or **read** it;
  - validate an intermediate plan before applying changes ("plan-validate-execute");
  - make validator messages verbose.

### 1.5 What NOT to put in a skill
- REC (best-practices [S]): Anthropic's list of things to keep out:
  - explanations of things Claude already knows ("Claude is already very smart");
  - time-sensitive statements (use an "Old patterns" section instead);
  - several alternative tools where one default would do;
  - inconsistent terminology;
  - Windows-style backslash paths;
  - unqualified MCP tool names (write `Server:tool`);
  - the assumption that packages are already installed.
- REC (skill-creator "Principle of Lack of Surprise" [D local]): no malware or exploit code, and no content that
  "would surprise the user in their intent if described".
- REC (plugin-dev skill-development [D local]): do not duplicate content between SKILL.md and references; each fact
  lives in one place.
- NOT FOUND: an explicit Anthropic rule against README, INSTALL or CHANGELOG files *inside a skill*. The current
  `anthropics/skills` skill-creator has no such section; this was checked by WebFetch of the raw file on
  raw.githubusercontent.com [S].
- FACT [D skills.md]: skills that are listed but never used still cost context on every turn. `/skill-doctor` (≥
  v2.1.252) reports this; see §5.

## 2. Plugin structure

### 2.1 Manifest and layout
All of this section comes from plugins-ref.md [D] and plugins.md [D].
- FACT: `.claude-plugin/plugin.json` is **optional**; default locations are auto-discovered.
  - Only `name` is required when a manifest exists: kebab-case, no spaces or control or bidi characters.
  - Optional fields: `displayName`, `version`, `description`, `author{name,email,url}`, `homepage`, `repository`,
    `license`, `keywords`, `metadata` (ignored by Claude Code), `defaultEnabled` (default `true`), `$schema`,
    `userConfig`, `dependencies`, `channels`, plus component path overrides.
  - Only `plugin.json` goes inside `.claude-plugin/`. Everything else sits at the plugin root.
- FACT: component directories:
  - `skills/<name>/SKILL.md`;
  - `commands/*.md`, described as a legacy flat format: "Use `skills/` for new plugins". The official
    example-plugin README [D local] says `commands/` "is loaded identically to `skills/<name>/SKILL.md`";
  - `agents/`, `workflows/`, `output-styles/`;
  - `hooks/hooks.json`, and other `*.json` files in `hooks/`;
  - `.mcp.json`, `.lsp.json`;
  - `monitors/monitors.json` (experimental);
  - `settings.json`, which supports only the `agent` and `subagentStatusLine` keys;
  - `bin/`.
- FACT: component paths must start with `./` and must not escape the plugin root. On macOS and Linux, paths
  containing a backslash are rejected. Files outside the plugin are not copied into the cache.
- FACT: plugin agents **cannot** declare `hooks`, `mcpServers` or `permissionMode`; they are ignored for security.
- FACT: plugin skills are namespaced `/plugin:skill`. The bare `/skill` also works when there is no name conflict.
- FACT: a plugin with exactly one skill may place `SKILL.md` at the plugin root.
- REC (docs): use `skills/` rather than `commands/`. "Start with standalone `.claude/` config for quick iteration,
  then convert to a plugin when ready to share."
- REC (claude.com/docs/plugins/submit [S]): "The best plugins bundle related capabilities together into a coherent
  package that solves a specific job function or workflow end-to-end."

### 2.2 How executables reach the session
- FACT [D plugins-ref.md L1003]: the `bin/` directory — "Executables added to the Bash tool's `PATH` and invokable as
  bare commands while the plugin is enabled."
- FACT [D mkt.md L859–866]: **claude.ai organization distribution rejects any plugin that has a top-level `bin/`**,
  with the message "Plugin contains a top-level bin/ directory".
  - Anthropic's advice: "Keep executables in another directory, such as `scripts/`, and reference them as
    `${CLAUDE_PLUGIN_ROOT}/scripts/<name>`".
  - REC [I]: for maximum portability (Cowork and org sync), avoid `bin/`. Call scripts through
    `${CLAUDE_PLUGIN_ROOT}` or `${CLAUDE_SKILL_DIR}` placeholders instead.
- NOT DOCUMENTED: whether `bin/` is also on the `PATH` of hook processes. The docs say only "the Bash tool's
  `PATH`" [D]. Treat it as Bash-tool only.
- FACT: `${CLAUDE_PLUGIN_ROOT}` **changes on every update** for copied (marketplace) installs.
  - The old directory is swept about 14 days later.
  - Do not write state there. Use `${CLAUDE_PLUGIN_DATA}` (`~/.claude/plugins/data/<id>/`), which survives
    updates and is deleted on the last uninstall.
  - Documented pattern for dependencies: a SessionStart hook that diffs the manifest against the one in
    `CLAUDE_PLUGIN_DATA` and reinstalls into `CLAUDE_PLUGIN_DATA` when they differ.
- FACT: Node dependencies are auto-installed in the cache when `package.json` sits beside `package-lock.json` or
  `bun.lock`.
  - The install runs `npm ci --ignore-scripts` with a 60-second timeout.
  - yarn and pnpm lockfiles are **not** supported.
  - "A failed or skipped install never blocks the plugin."
- NOT DOCUMENTED: an equivalent for Python (uv or pip). Use the `CLAUDE_PLUGIN_DATA` SessionStart pattern [I].

### 2.3 Per-user and per-project configuration
- FACT [D settings-ref.md]: `enabledPlugins` maps `"plugin@marketplace"` to a boolean and works at any settings
  scope.
  - **Project settings take precedence over user settings.** Setting a plugin to `false` in `~/.claude` does not
    disable one that a project enables.
  - To opt out of a project-enabled plugin on one machine, set it to `false` in `.claude/settings.local.json`.
  - Enabling an external-source plugin in a project's settings **does not install it** for other people.
- FACT [D plugins-ref.md]: `defaultEnabled: false` ships a plugin that installs disabled.
  - Docs: "Use this for plugins that add cost or scope a user should opt into."
  - Once a user has an `enabledPlugins` entry, a later change to `defaultEnabled` does not flip it.
- FACT [S plugins-reference]: **`userConfig`** declares typed options: `string`, `number`, `boolean`, `directory`,
  `file`, plus `sensitive`, `options`, `multiple`, `min`/`max`.
  - Values are prompted on install or set with `/config` or `claude plugin install --config k=v`.
  - Non-sensitive values go to `pluginConfigs[<id>].options` in settings; sensitive ones go to the keychain or
    `.credentials.json`.
  - They are substituted as `${user_config.KEY}` in MCP/LSP config and skill/agent content, but **rejected in
    hook, monitor and `headersHelper` commands** since v2.1.207, to prevent shell injection.
- REC (plugin-dev plugin-settings [D local]): the older convention is a **`.claude/<plugin>.local.md`** file.
  - Its YAML frontmatter holds settings such as `enabled: true`, and its body holds prose.
  - The file is gitignored.
  - Hooks exit 0 immediately when it is absent ("Plugin not configured, skip").
  - The skill claims changes need a restart. That claim looks stale against the current `/reload-plugins` docs [I].
  - This is Anthropic plugin-dev convention, not a Claude Code feature. `userConfig` is the documented mechanism
    for user-level options. For **per-project** state, a project file (`.local.md`, or a JSON/TOML file in the
    repo) is still the only pattern, because `userConfig` is user-scoped [I].

## 3. Hooks in plugins
- FACT [D hooks.md]: format and variables.
  - Hooks live in `hooks/hooks.json` with the same schema as the settings `hooks` object; an optional
    `description` is allowed.
  - Handler types: `command`, `http`, `mcp_tool`, `prompt`, `agent`.
  - Hooks that target the plugin's own MCP tools must use the scoped name
    `mcp__plugin_<plugin>_<server>__<tool>`.
- FACT [D hooks.md L430; hooks-guide.md]: default **timeouts**.
  - 600 s for `command`, `http` and `mcp_tool`.
  - 30 s for `prompt`, 60 s for `agent`.
  - 30 s on `UserPromptSubmit` and the model-switch events; 10 s on `MessageDisplay`.
  - `SessionEnd` hooks share a 1.5 s budget, raised to match a longer per-hook timeout, up to 60 s.
  - `async: true` is not bounded by the timeout.
  - The plugin-dev skill says "Command hooks (60s)", which is **stale** [D local vs D docs].
- FACT [D hooks.md L871–874]: **a timed-out `command`/`http`/`mcp_tool` PreToolUse hook does NOT block the tool
  call.** "Don't count on a stalled hook to act as a gate." It fails open.
- FACT [D hooks.md]: exit codes and matching.
  - Exit 0 means stdout is parsed as JSON only if it starts with `{`.
  - **Exit 2 blocks**, and stderr becomes the reason.
  - Other exit codes are non-blocking errors shown as "hook error" in the transcript.
  - All matching hooks run **in parallel**. Identical handlers in several *settings* files run once, but a
    plugin's copy of a handler runs separately.
  - A PreToolUse `deny` beats even `bypassPermissions`. An `allow` cannot loosen settings deny rules.
- FACT [D hooks.md]: the **`if` field** (for example `"if": "Bash(git push:*)"`) narrows a handler to specific
  subcommands, "so `block-rm.sh` only spawns when both filters match".
  - The official `security-guidance` plugin combines `if` with `asyncRewake: true` so that its hooks run in the
    background [D local `plugins/security-guidance/hooks/hooks.json`].
- FACT [D hooks.md]: **cross-platform** behaviour.
  - Shell form runs under `sh -c` on macOS and Linux, and on Windows under **Git Bash**, or PowerShell if Git Bash
    is absent.
  - Exec form (`command` + `args`) needs a real `.exe` on Windows. `.cmd` shims fail; use `node <script>`.
  - File paths in tool input arrive with backslashes on Windows, even under Git Bash.
  - Hooks have no `/dev/tty`.
  - REC (docs): prefer exec form with `args` for paths; in shell form, double-quote `"${CLAUDE_PLUGIN_ROOT}"`.
- REC (hooks.md "Security best practices" [D]):
  - validate and sanitize input;
  - quote all variables;
  - block `..` path traversal;
  - use absolute paths via the variables;
  - skip `.env`, `.git/` and key files.
  - Disclaimer: command hooks "execute shell commands with your full user permissions."
- FACT [D hooks.md]: in `-p`/SDK sessions the workspace is treated as trusted. That means **repo-committed hooks run
  in folders never trusted interactively.**
- REC (plugin-dev hook-development [D local]):
  - do not rely on execution order, create long-running hooks, hardcode paths or log secrets;
  - document any activation flag in the README.
- Observed in an official hook, not documented as a rule [D local
  `plugins/hookify/hooks/pretooluse.py` L61]: `# ALWAYS exit 0 - never block operations due to hook errors`. A
  broken hook script fails open.
- **When the plugin is irrelevant to the current project**, no single documented rule covers the case. The levers
  that exist:
  1. `enabledPlugins` per project, or `defaultEnabled: false` [D];
  2. narrow `matcher` / `if` so that no process spawns at all [D];
  3. an early `exit 0` when the project has no config marker (plugin-dev pattern [D local]);
  4. `async` for anything slow [D].
  - Why this matters [I]: a user-scope plugin's hooks fire in **every** project where the plugin is enabled.
  - The cheapest correct pattern is (2) followed by (3), with exit 0 and no output. Printing nothing matters
    because SessionStart and UserPromptSubmit add plain stdout to the model's context.

## 4. Public distribution
- **FACT, the key correction [D plugins.md L360–375; S discover-plugins]:** `claude-plugins-official` is "curated
  separately. Anthropic decides which plugins to include at its discretion. **There is no application process, and
  the submission form does not add plugins to the official marketplace.**"
  - Third-party submissions go to **`claude-community`** (repo `anthropics/claude-plugins-community`). That repo is
    a read-only mirror, synced nightly. PRs opened against it are auto-closed [S].
  - Approved plugins are **pinned to a commit SHA**. CI bumps the pin as you push, with "automated safety
    screening" on each update.
- CONFLICT to flag: claude.com/docs/plugins/submit [S] says the directory "is surfaced as the official
  `claude-plugins-official` marketplace".
  - This contradicts code.claude.com. I treat the more specific code.claude.com statement as authoritative [I].
  - Volatile: check again before promising anyone anything.
- FACT [S claude.com/docs/plugins/submit]: how to submit.
  - Forms: claude.ai (Team or Enterprise with directory access) or the Console
    (`platform.claude.com/plugins/submit`, for individuals).
  - "The repo must be public—closed-source plugins are not accepted."
  - Run `claude plugin validate` first. `--strict` turns warnings into errors [D plugins.md]; the pipeline runs the
    same check.
  - Two tiers: "Community", which gets basic automated review, and "Anthropic Verified", which gets additional
    quality and safety review with no guarantee.
  - Prefer MCP connectors from the Connectors Directory; that "will increase the likelihood of verification".
- FACT [S support.claude.com Software Directory Policy, updated 2026-04-15], rules that apply to plugins:
  - comply with the Usage Policy;
  - no evading guardrails or sandboxes;
  - no extracting memory, chat history or uploaded files;
  - collect only the data you need, and **no extraneous conversation data "even for logging"**;
  - a privacy policy link;
  - verified contact and support channels;
  - documented functionality and troubleshooting;
  - a test account and **at least three working example prompts**;
  - ongoing maintenance;
  - **no hidden, obfuscated or encoded instructions**;
  - no ads.
  - Whether every item binds pure-local plugins (as opposed to connectors) is not stated [I].
- NOT FOUND: a published scoring rubric for community review beyond "validate + automated safety screening". I
  searched "plugin directory submission review criteria"; the only detailed results were community blogs
  (systemprompt.io, flowlines.ai [C]), which I did not rely on.
- FACT [D marketplace README, local]: the marketplace `name` is an **immutable slug**.
  - Change `displayName` instead.
  - A rename that cannot be avoided needs the `renames` map (≥ v2.1.193).
  - Reserved marketplace names include `claude-plugins-official`, `claude-community`, `agent-skills` and others,
    plus impersonating variants [S plugin-marketplaces].
- FACT [D plugins-ref / mkt]: versioning.
  - Setting `version` **pins** the plugin: users get updates only when it is bumped.
  - "Avoid setting `version` in both `plugin.json` and the marketplace entry"; plugin.json silently wins.
- REC (docs "Share your plugins" [D plugins.md]): README with install and usage instructions, a versioning strategy,
  testing by others. `LICENSE` and `CHANGELOG.md` appear in the reference layout [S].
- Cross-platform:
  - FACT: forward-slash component paths; Git Bash or PowerShell for hooks; exec-form `.exe` caveat [D].
  - REC (best-practices [S]): "Unix-style paths work across all platforms".
  - REC [I]: write hook and helper scripts in a portable runtime the user surely has, or ship
    `shell: "powershell"` variants. Anthropic's own plugins use `python3` and `bash` wrappers
    (hookify, security-guidance [D local]), so a POSIX + python3 baseline is the de-facto norm.

## 5. Evaluating skills and plugins
- FACT [D/S code.claude.com/docs/en/plugin-evals; tool-results copy]: `claude plugin eval` (≥ v2.1.269).
  - The suite lives in `evals/<case>/` with `prompt.md` (frontmatter: `max_turns`, `allowed_tools`, `model`,
    `timeout_seconds`, …) and `graders/*.md`.
  - `claude plugin eval init` proposes cases; `--bare` gives a blank template.
  - Each case runs **3 times** in a fresh isolated headless session. A **no-plugin baseline arm** runs by default
    and the report shows `WITH / W/OUT / Δ`.
  - Grader types: `regex`, `tool_used`, `tool_order`, `file_exists` (free); `llm`, `baseline` (judge model). There
    are no custom-code graders.
  - Trigger check: `type: tool_used, tool: Skill, input_match: '"skill"\s*:\s*"(?:[\w-]+:)?name"'`. Skill-invocation
    graders are excluded from the Δ score.
  - Mocks are supported for MCP tools. The results support HTML/JSON output, a threshold and CI gating.
  - Every run is a real, billed model call.
- REC (plugin-evals [D]):
  - pair one outcome grader with one "how" grader (`tool_used` / `tool_order`);
  - use `regex` for long outputs;
  - write `llm` rubrics as concrete PASS/FAIL conditions;
  - if a `Skill` grader passes but Δ<0, suspect the judge first (`--judge-model sonnet`).
- FACT [D skills.md]: **skill-creator** plugin (`evals/evals.json` inside the skill; a *different* format).
  - It runs subagent-isolated runs, grading, and a with/without benchmark.
  - It supports a blind A/B between skill versions.
  - Its **description tuning** takes 20 should/should-not-trigger queries, splits them 60/40 train/test, runs each
    query 3×, iterates up to 5 times and picks the best result by *test* score [D local SKILL.md].
  - REC: near-miss negative queries matter most.
- FACT [D skills.md]: `/skill-doctor` (≥ v2.1.252) shows each skill's context cost and usage. It flags skills that
  were never invoked and plugins not used recently, in `/plugin` → Stats. It is a pruning tool, not an evaluator.
  `/doctor` estimates the cost of the skill listing.
- FACT [D plugins.md]: dev loop tools.
  - `claude --plugin-dir ./p` (also a folder of plugins, or a `.zip`), `--plugin-url` for CI artifacts.
  - `/reload-plugins`, `claude --debug`, `/hooks`, `/context`.
  - `claude plugin init <name>` scaffolds a skills-dir plugin.
- REC (best-practices [S]):
  - evaluation-driven development: baseline without the skill → three scenarios → minimal instructions → iterate;
  - test with Haiku, Sonnet and Opus;
  - "Claude A writes / Claude B tests".

## 6. Stale or contradictory guidance to watch
1. plugin-dev skills (local, cache `c2301c68838d`) contradict current docs in two places. They say hooks need a
   restart (docs: `/reload-plugins`) and give a 60 s command timeout (docs: 600 s).
   - Their own SKILL.md files run 476–884 lines, above the 500-line recommendation they repeat.
   - Use them for patterns, not for numbers [D local].
2. The official example-plugin puts `version:` in skill frontmatter. Claude Code ignores unknown fields, so this is
   harmless but inert [D local + D skills.md].
3. The submit page says "official marketplace" while code.claude.com says "community marketplace" (§4).
4. "Required" frontmatter differs by surface: Claude Code requires nothing, while the Agent Skills spec and the API
   require `name` and `description` (§1.1).

## 7. Queries run that found nothing primary
- "Anthropic plugin directory submission review criteria …": only policy pages plus community blogs.
- A search for a skill-creator "what not to include" section in anthropics/skills: absent in the current file.
- `bin/` PATH scope for hooks: not stated in the plugins-reference, plugins or hooks pages.
- Official Python-dependency install for plugins: not found. Only the Node lockfile install is documented.
