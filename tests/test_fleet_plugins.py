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
