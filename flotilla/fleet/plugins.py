"""Which enabled plugins bring MCP servers (H49, decisions 153-155).

A background seat starts every MCP server its plugins bring, whatever its post needs. The plugins are asked of
`claude plugin list --json` run in the main checkout, the directory seats are launched from: it reports `enabled`
for the directory it runs in, whatever scope enabled the plugin. A plugin brings MCP servers when the listing names
them, or its `.mcp.json` or the `mcpServers` of its `.claude-plugin/plugin.json` does. A plugin whose files cannot
be read is left out, so it stays enabled: doubt keeps behaviour. Nothing here writes Claude Code's files.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


def listing(run, cwd: Path, *, timeout: float = 30) -> list[dict] | None:
    """The entries of `claude plugin list --json` run in `cwd`, or None when it could not be asked."""
    try:
        done = run(["claude", "plugin", "list", "--json"], cwd=str(cwd), capture_output=True, text=True,
                   check=False, timeout=timeout)
        entries = json.loads(done.stdout or "") if done.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None
    if not isinstance(entries, list):
        return None
    return [entry for entry in entries if isinstance(entry, dict) and isinstance(entry.get("id"), str)]


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None


def _servers_in_mcp_json(data) -> bool:
    if not isinstance(data, dict):
        return False
    servers = data.get("mcpServers") if isinstance(data.get("mcpServers"), dict) else data
    return any(isinstance(spec, dict) for spec in servers.values())


def brings_mcp(entry: dict) -> bool:
    """Whether this listed plugin brings MCP servers; False where its install path is missing (doubt keeps it)."""
    root = Path(str(entry.get("installPath") or ""))
    if not entry.get("installPath") or not root.is_dir():
        return False
    if isinstance(entry.get("mcpServers"), dict) and entry["mcpServers"]:
        return True
    if _servers_in_mcp_json(_read_json(root / ".mcp.json")):
        return True
    manifest = _read_json(root / ".claude-plugin" / "plugin.json")
    return isinstance(manifest, dict) and bool(manifest.get("mcpServers"))


def mcp_plugins_in(entries: list[dict]) -> list[str]:
    """The ids enabled here that bring MCP servers, each once, sorted."""
    return sorted({entry["id"] for entry in entries if entry.get("enabled") is True and brings_mcp(entry)})


def mcp_plugins(run, cwd: Path, *, timeout: float = 30) -> list[str] | None:
    entries = listing(run, cwd, timeout=timeout)
    return None if entries is None else mcp_plugins_in(entries)
