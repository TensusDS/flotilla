"""A fake `claude` for fleet tests: launches and stops are recorded, the census is a list, git runs for real."""

import subprocess

from flotilla.core.census import CensusUnavailable, Session


def session(name, short_id, state="blocked"):
    return Session(name=name, session_id=f"sid-{short_id}", kind="background", pid=None, short_id=short_id,
                   status=None, state=state, cwd="", started_at_ms=None)


class FakeClaude:
    def __init__(self, sessions=(), *, appear=True, fail_launch=False, reachable=True):
        self.sessions = list(sessions)
        self.launched: list[list[str]] = []
        self.stopped: list[str] = []
        self.appear = appear
        self.fail_launch = fail_launch
        self.reachable = reachable
        self.stops = True

    def census(self):
        if not self.reachable:
            raise CensusUnavailable("`claude agents --json` did not answer within 30s")
        return list(self.sessions)

    def __call__(self, cmd, **kwargs):
        if not (isinstance(cmd, list) and cmd and cmd[0] == "claude"):
            return subprocess.run(cmd, **kwargs)
        if cmd[1] == "--bg":
            self.launched.append(list(cmd))
            if self.fail_launch:
                return subprocess.CompletedProcess(cmd, 1, "", "error: not logged in")
            if self.appear:
                name = cmd[cmd.index("-n") + 1]
                self.sessions.append(session(name, f"{len(self.launched):06x}"))
            return subprocess.CompletedProcess(cmd, 0, "backgrounded", "")
        if cmd[1] in ("stop", "kill"):
            self.stopped.append(cmd[2])
            if self.stops:
                self.sessions = [s for s in self.sessions if s.short_id != cmd[2]]
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return subprocess.CompletedProcess(cmd, 1, "", f"fake claude: {cmd[1:]}")
