import io
import subprocess

from flotilla import doctor
from flotilla.core.census import CensusUnavailable


def fake_run(version_line="2.1.280 (Claude Code)", returncode=0, missing=False):
    def run(argv, **kwargs):
        if missing:
            raise FileNotFoundError(argv[0])
        return subprocess.CompletedProcess(argv, returncode, stdout=version_line + "\n", stderr="")
    return run


def which_all(name):
    return "/usr/bin/" + name


def collect(tmp_path, **overrides):
    options = dict(cwd=tmp_path, env={"FLOTILLA_STATE_DIR": str(tmp_path / "state")},
                   run=fake_run(), which=which_all, read=lambda: [], os_name="linux", python=(3, 12, 1))
    options.update(overrides)
    return {f.check: f for f in doctor.collect(**options)}


def test_parse_version():
    assert doctor.parse_version("2.1.280 (Claude Code)") == (2, 1, 280)
    assert doctor.parse_version("unknown") is None


def test_healthy_machine_outside_a_project(tmp_path):
    found = collect(tmp_path)
    assert all(f.status in ("ok", "info") for f in found.values()), found
    assert found["project"].status == "info" and "not onboarded" in found["project"].detail


def test_old_python_fails_with_a_fix(tmp_path):
    found = collect(tmp_path, python=(3, 10, 12))
    assert found["python"].status == "fail" and "3.11" in found["python"].fix


def test_windows_fails(tmp_path):
    assert collect(tmp_path, os_name="win32")["platform"].status == "fail"


def test_claude_below_the_floor_fails(tmp_path):
    found = collect(tmp_path, run=fake_run("2.1.200 (Claude Code)"))
    assert found["claude"].status == "fail" and "2.1.280" in found["claude"].detail


def test_claude_missing_fails(tmp_path):
    assert collect(tmp_path, run=fake_run(missing=True))["claude"].status == "fail"


def test_unparseable_claude_version_is_a_warning_not_ok(tmp_path):
    assert collect(tmp_path, run=fake_run("weird"))["claude"].status == "warn"


def test_census_failure_is_fail_not_zero(tmp_path):
    def broken():
        raise CensusUnavailable("`claude` is not on PATH")
    found = collect(tmp_path, read=broken)
    assert found["census"].status == "fail"
    assert "0" not in found["census"].detail and "not on PATH" in found["census"].detail


def test_census_counts_live_sessions(tmp_path):
    assert "2 live" in collect(tmp_path, read=lambda: [object(), object()])["census"].detail


def test_broken_project_config_fails_with_the_file(tmp_path):
    (tmp_path / ".flotilla").mkdir()
    (tmp_path / ".flotilla" / "project.toml").write_text("schema = \n", encoding="utf-8")
    found = collect(tmp_path)
    assert found["project"].status == "fail" and "project.toml" in found["project"].detail


def test_state_dir_is_reported(tmp_path):
    found = collect(tmp_path)
    assert str(tmp_path / "state") in found["state"].detail


def test_quiet_render_keeps_only_what_needs_attention(tmp_path):
    findings = [doctor.Finding("ok", "a", "fine"), doctor.Finding("warn", "b", "hmm", "do x")]
    assert doctor.render(findings, quiet=True) == ["warn  b: hmm (fix: do x)"]
    assert len(doctor.render(findings, quiet=False)) == 2


def test_run_doctor_exit_code(tmp_path, monkeypatch):
    monkeypatch.setattr(doctor, "collect", lambda **kw: [doctor.Finding("fail", "x", "bad")])
    out = io.StringIO()
    assert doctor.run_doctor(cwd=tmp_path, out=out) == 1
    assert "fail  x: bad" in out.getvalue()
