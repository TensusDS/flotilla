#!/usr/bin/env python3
"""Measure which MCP servers a background seat starts, under three launch variants (plan part 8, Task 1).

Not part of the plugin: kept for the record of decision 153. It raises one `claude --bg` session at a time from a
trusted directory, with `--add-dir <a temp dir>` and a trivial prompt, in three variants:

  a  no extra flags
  b  --settings '{"enabledPlugins": {<id>: false, ...}}' for every enabled plugin with MCP servers except flotilla
  c  --strict-mcp-config alone

For each it waits for the session's process tree to settle (walking /proc/*/stat parent links from the pid the
census gives), counts the processes under each direct child whose command line names a plugin's MCP server, sums
their RSS, reports the other descendants apart (servers from the person's own MCP configuration), and stops and
removes the session.
Before each launch it reads MemAvailable and waits while it is under the floor (every 60 s, at most 30 minutes).

Usage: python3 tools/probe_seat_plugins.py [--cwd /home/user/workspace] [--floor-mb 3000] [--log FILE]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

GENERIC = {"npx", "node", "uvx", "uv", "python", "python3", "bun", "bunx", "deno", "docker", "run", "-y", "--yes",
           "--stdio", "stdio", "--from", "exec", "start", "server", "mcp"}


def available_mb() -> int | None:
    for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) // 1024
    return None


def wait_for_memory(floor: int, say) -> None:
    waited = 0
    while True:
        free = available_mb()
        if free is None or free >= floor:
            say(f"MemAvailable {free} MB (floor {floor} MB): launching")
            return
        if waited >= 30 * 60:
            raise SystemExit(f"MemAvailable stayed under {floor} MB for 30 minutes; probe stopped")
        say(f"MemAvailable {free} MB is under {floor} MB: waiting 60 s")
        time.sleep(60)
        waited += 60


def servers_of(entry: dict) -> dict:
    """The MCP server definitions a plugin brings: inline in the listing, `.mcp.json`, or plugin.json."""
    found: dict = {}
    if isinstance(entry.get("mcpServers"), dict):
        found.update(entry["mcpServers"])
    root = Path(entry.get("installPath") or "/nonexistent")
    for path, key in ((root / ".mcp.json", None), (root / ".claude-plugin" / "plugin.json", "mcpServers")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if key:
            data = data.get(key) if isinstance(data, dict) else None
        if isinstance(data, dict):
            found.update(data.get("mcpServers") if isinstance(data.get("mcpServers"), dict) else data)
    return {name: spec for name, spec in found.items() if isinstance(spec, dict)}


def enabled_mcp_plugins(cwd: str, roots: dict[str, str]) -> dict[str, dict]:
    listing = json.loads(subprocess.run(["claude", "plugin", "list", "--json"], cwd=cwd, capture_output=True,
                                        text=True, check=True, timeout=60).stdout)
    plugins: dict[str, dict] = {}
    for entry in listing:
        applies = entry.get("scope") in ("user", "synced") or entry.get("projectPath") == cwd
        if not (applies and entry.get("enabled")) or entry["id"].split("@")[0] == "flotilla":
            continue
        servers = servers_of(entry)
        if servers:
            plugins.setdefault(entry["id"], {}).update(servers)
            roots.setdefault(entry["id"], str(entry.get("installPath") or ""))
    return plugins


def tokens(plugins: dict[str, dict], roots: dict[str, str]) -> set[str]:
    """Words that name a server's process: the non-generic command and argument words of every definition, with
    `${CLAUDE_PLUGIN_ROOT}` read as the plugin's install path."""
    words: set[str] = set()
    for plugin, servers in plugins.items():
        for spec in servers.values():
            raw = [os.path.basename(str(spec.get("command", "")))] + [str(a) for a in spec.get("args", [])]
            for word in raw:
                word = re.sub(r"@[\d.]+$", "", word.replace("${CLAUDE_PLUGIN_ROOT}", roots.get(plugin, "")))
                if word and not word.startswith("-") and word.lower() not in GENERIC and "://" not in word:
                    words.add(word)
    return words


