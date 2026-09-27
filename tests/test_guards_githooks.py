import argparse
import io
import json
import os
import subprocess
import sys
from pathlib import Path

from flotilla import cli, hooks
from flotilla.guards import githooks
from guardkit import IDENTITY, git, onboarded, plain_repo, receipt

ROOT = Path(__file__).resolve().parent.parent


def test_install_writes_both_hooks_into_the_common_git_directory(tmp_path):
    root = plain_repo(tmp_path)
    ok, said = githooks.install(root, ["pre-commit", "pre-push"], state_dir=tmp_path / "state", cli=hooks.CLI)
    assert ok and len(said) == 2
    folder = root / ".git" / "hooks"
    assert githooks.MARK in (folder / "pre-push").read_text() and os.access(folder / "pre-push", os.X_OK)
    assert dict(githooks.status(root)) == {"pre-commit": "installed", "pre-push": "installed"}
    assert githooks.link_path(tmp_path / "state").resolve() == hooks.CLI.resolve()


def test_another_tools_hook_is_never_replaced(tmp_path):
    root = plain_repo(tmp_path)
    (root / ".git" / "hooks" / "pre-commit").write_text("#!/bin/sh\nrun-linters\n", encoding="utf-8")
    ok, said = githooks.install(root, ["pre-commit"], state_dir=tmp_path / "state", cli=hooks.CLI)
    assert not ok and "another tool's hook" in said[0] and "guard githook pre-commit" in said[0]
    assert (root / ".git" / "hooks" / "pre-commit").read_text() == "#!/bin/sh\nrun-linters\n"
    assert dict(githooks.status(root))["pre-commit"] == "another tool's hook"


def test_a_hooks_path_is_left_to_its_manager(tmp_path):
    root = plain_repo(tmp_path)
    git(root, "config", "core.hooksPath", ".husky")
    ok, said = githooks.install(root, ["pre-push"], state_dir=tmp_path / "state", cli=hooks.CLI)
    assert not ok and "core.hooksPath" in said[0]


def test_the_profile_names_the_hooks_it_asks_for():
    assert githooks.wanted({"guards": {"push_receipt": True}, "reservation": {"files": ["TODO.md"]}}) == \
        ["pre-commit", "pre-push"]
    assert githooks.wanted({"guards": {"revert": True}}) == []


def test_session_start_points_the_link_at_the_running_plugin(tmp_path, monkeypatch):
    from watchkit import context, onboarded as onboarded_dir, sess
    monkeypatch.setenv("FLOTILLA_STATE_DIR", str(tmp_path / "state"))
    from flotilla import doctor
    monkeypatch.setattr(doctor, "collect", lambda **kw: [])
    onboarded_dir(tmp_path)
    ctx = context(tmp_path, me=sess("main session 1"))
    hooks.run_hook("session-start", io.StringIO(json.dumps({"cwd": str(tmp_path)})), out=io.StringIO(),
                   gather=lambda root, sid: ctx)
    assert githooks.link_path(tmp_path / "state").resolve() == hooks.CLI.resolve()


def test_a_pushed_trunk_without_a_receipt_is_refused_by_git(tmp_path):
    root = onboarded(tmp_path)
    state = tmp_path / "state"
    githooks.install(root, ["pre-push"], state_dir=state, cli=hooks.CLI)
    (root / "g.txt").write_text("x\n", encoding="utf-8")
    git(root, "add", "g.txt")
    git(root, *IDENTITY, "commit", "-q", "-m", "x")
    env = {**os.environ, "FLOTILLA_STATE_DIR": str(state),
           "PATH": f"{Path(sys.executable).parent}{os.pathsep}{os.environ.get('PATH', '')}"}
    env.pop("FLOTILLA_GATE_OVERRIDE", None)
    script = tmp_path / "ship.sh"                     # a push the command line does not show
    script.write_text("#!/bin/sh\ngit push -q origin main\n", encoding="utf-8")
    script.chmod(0o755)
    done = subprocess.run([str(script)], cwd=root, env=env, capture_output=True, text=True)
    assert done.returncode != 0 and "flotilla pre-push" in done.stderr
    receipt(root, state)
    done = subprocess.run([str(script)], cwd=root, env=env, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def test_guard_check_prints_what_the_guards_would_say(tmp_path, capsys):
    root = onboarded(tmp_path)
    code = cli.main(["guard", "check", "sed -i '4d' f.txt", "--cwd", str(root)])
    assert code == 1 and "line_edit" in capsys.readouterr().out


def test_guard_help_names_the_ceiling(capsys):
    try:
        cli.main(["guard", "--help"])
    except SystemExit:
        pass
    assert "eval" in capsys.readouterr().out


def fake_cli(tmp_path, code):
    path = tmp_path / f"cli-{code}"
    path.write_text(f"#!/bin/sh\necho 'fake flotilla exits {code}' >&2\nexit {code}\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def test_a_pre_commit_that_cannot_start_lets_the_commit_through(tmp_path):
    root = plain_repo(tmp_path)
    state = tmp_path / "hookstate"
    githooks.install(root, ["pre-commit"], state_dir=state, cli=fake_cli(tmp_path, 3))
    env = {**os.environ, "FLOTILLA_STATE_DIR": str(state)}
    (root / "g.txt").write_text("x\n", encoding="utf-8")
    git(root, "add", "g.txt")
    done = subprocess.run(["git", *IDENTITY, "commit", "-q", "-m", "x"], cwd=root, env=env, capture_output=True,
                          text=True)
    assert done.returncode == 0 and "could not check" in done.stderr
    githooks.refresh_link(state, fake_cli(tmp_path, 1))
    (root / "h.txt").write_text("y\n", encoding="utf-8")
    git(root, "add", "h.txt")
    done = subprocess.run(["git", *IDENTITY, "commit", "-q", "-m", "y"], cwd=root, env=env, capture_output=True,
                          text=True)
    assert done.returncode != 0
