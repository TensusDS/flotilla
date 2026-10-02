import io
import sys
from contextlib import redirect_stdout

from flotilla import cli
from flotilla.onboard.tomlw import render_toml
from flotilla.posts import install_templates
from ledgerkit import commit, git, repo_with_origin

PROFILE = {"schema": 1, "trunk": {"branch": "main"}, "flow": {"mode": "pr"}, "review": {"depth": "every"},
           "lane": {"run_patterns": [r"\bno-such-runner-in-flotilla-tests\b"]}}


def run_cli(*args):
    out = io.StringIO()
    with redirect_stdout(out):
        code = cli.main(list(args))
    return code, out.getvalue()


def onboarded(tmp_path, monkeypatch, profile=PROFILE):
    monkeypatch.setenv("FLOTILLA_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("FLOTILLA_NO_CENSUS", "1")
    root = repo_with_origin(tmp_path)
    (root / ".flotilla").mkdir()
    (root / ".flotilla" / "project.toml").write_text(render_toml(profile), encoding="utf-8")
    install_templates(root)
    git(root, "add", ".flotilla")
    commit(root, "onboard")
    git(root, "push", "-q", "origin", "main")
    return root


def test_the_lane_is_shown_empty_on_a_quiet_machine(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch)
    code, out = run_cli("lane", "--root", str(root))
    assert code == 0 and "capacity: 1" in out and "held: nobody" in out and "foreign run: ok" in out


def test_a_lane_taken_by_hand_refuses_the_next_taker_until_released(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch)
    code, out = run_cli("lane", "take", "--note", "measuring a race", "--root", str(root), "--as", "main session 1")
    assert code == 0 and "booking b1" in out
    code, out = run_cli("lane", "take", "--root", str(root), "--as", "review session 1")
    assert code == 2 and "held by main session 1 (measuring a race)" in out
    code, out = run_cli("lane", "release", "--root", str(root), "--as", "main session 1")
    assert code == 0 and "released b1" in out
    assert run_cli("lane", "take", "--root", str(root), "--as", "review session 1")[0] == 0


def test_a_run_for_a_branch_records_its_result_on_the_row(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch)
    git(root, "checkout", "-q", "-b", "feat/x")
    assert run_cli("work", "claim", "feat/x", "--root", str(root), "--as", "main session 1")[0] == 0
    code, out = run_cli("lane", "run", "--for", "feat/x", "--tree", str(root), "--as", "main session 1", "--",
                        sys.executable, "-c", "print('===== 3 passed in 0.10s =====')")
    assert code == 0 and "lane run: green - 3 passed in 0.10s" in out
    code, out = run_cli("status", "--root", str(root))
    assert "last run: green: 3 passed in 0.10s" in out
    assert "held: nobody" in run_cli("lane", "--root", str(root))[1]


def test_a_killed_run_exits_non_zero_and_says_so(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch)
    code, out = run_cli("lane", "run", "--tree", str(root), "--as", "main session 1", "--", sys.executable, "-c",
                        "import os, signal; os.kill(os.getpid(), signal.SIGKILL)")
    assert code == 137 and "killed by signal 9 - no verdict" in out


def test_a_run_waits_its_turn_and_is_refused_when_the_wait_runs_out(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch)
    run_cli("lane", "take", "--note", "hands off", "--root", str(root), "--as", "main session 1")
    code, out = run_cli("lane", "run", "--wait", "0", "--tree", str(root), "--as", "review session 1", "--",
                        sys.executable, "-c", "print('never')")
    assert code == 2 and "held by main session 1 (hands off)" in out and "never" not in out


def test_a_run_needs_a_command(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch)
    code, out = run_cli("lane", "run", "--tree", str(root), "--as", "main session 1")
    assert code == 2 and "name the command after --" in out


def test_a_receipt_takes_the_lane(tmp_path, monkeypatch):
    tier = {"name": "unit", "command": f"{sys.executable} -c \"print('1 passed')\"", "required_for": ["handover"]}
    root = onboarded(tmp_path, monkeypatch, {**PROFILE, "tests": {"tier": [tier]}})   # something to run: it queues
    run_cli("lane", "take", "--note", "hands off", "--root", str(root), "--as", "main session 1")
    code, out = run_cli("receipt", "run", "--purpose", "handover", "--tree", str(root), "--lane-wait", "0")
    assert code == 2 and "held by main session 1 (hands off)" in out


def test_sweep_says_when_there_is_nothing_to_sweep(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch)
    code, out = run_cli("lane", "sweep", "--root", str(root))
    assert code == 0 and "nothing to sweep" in out


def test_a_run_is_recorded_over_the_revision_it_started_from(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch)
    assert run_cli("work", "claim", "main", "--root", str(root), "--as", "main session 1")[0] == 0
    before = git(root, "rev-parse", "HEAD")
    script = "import subprocess; subprocess.run(['git', '-c', 'user.email=t@x', '-c', 'user.name=t', 'commit', " \
             "'-q', '--allow-empty', '-m', 'during the run']); print('1 passed')"
    run_cli("lane", "run", "--for", "main", "--tree", str(root), "--as", "main session 1", "--", sys.executable, "-c",
            script)
    code, out = run_cli("work", "show", "main", "--root", str(root))
    assert f"over {before[:7]}" in run_cli("status", "--root", str(root))[1]


def test_a_run_over_uncommitted_work_says_so(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch)
    assert run_cli("work", "claim", "main", "--root", str(root), "--as", "main session 1")[0] == 0
    (root / "draft.txt").write_text("unsaved\n", encoding="utf-8")
    run_cli("lane", "run", "--for", "main", "--tree", str(root), "--as", "main session 1", "--", sys.executable, "-c",
            "print('1 passed')")
    assert "plus uncommitted changes" in run_cli("status", "--root", str(root))[1]


def test_a_run_from_another_branch_is_not_recorded(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch)
    assert run_cli("work", "claim", "feat/x", "--root", str(root), "--as", "main session 1")[0] == 0
    code, out = run_cli("lane", "run", "--for", "feat/x", "--tree", str(root), "--as", "main session 1", "--",
                        sys.executable, "-c", "print('1 passed')")
    assert code == 0 and "not recorded" in out and "is on main, not feat/x" in out
    assert "last run" not in run_cli("status", "--root", str(root))[1]


def test_release_ends_a_waiter_named_by_its_booking(tmp_path, monkeypatch):
    from flotilla.core.storage import LocalLogStore
    from flotilla.lane import book
    from flotilla.lane.procs import ProcessTable
    root = onboarded(tmp_path, monkeypatch)
    stuck = book.Book(LocalLogStore(tmp_path / "state" / "lane"), ProcessTable.for_machine()).enqueue(
        "main session 2", "waiting", pid=None)
    code, out = run_cli("lane", "release", "--booking", stuck.id, "--root", str(root), "--as", "orchestrator 1")
    assert code == 0 and f"ended {stuck.id}" in out
