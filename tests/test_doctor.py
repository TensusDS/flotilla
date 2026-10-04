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


def test_timeout_reaches_both_external_calls(tmp_path):
    seen = []
    def run(argv, **kwargs):
        seen.append((argv[1], kwargs.get("timeout")))
        out = "2.1.280 (Claude Code)" if argv[1] == "--version" else "[]"
        return subprocess.CompletedProcess(argv, 0, stdout=out, stderr="")
    doctor.collect(cwd=tmp_path, env={"FLOTILLA_STATE_DIR": str(tmp_path / "s")}, run=run,
                   which=which_all, os_name="linux", python=(3, 12, 1), timeout=3)
    assert seen == [("--version", 3), ("agents", 3)]


def test_unexecutable_claude_fails_instead_of_crashing(tmp_path):
    def run(argv, **kwargs):
        raise PermissionError(13, "Permission denied", argv[0])
    found = collect(tmp_path, run=run)
    assert found["claude"].status == "fail"


def plugin_run(enabled):
    import json as _json
    def run(argv, **kwargs):
        if argv[0] == "git":   # the main checkout is asked of git itself
            return subprocess.run(argv, **kwargs)
        if argv[:3] == ["claude", "plugin", "list"]:
            return subprocess.CompletedProcess(argv, 0, _json.dumps([{"id": "flotilla@flotilla", "enabled": enabled}]),
                                               "")
        return subprocess.CompletedProcess(argv, 0, "2.1.280 (Claude Code)\n", "")
    return run


def onboard_here(tmp_path):
    (tmp_path / ".flotilla").mkdir()
    (tmp_path / ".flotilla" / "project.toml").write_text("schema = 1\n", encoding="utf-8")


def test_doctor_names_a_plugin_not_enabled_here(tmp_path):
    onboard_here(tmp_path)
    found = collect(tmp_path, run=plugin_run(False), home=tmp_path)
    assert found["plugin"].status == "fail" and "claude plugin install flotilla@flotilla" in found["plugin"].fix
    assert found["trust"].status == "warn"


def test_doctor_is_green_when_enabled_and_trusted(tmp_path):
    import json as _json
    onboard_here(tmp_path)
    (tmp_path / ".claude.json").write_text(_json.dumps({"projects": {str(tmp_path.resolve()): {
        "hasTrustDialogAccepted": True}}}), encoding="utf-8")
    found = collect(tmp_path, run=plugin_run(True), home=tmp_path)
    assert found["plugin"].status == "ok" and found["trust"].status == "ok"


def test_a_hook_skips_the_setup_checks(tmp_path):
    onboard_here(tmp_path)
    found = collect(tmp_path, run=plugin_run(False), home=tmp_path, setup=False)
    assert "plugin" not in found and "trust" not in found


def test_doctor_in_a_fleet_worktree_checks_the_main_checkout(tmp_path):
    import json as _json
    from ledgerkit import git, repo_with_origin
    root = repo_with_origin(tmp_path)
    onboard_here(root)
    tree = tmp_path / "app-main-1"
    git(root, "worktree", "add", "-q", "-b", "fleet/main-1", str(tree))
    onboard_here(tree)
    (tmp_path / ".claude.json").write_text(_json.dumps({"projects": {str(root.resolve()): {
        "hasTrustDialogAccepted": True}}}), encoding="utf-8")
    found = collect(tree, run=plugin_run(True), home=tmp_path)
    assert found["trust"].status == "ok" and str(root.resolve()) in found["trust"].detail


def shapes_collect(tmp_path, census_rows, registry_entries=None, claude_json=None, setup=True):
    import json as _json
    config = tmp_path / "config"
    (config / "sessions").mkdir(parents=True)
    for pid, entry in (registry_entries or {}).items():
        (config / "sessions" / f"{pid}.json").write_text(_json.dumps(entry), encoding="utf-8")
    if claude_json is not None:
        (tmp_path / ".claude.json").write_text(_json.dumps(claude_json), encoding="utf-8")
    env = {"FLOTILLA_STATE_DIR": str(tmp_path / "state"), "CLAUDE_CONFIG_DIR": str(config)}
    return collect(tmp_path, env=env, home=tmp_path, read_rows=lambda: census_rows, setup=setup)


LIVE = [{"sessionId": "s1", "name": "session 1", "kind": "interactive", "cwd": "/p", "pid": 1001, "status": "busy"},
        {"sessionId": "s2", "id": "a0000002", "name": "session 2", "kind": "background", "cwd": "/p", "pid": 1002,
         "status": "idle", "state": "working"}]


