import pytest

from flotilla.ledger import delivery
from flotilla.ledger.errors import MoveRefused, NotYet
from ledgerkit import (PROFILE, actor, commit, drive, fake_gh, git, make_ledger, merge, repo_with_origin,
                       shipped_direct)

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


@pytest.fixture()
def direct(tmp_path):
    root = repo_with_origin(tmp_path)
    return root, make_ledger(root, tmp_path / "state", profile=DIRECT)


def queued(root, ledger, name="feat/x"):
    drive(root, ledger, name)
    return delivery.queue(ledger, actor(ledger, SENDER), name)


def test_land_records_the_merge_on_the_local_trunk(direct):
    root, ledger = direct
    queued(root, ledger)
    head = merge(root, "feat/x")
    row = delivery.land(ledger, actor(ledger, SENDER), "feat/x")
    assert (row.state, row.merge) == ("landed", head)


def test_land_refuses_work_that_is_not_merged(direct):
    root, ledger = direct
    queued(root, ledger)
    with pytest.raises(MoveRefused, match="does not contain the revision read"):
        delivery.land(ledger, actor(ledger, SENDER), "feat/x")


def test_land_refuses_a_batch_carrying_unread_work(direct):
    root, ledger = direct
    queued(root, ledger)
    merge(root, "feat/x")
    stray = commit(root, "a quick fix nobody read", "stray.txt")
    with pytest.raises(MoveRefused, match=rf"nobody read: {stray[:7]} a quick fix nobody read"):
        delivery.land(ledger, actor(ledger, SENDER), "feat/x", merge=stray)


def test_land_accepts_a_squash_of_the_revision_read(direct):
    root, ledger = direct
    queued(root, ledger)
    squashed = merge(root, "feat/x", squash=True)
    assert delivery.land(ledger, actor(ledger, SENDER), "feat/x").merge == squashed


def test_a_merge_that_is_not_on_trunk_is_refused(direct):
    root, ledger = direct
    queued(root, ledger)
    merge(root, "feat/x")
    drive(root, ledger, "feat/y", to="claimed")
    side = git(root, "rev-parse", "feat/y")
    with pytest.raises(MoveRefused, match="not on the local `main`"):
        delivery.land(ledger, actor(ledger, SENDER), "feat/x", merge=side)


def test_pr_mode_never_lands(pr_world):
    root, ledger, answers = pr_world
    row = drive(root, ledger)
    answers["pr"] = open_pr("feat/x", row.tip)
    delivery.queue(ledger, actor(ledger, SENDER), "feat/x", pr=12)
    with pytest.raises(MoveRefused, match="not legal from `queued`"):
        delivery.land(ledger, actor(ledger, SENDER), "feat/x")


GITHUB = {"provider": "github", "required_jobs": ["test"]}


def pr_shipping(tmp_path):
    root = repo_with_origin(tmp_path)
    answers = {}
    ledger = make_ledger(root, tmp_path / "state", profile={**PROFILE, "ci": GITHUB},
                         run=fake_gh(pr_handler(answers)))
    row = drive(root, ledger)
    answers["pr"] = open_pr("feat/x", row.tip)
    delivery.queue(ledger, actor(ledger, SENDER), "feat/x", pr=7)
    head = merge(root, "feat/x")
    git(root, "push", "-q", "origin", "main")
    answers["pr"] = {"state": "MERGED", "headRefOid": row.tip, "mergeCommit": {"oid": head}}
    answers["runs"] = [{"databaseId": 5, "event": "push"}]
    answers["jobs"] = [{"name": "test", "status": "completed", "conclusion": "success"}]
    return root, ledger, answers, row, head


def test_a_merged_pr_with_green_ci_is_shipped(tmp_path):
    root, ledger, answers, row, head = pr_shipping(tmp_path)
    shipped = delivery.ship(ledger, actor(ledger, SENDER), "feat/x")
    assert (shipped.state, shipped.merge, shipped.gate) == ("shipped", head, f"github run 5 over {head[:7]}")


def test_an_open_pr_is_not_shipped_yet(tmp_path):
    root, ledger, answers, row, head = pr_shipping(tmp_path)
    answers["pr"] = {"state": "OPEN", "headRefOid": row.tip}
    with pytest.raises(NotYet, match="still open"):
        delivery.ship(ledger, actor(ledger, SENDER), "feat/x")


def test_a_pr_that_merged_another_head_is_not_shipped(tmp_path):
    root, ledger, answers, row, head = pr_shipping(tmp_path)
    answers["pr"] = {"state": "MERGED", "headRefOid": "f" * 40, "mergeCommit": {"oid": head}}
    with pytest.raises(MoveRefused, match="not what was read"):
        delivery.ship(ledger, actor(ledger, SENDER), "feat/x")
    assert ledger.rows()["r1"].state == "queued"


def test_pending_ci_is_not_shipped(tmp_path):
    root, ledger, answers, row, head = pr_shipping(tmp_path)
    answers["jobs"] = [{"name": "test", "status": "queued", "conclusion": ""}]
    with pytest.raises(NotYet, match="has not finished"):
        delivery.ship(ledger, actor(ledger, SENDER), "feat/x")
    assert ledger.rows()["r1"].state == "queued"


def test_red_ci_is_refused_and_named(tmp_path):
    root, ledger, answers, row, head = pr_shipping(tmp_path)
    answers["jobs"] = [{"name": "test", "status": "completed", "conclusion": "failure"}]
    with pytest.raises(MoveRefused, match=r"red: test \(failure\)"):
        delivery.ship(ledger, actor(ledger, SENDER), "feat/x")


def test_landed_but_not_pushed_is_not_shipped(direct):
    root, ledger = direct
    queued(root, ledger)
    merge(root, "feat/x")
    delivery.land(ledger, actor(ledger, SENDER), "feat/x")
    with pytest.raises(NotYet, match="landed, not pushed"):
        delivery.ship(ledger, actor(ledger, SENDER), "feat/x")


def test_direct_push_without_ci_ships_and_says_nothing_was_verified(direct):
    root, ledger = direct
    row = shipped_direct(root, ledger)
    assert row.state == "shipped" and row.gate.startswith("none:")


def test_reconcile_ships_what_is_ready_and_reports_the_rest(direct):
    root, ledger = direct
    queued(root, ledger, "feat/a")
    merge(root, "feat/a")
    delivery.land(ledger, actor(ledger, SENDER), "feat/a")
    git(root, "push", "-q", "origin", "main")
    queued(root, ledger, "feat/b")
    merge(root, "feat/b")
    delivery.land(ledger, actor(ledger, SENDER), "feat/b")
    lines = delivery.reconcile(ledger, actor(ledger, SENDER))
    assert any(line.startswith("shipped feat/a") for line in lines)
    assert any(line.startswith("not yet feat/b") and "landed, not pushed" in line for line in lines)


def test_land_refuses_unread_work_even_when_merge_names_an_older_commit(direct):
    root, ledger = direct
    queued(root, ledger)
    head = merge(root, "feat/x")
    stray = commit(root, "rides along after the merge", "stray.txt")
    with pytest.raises(MoveRefused, match=rf"nobody read: {stray[:7]}"):
        delivery.land(ledger, actor(ledger, SENDER), "feat/x", merge=head)
