import json
import os
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
        assert command.startswith('"${CLAUDE_PLUGIN_ROOT}/scripts/flotilla" hook ')
    assert os.access(ROOT / "scripts" / "flotilla", os.X_OK)
