import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_manifest_loads_enabled_under_the_immutable_slug():
    # defaultEnabled false keeps the plugin out of the session entirely, --plugin-dir included (measured
    # 2026-09-24: no flotilla commands, not in the session's plugin list). "Installed is not active" is
    # held at the project level instead: hooks stay silent and nothing is written until onboarding.
    manifest = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert manifest["name"] == "flotilla"
    assert manifest.get("defaultEnabled", True) is True


def test_manifest_version_matches_the_package():
    from flotilla import __version__
    manifest = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert manifest["version"] == __version__


def test_every_hook_command_points_at_the_executable_entry():
    hooks = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
    commands = [h["command"] for groups in hooks.values() for group in groups for h in group["hooks"]]
    assert commands, "no hook commands declared"
    for command in commands:
        assert command.startswith('"${CLAUDE_PLUGIN_ROOT}/bin/flotilla" hook ')
    assert os.access(ROOT / "bin" / "flotilla", os.X_OK)


def test_the_command_line_lives_in_bin_so_only_claude_code_installs_the_plugin():
    """Directory readiness: flotilla works only in Claude Code (Bash, `claude --bg`, git worktrees). `bin/` puts the
    command line on the Bash tool's PATH there, and claude.ai chat and Cowork do not install a plugin that has it
    (plugin manifest reference, standard layout). One copy: no `bin/flotilla` beside it."""
    assert (ROOT / "bin" / "flotilla").is_file() and not (ROOT / "bin" / "flotilla").is_symlink()
    assert not (ROOT / "scripts").exists()


def test_the_repository_is_its_own_marketplace():
    market = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
    plugin = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert market["name"] == "flotilla" and market["owner"]["name"]
    assert [(p["name"], p["source"]) for p in market["plugins"]] == [(plugin["name"], "./")]


def test_the_readme_says_how_to_install():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    # W1 (worldcore field test): without --scope project the marketplace lands in the person's own settings, and
    # the project's .claude/settings.json gets no marketplace entry - the claim below would be false.
    assert "claude plugin marketplace add TensusDS/flotilla --scope project" in text
    assert "claude plugin install flotilla@flotilla --scope project" in text


def test_the_readme_says_what_project_scope_commits():
    text = " ".join((ROOT / "README.md").read_text(encoding="utf-8").split())
    assert "commits a marketplace entry that points at this repository" in text


def test_the_listing_names_privacy_support_and_documentation():
    """Directory readiness, p.3 (Software Directory Policy: a privacy policy link, verified support channels,
    documentation). The directory reads these fields from plugin.json; each points at a file in this repository."""
    manifest = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    base = "https://github.com/TensusDS/flotilla"
    assert manifest["privacyPolicyUrl"] == f"{base}/blob/main/PRIVACY.md"
    assert manifest["supportUrl"] == f"{base}/blob/main/SUPPORT.md"
    assert manifest["documentationUrl"] == f"{base}/blob/main/USER_MANUAL.md"
    for name in ("PRIVACY.md", "SUPPORT.md", "SECURITY.md", "USER_MANUAL.md", "CHANGELOG.md"):
        assert (ROOT / name).is_file(), name
    assert "Claude Code only" in manifest["description"]


def test_the_readme_gives_at_least_three_examples():
    """The policy asks for "at least three working examples of prompts or use cases"; each names a skill that exists."""
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    section = text.split("## Examples", 1)[1].split("\n## ", 1)[0]
    used = set(re.findall(r"/flotilla:([a-z-]+)", section))
    assert len(re.findall(r"^\d+\. ", section, re.M)) >= 3
    assert used and all((ROOT / "skills" / name / "SKILL.md").is_file() for name in used)


def test_the_listing_icon_is_what_the_directory_accepts():
    """The directory takes a square PNG or JPEG of 512 to 2048 px a side, under 2 MB, and only once: the first time
    the plugin is saved in the developer portal. Read from the PNG header, so a replaced file is checked too."""
    import struct
    manifest = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    icon = (ROOT / ".claude-plugin" / manifest["icon"].removeprefix("./.claude-plugin/")).resolve()
    data = icon.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) < 2 * 1024 * 1024
    width, height = struct.unpack(">II", data[16:24])
    assert width == height and 512 <= width <= 2048