def test_doctor_checks_claude_codes_records_against_their_measured_shape(tmp_path):
    found = shapes_collect(tmp_path, LIVE, {1001: {"sessionId": "s1", "entrypoint": "cli"}}, {"projects": {}})
    assert found["census-shape"].status == "ok" and "2.1.289" in found["census-shape"].detail
    assert found["registry"].status == "ok" and found["trust-record"].status == "ok"


def test_doctor_warns_and_names_what_stops_when_a_record_drifts(tmp_path):
    drifted = [{**row, "state": "paused"} if row["kind"] == "background" else row for row in LIVE]
    found = shapes_collect(tmp_path, drifted, {}, {"workspaces": {}})
    assert found["census-shape"].status == "warn" and "`state`" in found["census-shape"].detail
    assert found["registry"].status == "warn" and "stranger" in found["registry"].detail
    assert found["trust-record"].status == "warn" and "spawn" in found["trust-record"].detail
    assert all(found[check].fix for check in ("census-shape", "registry", "trust-record"))


def test_a_hook_skips_the_record_checks(tmp_path):
    found = shapes_collect(tmp_path, LIVE, setup=False)
    assert not {"census-shape", "registry", "trust-record"} & set(found)


def test_a_census_that_cannot_be_asked_is_not_measured(tmp_path):
    def down():
        raise CensusUnavailable("`claude` is not on PATH")
    found = collect(tmp_path, read_rows=down, home=tmp_path)
    assert found["census-shape"].status == "info" and "not on PATH" in found["census-shape"].detail


def counting_run(answer="[]", fail=False):
    seen = []
    def run(argv, **kwargs):
        seen.append(argv[1])
        if argv[1] == "agents" and fail:
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr="daemon down")
        out = "2.1.289 (Claude Code)" if argv[1] == "--version" else answer
        return subprocess.CompletedProcess(argv, 0, stdout=out, stderr="")
    return run, seen


def test_one_census_call_answers_the_count_and_the_shape(tmp_path):
    import json as _json
    run, seen = counting_run(_json.dumps(LIVE))
    found = collect(tmp_path, run=run, home=tmp_path, read=None)
    assert seen.count("agents") == 1
    assert found["census"].status == "ok" and found["census-shape"].status == "ok"
    assert "2 listed" in found["census-shape"].detail


def test_a_census_that_fails_once_fails_both_lines_without_a_second_call(tmp_path):
    import json as _json
    (tmp_path / ".claude.json").write_text(_json.dumps({"projects": {}}), encoding="utf-8")
    run, seen = counting_run(fail=True)
    found = collect(tmp_path, run=run, home=tmp_path, read=None)
    assert seen.count("agents") == 1
    assert found["census"].status == "fail" and found["census-shape"].status == "info"
    assert found["trust-record"].status == "ok"   # the trust record does not depend on the census


def test_an_empty_census_is_not_called_measured(tmp_path):
    found = collect(tmp_path, read_rows=lambda: [], home=tmp_path)
    assert found["census-shape"].status == "info"


def posts_here(tmp_path, *, behind=()):
    from flotilla.posts import TEMPLATE_DIR
    folder = tmp_path / ".flotilla" / "posts"
    folder.mkdir(parents=True, exist_ok=True)
    for template in TEMPLATE_DIR.glob("*.md"):
        text = template.read_text(encoding="utf-8")
        if template.stem in behind:
            import re
            text = re.sub(r"(?m)^template_version: (\d+)$", lambda m: f"template_version: {int(m.group(1)) - 1}", text)
        (folder / template.name).write_text(text, encoding="utf-8")


def test_doctor_names_a_post_older_than_the_shipped_template(tmp_path):
    """Post files are copied at onboarding and never overwritten, and nothing said when one fell behind (review of
    0.7.12): an orchestrator copy without the v13 refusal rule ran on in a project that updated the plugin."""
    from flotilla.posts import TEMPLATE_DIR, load_post
    onboard_here(tmp_path)
    posts_here(tmp_path, behind=("orchestrator",))
    shipped = load_post(TEMPLATE_DIR / "orchestrator.md").template_version
    found = collect(tmp_path, run=plugin_run(True), home=tmp_path)
    assert found["posts"].status == "warn"
    assert f"orchestrator (v{shipped - 1}, shipped v{shipped})" in found["posts"].detail
    assert "flotilla onboard publish" in found["posts"].fix


def test_doctor_is_quiet_about_current_and_custom_posts(tmp_path):
    onboard_here(tmp_path)
    posts_here(tmp_path)
    (tmp_path / ".flotilla" / "posts" / "release-captain.md").write_text(
        "---\nname: release-captain\nname_pattern: \"release captain {n}\"\nmay: []\ntemplate_version: 1\n---\nbody\n",
        encoding="utf-8")
    found = collect(tmp_path, run=plugin_run(True), home=tmp_path)
    assert found["posts"].status == "ok"
