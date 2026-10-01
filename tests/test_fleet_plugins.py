import json
import subprocess

from flotilla.fleet import plugins


def plugin_dir(tmp_path, name, *, mcp_json=None, manifest=None):
    root = tmp_path / "cache" / name
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text(json.dumps(manifest or {"name": name}), encoding="utf-8")
    if mcp_json is not None:
        (root / ".mcp.json").write_text(json.dumps(mcp_json), encoding="utf-8")
    return root


def entry(plugin_id, path, *, enabled=True, scope="user", **more):
    return {"id": plugin_id, "scope": scope, "enabled": enabled, "installPath": str(path), **more}


class FakeList:
    """`claude plugin list --json` answering from a list of entries, or failing as told."""

    def __init__(self, entries=(), *, returncode=0, stdout=None, timeout=False, missing=False):
        self.entries = list(entries)
        self.returncode, self.stdout, self.timeout, self.missing = returncode, stdout, timeout, missing
        self.calls = []

    def __call__(self, cmd, **kwargs):
        self.calls.append((cmd, kwargs.get("cwd")))
        if self.missing:
            raise FileNotFoundError("claude")
        if self.timeout:
            raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout", 30))
        out = json.dumps(self.entries) if self.stdout is None else self.stdout
        return subprocess.CompletedProcess(cmd, self.returncode, out, "boom" if self.returncode else "")


def test_the_enabled_plugins_that_bring_mcp_servers_are_named(tmp_path):
    pdf = plugin_dir(tmp_path, "pdf", mcp_json={"mcpServers": {"pdf": {"command": "npx"}}})
    serena = plugin_dir(tmp_path, "serena", mcp_json={"serena": {"command": "uvx"}})
    tool = plugin_dir(tmp_path, "tool", manifest={"name": "tool", "mcpServers": {"t": {"command": "t"}}})
    skills = plugin_dir(tmp_path, "skills")
    fake = FakeList([entry("pdf@m", pdf), entry("serena@m", serena), entry("tool@m", tool),
                     entry("skills@m", skills)])
    assert plugins.mcp_plugins(fake, tmp_path) == ["pdf@m", "serena@m", "tool@m"]
    assert fake.calls[0] == (["claude", "plugin", "list", "--json"], str(tmp_path))


def test_a_plugin_not_enabled_here_is_not_named(tmp_path):
    pdf = plugin_dir(tmp_path, "pdf", mcp_json={"pdf": {"command": "npx"}})
    fake = FakeList([entry("pdf@m", pdf, enabled=False, scope="local", projectPath="/elsewhere")])
    assert plugins.mcp_plugins(fake, tmp_path) == []


def test_a_plugin_enabled_only_at_project_scope_is_named(tmp_path):
    pdf = plugin_dir(tmp_path, "pdf", mcp_json={"pdf": {"command": "npx"}})
    fake = FakeList([entry("pdf@m", pdf, scope="project", projectPath=str(tmp_path))])
    assert plugins.mcp_plugins(fake, tmp_path) == ["pdf@m"]


def test_servers_listed_inline_by_claude_count(tmp_path):
    bare = plugin_dir(tmp_path, "bare")
    fake = FakeList([entry("bare@m", bare, mcpServers={"b": {"command": "b"}})])
    assert plugins.mcp_plugins(fake, tmp_path) == ["bare@m"]


def test_a_plugin_whose_install_path_is_missing_is_omitted(tmp_path):
    fake = FakeList([entry("gone@m", tmp_path / "nowhere", mcpServers={"g": {"command": "g"}})])
    assert plugins.mcp_plugins(fake, tmp_path) == []


def test_unreadable_files_keep_a_plugin_enabled(tmp_path):
    odd = plugin_dir(tmp_path, "odd")
    (odd / ".mcp.json").write_text("{not json", encoding="utf-8")
    (odd / ".claude-plugin" / "plugin.json").write_text("{not json", encoding="utf-8")
    empty = plugin_dir(tmp_path, "empty", mcp_json={"mcpServers": {}})
    fake = FakeList([entry("odd@m", odd), entry("empty@m", empty)])
    assert plugins.mcp_plugins(fake, tmp_path) == []


def test_one_id_listed_at_several_scopes_is_named_once(tmp_path):
    pdf = plugin_dir(tmp_path, "pdf", mcp_json={"pdf": {"command": "npx"}})
    fake = FakeList([entry("pdf@m", tmp_path / "old-version"), entry("pdf@m", pdf, scope="project",
                                                                       projectPath=str(tmp_path))])
    assert plugins.mcp_plugins(fake, tmp_path) == ["pdf@m"]


def test_claude_failing_answers_none(tmp_path):
    for fake in (FakeList(returncode=1), FakeList(timeout=True), FakeList(missing=True), FakeList(stdout="nope"),
                 FakeList(stdout='{"not": "a list"}')):
        assert plugins.mcp_plugins(fake, tmp_path) is None


