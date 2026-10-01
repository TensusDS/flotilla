"""A fake `claude` for fleet tests: launches and stops are recorded, the census is a list, git runs for real."""

import json
import subprocess

from flotilla.core.census import CensusUnavailable, Session


FLOTILLA_ENABLED = {"id": "flotilla@flotilla", "scope": "project", "enabled": True}


def session(name, short_id, state="blocked", cwd=""):
    return Session(name=name, session_id=f"sid-{short_id}", kind="background", pid=None, short_id=short_id,
                   status=None, state=state, cwd=str(cwd), started_at_ms=None)


class FakeClaude:
    def __init__(self, sessions=(), *, appear=True, fail_launch=False, reachable=True, fail_on=(),
                 appear_then_fail=False, timeout=False, plugin_entries=None, plugin_list_fails=False):
        # what `claude plugin list --json` answers: by default flotilla enabled and nothing that brings MCP servers
        self.plugin_entries = list(plugin_entries if plugin_entries is not None else [FLOTILLA_ENABLED])
        self.plugin_list_fails = plugin_list_fails
        self.plugin_lists: list[str] = []             # the directories it was asked in
        self.fail_on = set(fail_on)
        self.appear_then_fail = appear_then_fail
        self.timeout = timeout
        self.sessions = list(sessions)
        self.launched: list[list[str]] = []
        self.stopped: list[str] = []
        self.appear = appear
        self.fail_launch = fail_launch
        self.reachable = reachable
        self.stops = True
        self.stop_fails: set[str] = set()   # session names whose `claude stop` exits 1

    def census(self):
        if not self.reachable:
            raise CensusUnavailable("`claude agents --json` did not answer within 30s")
        return list(self.sessions)

    def __call__(self, cmd, **kwargs):
        if not (isinstance(cmd, list) and cmd and cmd[0] == "claude"):
            return subprocess.run(cmd, **kwargs)
        if cmd[1] == "--bg":
            self.launched.append(list(cmd))
            name = cmd[cmd.index("-n") + 1]
            if self.appear_then_fail or self.timeout:
                self.sessions.append(session(name, f"{len(self.launched):06x}"))
            if self.timeout:
                raise subprocess.TimeoutExpired(cmd, 180)
            if self.fail_launch or self.appear_then_fail or name in self.fail_on:
                return subprocess.CompletedProcess(cmd, 1, "", "error: not logged in")
            if self.appear:
                self.sessions.append(session(name, f"{len(self.launched):06x}"))
            return subprocess.CompletedProcess(cmd, 0, "backgrounded", "")
        if cmd[1:4] == ["plugin", "list", "--json"]:
            self.plugin_lists.append(kwargs.get("cwd"))
            if self.plugin_list_fails:
                raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout", 30))
            return subprocess.CompletedProcess(cmd, 0, json.dumps(self.plugin_entries), "")
        if cmd[1] in ("stop", "kill"):
            if any(s.short_id == cmd[2] and s.name in self.stop_fails for s in self.sessions):
                return subprocess.CompletedProcess(cmd, 1, "", "fake claude: stop failed")
            self.stopped.append(cmd[2])
            if self.stops:
                self.sessions = [s for s in self.sessions if s.short_id != cmd[2]]
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return subprocess.CompletedProcess(cmd, 1, "", f"fake claude: {cmd[1:]}")