def processes() -> dict[int, tuple[int, str, int]]:
    """pid -> (ppid, command line, RSS in kB) for every readable process."""
    table = {}
    page_kb = os.sysconf("SC_PAGE_SIZE") // 1024
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            stat = (proc / "stat").read_text()
            fields = stat[stat.rindex(")") + 2:].split()
            cmd = (proc / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace").strip()
            rss = int((proc / "statm").read_text().split()[1]) * page_kb
        except (OSError, ValueError):
            continue
        table[int(proc.name)] = (int(fields[1]), cmd, rss)
    return table


def descendants(root: int) -> dict[int, tuple[str, int, int]]:
    """pid -> (command line, RSS in kB, the direct child of `root` whose subtree holds it)."""
    table = processes()
    children: dict[int, list[int]] = {}
    for pid, (ppid, _, _) in table.items():
        children.setdefault(ppid, []).append(pid)
    out, todo = {}, [(child, child) for child in children.get(root, [])]
    while todo:
        pid, top = todo.pop()
        out[pid] = (table[pid][1], table[pid][2], top)
        todo += [(child, top) for child in children.get(pid, [])]
    return out


def split(tree: dict[int, tuple[str, int, int]], words: set[str]) -> tuple[set[int], set[int]]:
    """Plugin MCP processes (every process under a direct child whose command line names a plugin server) and
    the other descendants (MCP servers from the person's own configuration, hooks)."""
    plugin_tops = {top for pid, (cmd, _, top) in tree.items() if pid == top and any(w in cmd for w in words)}
    plugin = {pid for pid, (_, _, top) in tree.items() if top in plugin_tops}
    return plugin, set(tree) - plugin


def census_pid(name: str) -> tuple[int | None, str | None]:
    done = subprocess.run(["claude", "agents", "--json"], capture_output=True, text=True, timeout=30, check=False)
    for row in json.loads(done.stdout or "[]"):
        if row.get("name") == name:
            return row.get("pid"), row.get("id")
    return None, None


def measure(variant: str, extra: list[str], cwd: str, add_dir: str, words: set[str], say) -> tuple:
    name = f"probe seat plugins {variant} {os.getpid()}"
    launched = subprocess.run(["claude", "--bg", "-n", name, "--add-dir", add_dir, *extra,
                               "Reply with the single word ok and nothing else."],
                              cwd=cwd, capture_output=True, text=True, timeout=180, check=False)
    say(f"[{variant}] launched rc={launched.returncode}: {(launched.stdout + launched.stderr).strip()[-200:]}")
    pid = short = None
    for _ in range(30):
        pid, short = census_pid(name)
        if pid:
            break
        time.sleep(2)
    if not pid:
        raise SystemExit(f"[{variant}] the census never listed {name!r}; stop it by hand if it appears")
    try:
        best: dict[int, tuple[str, int, int]] = {}
        stable, last = 0, None
        for tick in range(45):   # up to ~90 s: servers start at session start; wait until the set holds for 10 s
            time.sleep(2)
            tree = descendants(pid)
            if len(tree) > len(best):
                best = tree
            key = frozenset(tree)
            stable = stable + 1 if key == last else 0
            last = key
            if stable >= 5 and tick >= 10:
                break
        plugin, other = split(best, words)
        say(f"[{variant}] descendants of pid {pid}:")
        for p, (cmd, rss, _) in sorted(best.items()):
            say(f"    {p:>8} {rss // 1024:>6} MB {'plugin' if p in plugin else 'other '} {cmd[:130]}")
        rss_of = lambda pids: sum(best[p][1] for p in pids) // 1024
        return len(plugin), rss_of(plugin), len(other), rss_of(other)
    finally:
        stopped = subprocess.run(["claude", "stop", short or name], capture_output=True, text=True, timeout=60,
                                 check=False)
        say(f"[{variant}] stopped {short}: rc={stopped.returncode} {(stopped.stdout + stopped.stderr).strip()[:160]}")
        removed = subprocess.run(["claude", "rm", short or name], capture_output=True, text=True, timeout=60,
                                 check=False)
        say(f"[{variant}] removed {short}: rc={removed.returncode} {(removed.stdout + removed.stderr).strip()[:160]}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cwd", default="/home/user/workspace")
    parser.add_argument("--floor-mb", type=int, default=3000)
    parser.add_argument("--log", default="")
    parser.add_argument("--variants", default="abc")
    args = parser.parse_args()
    log = open(args.log, "a", encoding="utf-8") if args.log else None

    def say(text: str) -> None:
        print(text, flush=True)
        if log:
            log.write(text + "\n")
            log.flush()

    roots: dict[str, str] = {}
    plugins = enabled_mcp_plugins(args.cwd, roots)
    words = tokens(plugins, roots)
    say(f"enabled plugins with MCP servers at {args.cwd}: {', '.join(sorted(plugins))}")
    say(f"process words: {', '.join(sorted(words))}")
    settings = json.dumps({"enabledPlugins": {plugin: False for plugin in sorted(plugins)}})
    variants = {"a": [], "b": ["--settings", settings], "c": ["--strict-mcp-config"]}
    rows = []
    with tempfile.TemporaryDirectory(prefix="probe-seat-") as add_dir:
        for variant in args.variants:
            wait_for_memory(args.floor_mb, say)
            rows.append((variant, *measure(variant, variants[variant], args.cwd, add_dir, words, say)))
    say("\n| variant | plugin MCP processes | their RSS (MB) | other descendants | their RSS (MB) |")
    say("|---|---|---|---|---|")
    for variant, count, rss, others, other_rss in rows:
        say(f"| {variant} | {count} | {rss} | {others} | {other_rss} |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
