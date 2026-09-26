import sys

from flotilla.ledger import gate, receipts
from ledgerkit import PROFILE, commit, fake_gh, git, make_ledger, repo_with_origin

GREEN_CMD = f"{sys.executable} -c \"print('1 passed')\""


def ledger_with(tmp_path, ci, handler=lambda args: (1, ""), tiers=None):
    root = repo_with_origin(tmp_path)
    profile = {**PROFILE, "ci": ci}
    if tiers:
        profile["tests"] = {"tier": tiers}
    return root, make_ledger(root, tmp_path / "state", profile=profile, run=fake_gh(handler))


def jobs_handler(runs, jobs):
    def handler(args):
        if args[:2] == ["run", "list"]:
            return 0, runs
        if args[:2] == ["run", "view"]:
            return 0, {"jobs": jobs}
        return 1, ""
    return handler


def test_every_required_job_green_is_green(tmp_path):
    runs = [{"databaseId": 5, "event": "push"}]
    jobs = [{"name": "test", "status": "completed", "conclusion": "success"},
            {"name": "lint", "status": "completed", "conclusion": "success"}]
    root, ledger = ledger_with(tmp_path, {"provider": "github", "required_jobs": ["test", "lint"]},
                               jobs_handler(runs, jobs))
    sha = git(root, "rev-parse", "HEAD")
    found = gate.gate_for(ledger, sha)
    assert found.status == gate.GREEN and found.text == f"github run 5 over {sha[:7]}"


def test_one_red_job_is_red_and_named(tmp_path):
    jobs = [{"name": "test", "status": "completed", "conclusion": "failure"}]
    root, ledger = ledger_with(tmp_path, {"provider": "github", "required_jobs": ["test"]},
                               jobs_handler([{"databaseId": 5, "event": "push"}], jobs))
    found = gate.gate_for(ledger, git(root, "rev-parse", "HEAD"))
    assert found.status == gate.RED and "test (failure)" in found.text


def test_a_required_job_missing_or_running_is_pending(tmp_path):
    jobs = [{"name": "test", "status": "in_progress", "conclusion": ""}]
    root, ledger = ledger_with(tmp_path, {"provider": "github", "required_jobs": ["test", "e2e"]},
                               jobs_handler([{"databaseId": 5, "event": "push"}], jobs))
    found = gate.gate_for(ledger, git(root, "rev-parse", "HEAD"))
    assert found.status == gate.PENDING and "test" in found.text and "e2e" in found.text


def test_github_unreachable_is_unknown_not_red(tmp_path):
    root, ledger = ledger_with(tmp_path, {"provider": "github", "required_jobs": ["test"]})
    assert gate.gate_for(ledger, git(root, "rev-parse", "HEAD")).status == gate.UNKNOWN


def test_no_required_jobs_in_the_profile_is_unknown(tmp_path):
    root, ledger = ledger_with(tmp_path, {"provider": "github"})
    found = gate.gate_for(ledger, git(root, "rev-parse", "HEAD"))
    assert found.status == gate.UNKNOWN and "/flotilla:check" in found.text


def test_ci_over_a_later_push_covers_an_earlier_commit(tmp_path):
    root = repo_with_origin(tmp_path)
    first = commit(root, "one", "one.txt")
    later = commit(root, "two", "two.txt")

    def handler(args):
        if args[:2] == ["run", "list"] and "--commit" in args:
            return 0, []
        if args[:2] == ["run", "list"]:
            return 0, [{"databaseId": 9, "headSha": later}]
        if args[:2] == ["run", "view"]:
            return 0, {"jobs": [{"name": "test", "status": "completed", "conclusion": "success"}]}
        return 1, ""
    ledger = make_ledger(root, tmp_path / "state",
                         profile={**PROFILE, "ci": {"provider": "github", "required_jobs": ["test"]}},
                         run=fake_gh(handler))
    assert gate.gate_for(ledger, first).text == f"github run 9 over {first[:7]}"


def test_the_gate_command_answers_by_exit_code(tmp_path):
    for code, status in ((0, gate.GREEN), (1, gate.RED), (2, gate.PENDING), (7, gate.UNKNOWN)):
        (tmp_path / str(code)).mkdir()
        root, ledger = ledger_with(tmp_path / str(code), {
            "provider": "command", "gate_command": f"{sys.executable} -c \"import sys; sys.exit({code})\""})
        assert gate.gate_for(ledger, git(root, "rev-parse", "HEAD")).status == status


def test_without_ci_a_push_receipt_is_the_gate(tmp_path):
    tiers = [{"name": "unit", "command": GREEN_CMD, "required_for": ["push"]}]
    root, ledger = ledger_with(tmp_path, {"provider": "none"}, tiers=tiers)
    sha = git(root, "rev-parse", "HEAD")
    assert gate.gate_for(ledger, sha).status == gate.PENDING
    receipts.run_receipt(root, state=ledger.state_dir, repo_key=ledger.repo_key, purpose="push",
                         profile=ledger.profile, timeout=60)
    assert gate.gate_for(ledger, sha).text == f"local receipt over {sha[:7]}"


def test_without_ci_and_without_push_tiers_nothing_is_verified_and_it_says_so(tmp_path):
    root, ledger = ledger_with(tmp_path, {"provider": "none"})
    found = gate.gate_for(ledger, git(root, "rev-parse", "HEAD"))
    assert found.status == gate.NONE and "nothing verified" in found.text
