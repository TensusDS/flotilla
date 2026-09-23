import io
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENTRY = ROOT / "scripts" / "flotilla"


def hook(cwd, env_state):
    # Run the entry with THIS interpreter. Through the shebang with a bare PATH, macOS finds its
    # system python3 (3.9.6 on GitHub's macos-latest, 2026-09-23), and the test would measure the
    # interpreter gate instead of the hook.
    return subprocess.run([sys.executable, str(ENTRY), "hook", "session-start"],
                          input=json.dumps({"cwd": str(cwd)}), capture_output=True, text=True,
                          env={"PATH": "/usr/bin:/bin", "FLOTILLA_STATE_DIR": str(env_state)})


def test_inactive_project_is_silent(tmp_path):
    done = hook(tmp_path, tmp_path / "state")
    assert done.returncode == 0
    assert done.stdout == "" and done.stderr == ""


def test_inactive_path_imports_nothing_heavy(tmp_path):
    probe = (
        "import io, json, sys\n"
        f"sys.path.insert(0, {str(ROOT)!r})\n"
        "from flotilla.hooks import run_hook\n"
        f"run_hook('session-start', io.StringIO(json.dumps({{'cwd': {str(tmp_path)!r}}})))\n"
        "heavy = [m for m in ('flotilla.doctor', 'flotilla.core.census', 'flotilla.core.storage') if m in sys.modules]\n"
        "print(','.join(heavy))\n"
    )
    done = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, check=True)
    assert done.stdout.strip() == ""


def test_garbage_on_stdin_is_treated_as_inactive_elsewhere(tmp_path):
    from flotilla.hooks import run_hook
    out = io.StringIO()
    assert run_hook("session-start", io.StringIO("not json"), out=out) == 0


def test_session_start_reports_broken_config(tmp_path):
    (tmp_path / ".flotilla").mkdir()
    (tmp_path / ".flotilla" / "project.toml").write_text("schema = \n", encoding="utf-8")
    done = hook(tmp_path, tmp_path / "state")
    assert done.returncode == 0
    assert "project.toml" in done.stdout and done.stdout.startswith("flotilla:")


def test_session_start_in_a_healthy_project_names_what_needs_attention_only(tmp_path, monkeypatch):
    from flotilla import doctor, hooks
    (tmp_path / ".flotilla").mkdir()
    (tmp_path / ".flotilla" / "project.toml").write_text("schema = 1\n", encoding="utf-8")
    monkeypatch.setattr(doctor, "collect", lambda **kw: [doctor.Finding("ok", "python", "3.12")])
    out = io.StringIO()
    assert hooks.run_hook("session-start", io.StringIO(json.dumps({"cwd": str(tmp_path)})), out=out) == 0
    assert out.getvalue() == ""


def test_an_internal_error_is_said_not_raised(tmp_path, monkeypatch):
    from flotilla import doctor, hooks
    (tmp_path / ".flotilla").mkdir()
    (tmp_path / ".flotilla" / "project.toml").write_text("schema = 1\n", encoding="utf-8")
    def boom(**kw):
        raise RuntimeError("disk on fire")
    monkeypatch.setattr(doctor, "collect", boom)
    out = io.StringIO()
    assert hooks.run_hook("session-start", io.StringIO(json.dumps({"cwd": str(tmp_path)})), out=out) == 0
    assert "could not check this project" in out.getvalue() and "disk on fire" in out.getvalue()


def test_hook_checks_fit_inside_the_declared_timeout(tmp_path, monkeypatch):
    from flotilla import doctor, hooks
    declared = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]["SessionStart"][0]["hooks"][0]["timeout"]
    (tmp_path / ".flotilla").mkdir()
    (tmp_path / ".flotilla" / "project.toml").write_text("schema = 1\n", encoding="utf-8")
    seen = {}
    def spy(**kw):
        seen.update(kw)
        return []
    monkeypatch.setattr(doctor, "collect", spy)
    hooks.run_hook("session-start", io.StringIO(json.dumps({"cwd": str(tmp_path)})), out=io.StringIO())
    # Two external calls (claude --version, the census) plus a margin must finish before the kill.
    assert "timeout" in seen and seen["timeout"] * 2 + 2 <= declared
