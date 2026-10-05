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


def test_lane_run_takes_a_ceiling(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch)
    code, out = run_cli("lane", "run", "--max", "1", "--tree", str(root), "--as", "main session 1", "--",
                        sys.executable, "-c", "import time; time.sleep(30)")
    assert code != 0 and "ceiling of 1 s" in out


def test_the_default_ceiling_follows_what_the_tiers_were_measured_at(tmp_path, monkeypatch):
    """For a receipt, whose commands are the measured tiers: ten times the slowest, never under ten minutes. For any
    other run in the lane, never under half an hour - a measured project must not get less room than an unmeasured
    one (review of 0.6.2, I5). The profile may name its own."""
    from flotilla.lane import commands
    assert commands.ceiling({}, {}) == 1800
    assert commands.ceiling({}, {"node": 34.0}, tiers=True) == 600
    assert commands.ceiling({}, {"node": 34.0}) == 1800
    assert commands.ceiling({}, {"node": 34.0, "e2e": 300.0}) == 3000
    assert commands.ceiling({}, {"node": 34.0, "e2e": 300.0}, tiers=True) == 3000
    assert commands.ceiling({"lane": {"max_run_seconds": 90}}, {"node": 34.0}) == 90


def test_lane_stop_stops_the_holders_own_run(tmp_path, monkeypatch):
    """Worldcore field test W22: to free the lane a seat ran `pkill -f` and killed itself first. Flotilla recorded
    the run's pid; `lane stop` uses it."""
    import os
    import subprocess
    import time
    root = onboarded(tmp_path, monkeypatch)
    env = {**os.environ, "FLOTILLA_STATE_DIR": str(tmp_path / "state"), "FLOTILLA_NO_CENSUS": "1"}
    cli_path = os.path.join(os.path.dirname(__file__), "..", "bin", "flotilla")
    runner = subprocess.Popen([sys.executable, cli_path, "lane", "run", "--tree", str(root), "--as", "main session 7",
                               "--", sys.executable, "-c", "import time; time.sleep(60)"], env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    for _ in range(100):
        if "main session 7" in run_cli("lane", "--root", str(root))[1]:
            break
        time.sleep(0.1)
    code, out = run_cli("lane", "stop", "--root", str(root), "--as", "main session 8")
    assert code == 2 and "main session 8" in out          # not someone else's run
    code, out = run_cli("lane", "stop", "--root", str(root), "--as", "main session 7")
    assert code == 0 and "stopped" in out, out
    said, _ = runner.communicate(timeout=20)
    assert runner.returncode != 0 and "killed" in said
    assert "held: nobody" in run_cli("lane", "--root", str(root))[1]


def test_lane_stop_does_not_take_another_sessions_word(tmp_path, monkeypatch):
    """Review of 0.6.2, I2: the lane fell back to the name given with --as even when the census said the caller is
    another session, so `lane stop --as <peer>` stopped the peer's run (and `release` released it)."""
    from flotilla.ledger.errors import ActorMismatch
    from flotilla.lane import commands
    root = onboarded(tmp_path, monkeypatch)

    def refuse(posts, as_name=None, **kw):
        raise ActorMismatch(f"this process runs inside `seat A`, which cannot act as `{as_name}`")
    monkeypatch.setattr(commands, "resolve_actor", refuse)
    for action in (["stop"], ["release"]):
        code, out = run_cli("lane", *action, "--root", str(root), "--as", "seat B")
        assert code == 2 and "cannot act as" in out


def test_lane_status_is_the_bare_lane(tmp_path, monkeypatch):
    """Twosuns field test of 0.6.7, W7: a judge waiting for the lane guessed `flotilla lane status` and met argparse's
    "invalid choice"; the status is the bare `flotilla lane`."""
    root = onboarded(tmp_path, monkeypatch)
    code, out = run_cli("lane", "status", "--root", str(root))
    assert code == 0 and "capacity: 1" in out and "held: nobody" in out, out


def test_a_peers_booking_note_cannot_forge_lines_or_move_the_cursor(tmp_path, monkeypatch):
    """Scan of 0.7.0, F2: a note (or `--for`) is text another session writes into the shared booking log, and the
    lane printed it as it was - a newline forged status lines, an escape rewrote the terminal. Every booking field a
    caller wrote is made visible where the log is read, so every place that prints it is covered."""
    root = onboarded(tmp_path, monkeypatch)
    note = "tests\n  waiting: nobody\nmachine:\x1b[2J\x1b]0;owned\x07"
    code, _ = run_cli("lane", "take", "--note", note, "--root", str(root), "--as", "main session 1")
    assert code == 0
    _, out = run_cli("lane", "--root", str(root))
    held = [line for line in out.splitlines() if "main session 1" in line]
    assert len(held) == 1 and "\\n  waiting: nobody" in held[0]
    assert "\x1b" not in out and "\x07" not in out
    code, out = run_cli("lane", "take", "--root", str(root), "--as", "review session 1")   # the refusal names it too
    refused = [line for line in out.splitlines() if "held by main session 1" in line]
    assert code == 2 and "\x1b" not in out and len(refused) == 1 and "\\n  waiting: nobody" in refused[0]


def test_a_name_with_a_hidden_character_still_releases_its_own_booking(tmp_path, monkeypatch):
    """Review of the scan fixes of 0.7.0, M1: booking names are made visible where the log is read, and release
    compared that with the raw name - a name with a tab or a joiner could no longer release its own booking."""
    root = onboarded(tmp_path, monkeypatch)
    name = "main\tsession 1"
    assert run_cli("lane", "take", "--root", str(root), "--as", name)[0] == 0
    code, out = run_cli("lane", "release", "--root", str(root), "--as", name)
    assert code == 0 and "released b1" in out


def _journal(tmp_path):
    from flotilla.core.storage import LocalLogStore
    from flotilla.lane import book
    return book.fold(LocalLogStore(tmp_path / "state" / "lane").read(book.KEY).records)


def test_a_lane_run_records_its_command_and_measurement(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch)
    code, out = run_cli("lane", "run", "--tree", str(root), "--", sys.executable, "-c", "print('1 passed')")
    assert code == 0, out
    [item] = [b for b in _journal(tmp_path).values() if b.command]
    assert sys.executable in item.command and item.ladder[-1].startswith("project:")
    assert item.verdict == "green" and item.seconds is not None and item.rule == 1


def test_a_receipt_records_the_tiers_it_ran(tmp_path, monkeypatch):
    tier = {"name": "unit", "command": f"{sys.executable} -c \"print('1 passed')\"", "required_for": ["handover"]}
    root = onboarded(tmp_path, monkeypatch, {**PROFILE, "tests": {"tier": [tier]}})
    code, out = run_cli("receipt", "run", "--purpose", "handover", "--tree", str(root))
    assert code == 0, out
    [item] = [b for b in _journal(tmp_path).values() if b.note == "handover receipt"]
    assert item.will_run == ["unit"] and item.ladder[0].startswith("receipt:")
    assert [tier["name"] for tier in item.ran] == ["unit"]
    assert all("seconds" in tier and "peak_mb" in tier for tier in item.ran)
    assert item.verdict == "green" and item.seconds is not None


def test_a_nested_run_is_not_measured_twice(tmp_path, monkeypatch):
    """A `lane run` inside a booking is part of it: the outer booking is measured, the inner never books."""
    from pathlib import Path
    root = onboarded(tmp_path, monkeypatch)
    flotilla = Path(__file__).resolve().parent.parent / "bin" / "flotilla"
    inner = f"{flotilla} lane run --tree {root} --wait 5 -- {sys.executable} -c pass"   # a broken rule waits, not hangs
    code, out = run_cli("lane", "run", "--tree", str(root), "--", "sh", "-c", inner)
    assert code == 0, out
    journal = _journal(tmp_path)
    [outer] = [b for b in journal.values() if not b.inside]
    nested = [b for b in journal.values() if b.inside]
    assert outer.seconds is not None and all(b.inside == outer.id and b.state == "released" for b in nested)
    assert len(nested) == 1   # the inner run is recorded beside its parent, never booked a second time


def test_the_lane_shows_each_bookings_estimate(tmp_path, monkeypatch):
    root = onboarded(tmp_path, monkeypatch)
    for _ in range(3):
        run_cli("lane", "run", "--tree", str(root), "--", sys.executable, "-c", "print('1 passed')")
    from flotilla.core.storage import LocalLogStore
    from flotilla.lane import book

    class Alive:
        def alive(self, pid, mark):
            return True
    lanes = book.Book(LocalLogStore(tmp_path / "state" / "lane"), Alive())
    known = next(b for b in lanes.bookings().values() if b.command)
    lanes.enqueue("main session 2", "again", pid=None, mark="", command=known.command, ladder=known.ladder,
                  project=known.project)
    code, out = run_cli("lane", "--root", str(root))
    waiting = out.split("waiting:", 1)[1]
    assert "estimate:" in waiting and "3 runs (exact match)" in waiting, out


def test_an_older_booking_says_it_has_no_estimate():
    from flotilla.lane import book, commands
    assert commands.describe_estimate(book.Booking(id="b1"), {}) == "estimate: none (an older flotilla booked it)"


def test_an_estimate_names_unknown_parts():
    from flotilla.lane import book, commands
    item = book.Booking(id="b2", ladder=["exact:x", "project:q"], project="q")
    assert commands.describe_estimate(item, {}) == "estimate: ? s, 4 cores, 2.0 GB (fixed prior: 4 cores, 2 GB)"


def test_a_booking_taken_by_hand_says_so():
    from flotilla.lane import book, commands
    assert commands.describe_estimate(book.Booking(id="b1", rule=1), {}) == "estimate: none (taken by hand)"


def test_an_interrupted_run_records_that_it_was_killed(tmp_path, monkeypatch):
    """Review of stage 1: a run stopped by Ctrl-C or `lane stop` was released with nothing - it held the lane."""
    root = onboarded(tmp_path, monkeypatch)
    from flotilla.lane import run as runner

    def stopped(*a, **kw):
        raise SystemExit(143)
    monkeypatch.setattr(runner, "execute", stopped)
    run_cli("lane", "run", "--tree", str(root), "--", sys.executable, "-c", "pass")
    [item] = list(_journal(tmp_path).values())
    assert item.verdict == "killed" and item.seconds is not None


def test_an_interrupted_receipt_records_that_it_was_killed(tmp_path, monkeypatch):
    import pytest
    tier = {"name": "unit", "command": f"{sys.executable} -c \"print('1 passed')\"", "required_for": ["handover"]}
    root = onboarded(tmp_path, monkeypatch, {**PROFILE, "tests": {"tier": [tier]}})
    from flotilla.ledger import receipts

    def stopped(*a, **kw):
        raise SystemExit(143)
    monkeypatch.setattr(receipts, "run_receipt", stopped)
    with pytest.raises(SystemExit):
        run_cli("receipt", "run", "--purpose", "handover", "--tree", str(root))
    [item] = list(_journal(tmp_path).values())
    assert item.verdict == "killed" and item.seconds is not None


def test_a_receipt_inside_a_lane_run_records_its_tiers(tmp_path, monkeypatch):
    """Review of stage 1: a receipt nested in a booking wrote nothing, so its tiers never learned."""
    from pathlib import Path
    tier = {"name": "unit", "command": f"{sys.executable} -c \"print('1 passed')\"", "required_for": ["handover"]}
    root = onboarded(tmp_path, monkeypatch, {**PROFILE, "tests": {"tier": [tier]}})
    flotilla = Path(__file__).resolve().parent.parent / "bin" / "flotilla"
    inner = f"{flotilla} receipt run --purpose handover --tree {root} --lane-wait 5"
    code, out = run_cli("lane", "run", "--tree", str(root), "--", "sh", "-c", inner)
    assert code == 0, out
    journal = _journal(tmp_path)
    [outer] = [b for b in journal.values() if not b.inside]
    [nested] = [b for b in journal.values() if b.inside]
    assert nested.inside == outer.id and nested.state == "released"
    assert [t["name"] for t in nested.ran] == ["unit"] and nested.ladder[0].startswith("receipt:")


def test_a_receipt_refused_mid_way_says_refused_not_killed(tmp_path, monkeypatch):
    tier = {"name": "unit", "command": f"{sys.executable} -c \"print('1 passed')\"", "required_for": ["handover"]}
    root = onboarded(tmp_path, monkeypatch, {**PROFILE, "tests": {"tier": [tier]}})
    from flotilla.ledger import receipts

    def refused(*a, **kw):
        raise receipts.ReceiptRefused("the tree changed while its tiers ran")
    monkeypatch.setattr(receipts, "run_receipt", refused)
    run_cli("receipt", "run", "--purpose", "handover", "--tree", str(root))
    [item] = list(_journal(tmp_path).values())
    assert item.verdict == "refused"
