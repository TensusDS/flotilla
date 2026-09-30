import dataclasses
import io
from contextlib import redirect_stdout

from flotilla import cli
from flotilla.watch import fired
from ledgerkit import commit, git, repo_with_origin
from watchkit import sess


def onboarded(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOTILLA_STATE_DIR", str(tmp_path / "state"))
    root = repo_with_origin(tmp_path)
    (root / ".flotilla").mkdir()
    (root / ".flotilla" / "project.toml").write_text("schema = 1\n", encoding="utf-8")
    git(root, "add", ".flotilla")
    commit(root, "onboard")
    return root


def status(root):
    out = io.StringIO()
    with redirect_stdout(out):
        code = cli.main(["guard", "status", "--root", str(root)])
    return code, out.getvalue()


def test_guard_status_shows_when_each_hook_last_ran_per_session(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch)
    here = dataclasses.replace(sess("main session 1", sid="sid-here"), cwd=str(root))
    there = dataclasses.replace(sess("minor session 9", sid="sid-there"), cwd=str(tmp_path / "elsewhere"))
    monkeypatch.setattr("flotilla.guards.commands.census", lambda: [here, there])
    fired.record(tmp_path / "state", "sid-here", "session-start", root=root, at="2026-09-29T10:00:00+00:00")
    fired.record(tmp_path / "state", "sid-here", "guard", root=root, at="2026-09-29T10:05:00+00:00")
    code, out = status(root)
    assert code == 0
    line = next(line for line in out.splitlines() if line.startswith("session main session 1:"))
    assert "session-start 2026-09-29T10:00:00+00:00" in line and "guard 2026-09-29T10:05:00+00:00" in line
    assert "stop never" in line and "minor session 9" not in out


def test_guard_status_says_when_the_census_cannot_be_asked(tmp_path, monkeypatch):
    from flotilla.core.census import CensusUnavailable
    root = onboarded(tmp_path, monkeypatch)
    def down():
        raise CensusUnavailable("`claude` is not on PATH")
    monkeypatch.setattr("flotilla.guards.commands.census", down)
    code, out = status(root)
    assert code == 0 and "hooks: the census could not be asked" in out


def test_guard_status_names_the_lane_guard_on_by_default(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch)
    monkeypatch.setattr("flotilla.guards.commands.census", lambda: [])
    code, out = status(root)
    assert code == 0 and "guard lane: on (warns; on unless the profile says lane = false)" in out
