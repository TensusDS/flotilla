import pytest

from flotilla.ledger import delivery
from flotilla.ledger.errors import MoveRefused
from ledgerkit import PROFILE, actor, commit, drive, fake_gh, git, make_ledger, repo_with_origin

SENDER = "sender 1"
DIRECT = {**PROFILE, "flow": {"mode": "direct"}}


def pr_handler(answers):
    def handler(args):
        if args[:2] == ["pr", "view"]:
            return 0, answers["pr"]
        if args[:2] == ["run", "list"]:
            return (0, answers["runs"]) if "runs" in answers else (1, "")
        if args[:2] == ["run", "view"]:
            return 0, {"jobs": answers.get("jobs", [])}
        return 1, ""
    return handler


@pytest.fixture()
def pr_world(tmp_path):
    root = repo_with_origin(tmp_path)
    answers = {}
    ledger = make_ledger(root, tmp_path / "state", run=fake_gh(pr_handler(answers)))
    return root, ledger, answers


def open_pr(branch, tip, **more):
    return {"state": "OPEN", "headRefName": branch, "headRefOid": tip, "baseRefName": "main", **more}


def test_queue_in_pr_mode_records_the_pr(pr_world):
    root, ledger, answers = pr_world
    row = drive(root, ledger)
    answers["pr"] = open_pr("feat/x", row.tip)
    queued = delivery.queue(ledger, actor(ledger, SENDER), "feat/x", pr=12)
    assert (queued.state, queued.pr, queued.tip) == ("queued", "12", row.tip)


def test_pr_mode_needs_the_pr_number(pr_world):
    root, ledger, _ = pr_world
    drive(root, ledger)
    with pytest.raises(MoveRefused, match="--pr"):
        delivery.queue(ledger, actor(ledger, SENDER), "feat/x")


def test_a_pr_whose_head_is_not_the_revision_read_is_refused(pr_world):
    root, ledger, answers = pr_world
    drive(root, ledger)
    answers["pr"] = open_pr("feat/x", "0" * 40)
    with pytest.raises(MoveRefused, match="push the branch first"):
        delivery.queue(ledger, actor(ledger, SENDER), "feat/x", pr=12)


def test_a_pr_for_another_branch_is_refused(pr_world):
    root, ledger, answers = pr_world
    row = drive(root, ledger)
    answers["pr"] = open_pr("feat/other", row.tip)
    with pytest.raises(MoveRefused, match="feat/other"):
        delivery.queue(ledger, actor(ledger, SENDER), "feat/x", pr=12)


def test_github_unreachable_refuses_the_queue(tmp_path):
    root = repo_with_origin(tmp_path)
    ledger = make_ledger(root, tmp_path / "state", run=fake_gh(lambda args: (1, "")))
    drive(root, ledger)
    with pytest.raises(MoveRefused, match="could not ask GitHub"):
        delivery.queue(ledger, actor(ledger, SENDER), "feat/x", pr=12)


def test_direct_mode_refuses_a_pr_number(tmp_path):
    root = repo_with_origin(tmp_path)
    ledger = make_ledger(root, tmp_path / "state", profile=DIRECT)
    drive(root, ledger)
    with pytest.raises(MoveRefused, match="does not use pull requests"):
        delivery.queue(ledger, actor(ledger, SENDER), "feat/x", pr=12)


def test_a_dependency_blocks_the_queue_until_it_ships(tmp_path):
    root = repo_with_origin(tmp_path)
    ledger = make_ledger(root, tmp_path / "state", profile=DIRECT)
    drive(root, ledger, "feat/a", to="claimed")
    drive(root, ledger, "feat/b", requires=["feat/a"])
    with pytest.raises(MoveRefused, match=r"requires `feat/a` \(claimed\)"):
        delivery.queue(ledger, actor(ledger, SENDER), "feat/b")


def test_a_branch_that_moved_since_acceptance_is_not_queued(tmp_path):
    root = repo_with_origin(tmp_path)
    ledger = make_ledger(root, tmp_path / "state", profile=DIRECT)
    drive(root, ledger)
    git(root, "checkout", "-q", "feat/x")
    commit(root, "after the verdict", "late.txt")
    git(root, "checkout", "-q", "main")
    with pytest.raises(MoveRefused, match="moved since it was accepted"):
        delivery.queue(ledger, actor(ledger, SENDER), "feat/x")


def test_without_review_a_claim_is_queued_at_its_tip(tmp_path):
    root = repo_with_origin(tmp_path)
    ledger = make_ledger(root, tmp_path / "state", profile={**DIRECT, "review": {"depth": "none"}})
    row = drive(root, ledger, to="claimed")
    queued = delivery.queue(ledger, actor(ledger, SENDER), "feat/x")
    assert queued.state == "queued" and queued.tip == git(root, "rev-parse", "feat/x") and not row.tip


def test_only_the_sender_queues(tmp_path):
    root = repo_with_origin(tmp_path)
    ledger = make_ledger(root, tmp_path / "state", profile=DIRECT)
    drive(root, ledger)
    with pytest.raises(MoveRefused, match="may not `queue`"):
        delivery.queue(ledger, actor(ledger, "main session 1"), "feat/x")