def world_of_plugins(tmp_path):
    """Four enabled plugins with MCP servers (one only at project scope, one is flotilla) and one without."""
    ids = {"playwright@official": "playwright", "serena@official": "serena", "flotilla@flotilla": "flotilla"}
    entries = [entry(pid, plugin_dir(tmp_path, name, mcp_json={name: {"command": name}})) for pid, name in ids.items()]
    entries.append(entry("pdf@synced", plugin_dir(tmp_path, "pdf", mcp_json={"pdf": {"command": "npx"}}),
                         scope="project", projectPath=str(tmp_path)))
    entries.append(entry("skills@official", plugin_dir(tmp_path, "skills")))
    entries.append(entry("off@official", plugin_dir(tmp_path, "off", mcp_json={"o": {"command": "o"}}),
                         enabled=False))
    return entries


def post_keeping(*kept, name="judge"):
    from dataclasses import replace
    from flotilla.posts import TEMPLATE_DIR, load_post
    return replace(load_post(TEMPLATE_DIR / "reviewer.md"), name=name, plugins=tuple(kept))


def test_a_post_that_declares_nothing_turns_off_every_mcp_plugin_but_flotilla(tmp_path):
    narrow = plugins.narrowing(FakeList(world_of_plugins(tmp_path)), tmp_path)
    reviewer = post_keeping(name="reviewer")
    assert narrow.turned_off(reviewer) == ["pdf@synced", "playwright@official", "serena@official"]
    assert json.loads(narrow.settings_json(reviewer)) == {"enabledPlugins": {
        "pdf@synced": False, "playwright@official": False, "serena@official": False}}
    assert narrow.warnings([reviewer]) == []


def test_a_post_keeps_the_plugins_it_declares(tmp_path):
    narrow = plugins.narrowing(FakeList(world_of_plugins(tmp_path)), tmp_path)
    judge = post_keeping("playwright@official")
    assert narrow.turned_off(judge) == ["pdf@synced", "serena@official"]
    assert "playwright@official" not in json.loads(narrow.settings_json(judge))["enabledPlugins"]


def test_a_plugin_enabled_only_at_project_scope_is_still_turned_off(tmp_path):
    narrow = plugins.narrowing(FakeList(world_of_plugins(tmp_path)), tmp_path)
    assert "pdf@synced" in json.loads(narrow.settings_json(post_keeping()))["enabledPlugins"]


def test_flotilla_is_never_turned_off_even_when_it_brings_servers(tmp_path):
    narrow = plugins.narrowing(FakeList(world_of_plugins(tmp_path)), tmp_path)
    assert not any(pid.startswith("flotilla@") for pid in narrow.turned_off(post_keeping()))


def test_nothing_to_turn_off_means_no_settings(tmp_path):
    narrow = plugins.narrowing(FakeList([entry("skills@official", plugin_dir(tmp_path, "skills"))]), tmp_path)
    assert narrow.turned_off(post_keeping()) == [] and narrow.settings_json(post_keeping()) == ""


def test_a_failing_plugin_list_narrows_nothing_and_says_so(tmp_path):
    for fake in (FakeList(returncode=1), FakeList(timeout=True)):
        narrow = plugins.narrowing(fake, tmp_path)
        assert narrow.settings_json(post_keeping()) == "" and narrow.turned_off(post_keeping()) == []
        (line,) = narrow.warnings([post_keeping()])
        assert "plugin set not narrowed" in line and str(tmp_path) in line


def test_a_declared_plugin_that_is_not_installed_is_named_and_changes_nothing(tmp_path):
    narrow = plugins.narrowing(FakeList(world_of_plugins(tmp_path)), tmp_path)
    judge = post_keeping("playwright@official", "ghost@nowhere")
    assert narrow.turned_off(judge) == ["pdf@synced", "serena@official"]
    (line,) = narrow.warnings([judge, judge])
    assert "`ghost@nowhere`" in line and "not installed" in line and "`judge`" in line


def test_a_declared_plugin_installed_but_not_enabled_here_is_named(tmp_path):
    narrow = plugins.narrowing(FakeList(world_of_plugins(tmp_path)), tmp_path)
    (line,) = narrow.warnings([post_keeping("off@official")])
    assert "`off@official`" in line and "not enabled" in line


def test_plugin_ids_that_need_quoting_stay_valid_json(tmp_path):
    odd = 'we"ird\\id@m'
    narrow = plugins.narrowing(FakeList([entry(odd, plugin_dir(tmp_path, "weird", mcp_json={"w": {"command": "w"}}))]),
                               tmp_path)
    assert json.loads(narrow.settings_json(post_keeping())) == {"enabledPlugins": {odd: False}}
