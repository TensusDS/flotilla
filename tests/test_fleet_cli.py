import io
from contextlib import redirect_stdout

from flotilla import cli
from flotilla.onboard.tomlw import render_toml
from flotilla.posts import install_templates
from ledgerkit import commit, git, repo_with_origin

PROFILE = {"schema": 1, "trunk": {"branch": "main"}, "flow": {"mode": "pr"}, "review": {"depth": "every"},
           "permissions": {"mode": "auto"}, "fleet": {"default": {"main": 1, "review": 1}, "model": "one"}}


def run_cli(*args):
    out = io.StringIO()
    with redirect_stdout(out):
        code = cli.main(list(args))
    return code, out.getvalue()


def onboarded(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOTILLA_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("FLOTILLA_NO_CENSUS", "1")
    root = repo_with_origin(tmp_path)
    (root / ".flotilla").mkdir()
    (root / ".flotilla" / "project.toml").write_text(render_toml(PROFILE), encoding="utf-8")
    install_templates(root)
    git(root, "add", ".flotilla")
    commit(root, "onboard")
    git(root, "push", "-q", "origin", "main")
    return root


def test_dry_run_without_the_census_warns(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch)
    code, out = run_cli("spawn", "--default", "--dry-run", "--root", str(root))
    assert code == 0
    assert "census: unknown" in out and "names may collide" in out
    assert "review session 1" in out and "main session 1" in out
    assert "--permission-mode auto" in out and "fleet/reviewer-1" in out
    assert not (tmp_path / "app-reviewer-1").exists()


def test_spawn_needs_a_composition_and_refuses_both_kinds_at_once(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch)
    code, out = run_cli("spawn", "--dry-run", "--root", str(root))
    assert code == 2 and "name a composition" in out
    code, out = run_cli("spawn", "--default", "-r", "1", "--dry-run", "--root", str(root))
    assert code == 2 and "either --default or counts" in out


def test_spawn_without_the_census_refuses_to_launch(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch)
    code, out = run_cli("spawn", "-M", "1", "--root", str(root))
    assert code == 2 and "census" in out


def test_the_fleet_is_listed_even_when_the_census_is_unknown(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch)
    code, out = run_cli("fleet", "--root", str(root))
    assert code == 0 and "no post rows" in out


def test_retire_of_an_unknown_name_is_refused(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch)
    code, out = run_cli("retire", "main session 7", "--root", str(root))
    assert code == 2 and "no post row for `main session 7`" in out


def test_a_custom_count_is_given_by_post_name(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch)
    code, out = run_cli("spawn", "--post", "minor=2", "--dry-run", "--root", str(root))
    assert code == 0 and "minor session 1" in out and "minor session 2" in out
    code, out = run_cli("spawn", "--post", "minor", "--dry-run", "--root", str(root))
    assert code == 2 and "NAME=N" in out


def test_fleet_down_without_the_census_refuses_and_stops_nothing(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch)
    code, out = run_cli("fleet", "down", "--root", str(root))
    assert code == 2 and "nothing was stopped or released" in out


def test_fleet_down_inside_an_unidentified_claude_session_refuses(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch)
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setattr("flotilla.fleet.commands.census", lambda: [])
    code, out = run_cli("fleet", "down", "--root", str(root))
    assert code == 2 and "cannot tell which session runs this" in out


def test_a_dry_run_says_where_the_numbering_continues_from(tmp_path, monkeypatch):
    from flotilla.core import claude_state
    from fleetkit import session
    root = onboarded(tmp_path, monkeypatch)
    monkeypatch.setattr("flotilla.fleet.commands.census", lambda: [session("review session 37", "abc123")])
    monkeypatch.setattr(claude_state, "setup_problems", lambda main, **kw: ([], []))
    code, out = run_cli("spawn", "--dry-run", "--post", "reviewer=1", "--root", str(root))
    assert code == 0 and "review session 38" in out
    assert "note: reviewer numbering continues after review session 37 (alive on this machine" in out
