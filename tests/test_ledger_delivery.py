import subprocess
from pathlib import Path

import pytest

from flotilla.ledger import delivery
from flotilla.ledger.errors import MoveRefused, NotYet
from ledgerkit import (IDENTITY, PROFILE, actor, branch, commit, drive, fake_gh, git, make_ledger, merge,
                       repo_with_origin, shipped_direct)

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


def test_github_unreachable_is_not_yet_known_not_refused(tmp_path):
    """An instrument that could not ask says unknown (exit 3, ask again), never "refused" (exit 2)."""
    root = repo_with_origin(tmp_path)
    unreachable = fake_gh(lambda args: (1, "", "error connecting to api.github.com"))
    ledger = make_ledger(root, tmp_path / "state", run=unreachable)
    drive(root, ledger)
    with pytest.raises(NotYet, match="could not ask GitHub"):
        delivery.queue(ledger, actor(ledger, SENDER), "feat/x", pr=12)


def test_a_pr_github_says_does_not_exist_is_refused(tmp_path):
    root = repo_with_origin(tmp_path)
    missing = "GraphQL: Could not resolve to a PullRequest with the number of 12. (repository.pullRequest)"
    ledger = make_ledger(root, tmp_path / "state", run=fake_gh(lambda args: (1, "", missing)))
    drive(root, ledger)
    with pytest.raises(MoveRefused, match="no PR #12") as caught:
        delivery.queue(ledger, actor(ledger, SENDER), "feat/x", pr=12)
    assert not isinstance(caught.value, NotYet)


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


def test_an_after_link_blocks_the_queue_until_that_row_ships(tmp_path):
    from flotilla.ledger import core
    root = repo_with_origin(tmp_path)
    ledger = make_ledger(root, tmp_path / "state", profile={**DIRECT, "review": {"depth": "none"}})
    drive(root, ledger, "feat/a", to="claimed")
    branch(root, "tool/measure-a", "a measurement")
    core.claim(ledger, actor(ledger, "main session 1"), "tool/measure-a", after=["feat/a"])
    with pytest.raises(MoveRefused, match=r"goes after `feat/a` \(claimed\); it is queued once they are delivered"):
        delivery.queue(ledger, actor(ledger, SENDER), "tool/measure-a")


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


def pushed_from_a_side_tree(root, name="feat/x", *, extra=None):
    """The sender's direct-push sequence without touching the local trunk: merge on a branch from origin's trunk,
    push HEAD:main, come back. Returns the pushed commit."""
    git(root, "fetch", "-q", "origin")
    git(root, "checkout", "-q", "-b", f"integrate-{name.replace('/', '-')}", "origin/main")
    git(root, *IDENTITY, "merge", "-q", "--no-ff", "-m", f"merge {name}", name)
    if extra:
        commit(root, extra, "extra.txt")
    pushed = git(root, "rev-parse", "HEAD")
    git(root, "push", "-q", "origin", "HEAD:main")
    git(root, "checkout", "-q", "main")
    return pushed


def test_land_accepts_a_merge_already_pushed_to_origin(direct):
    root, ledger = direct
    queued(root, ledger)
    pushed = pushed_from_a_side_tree(root)
    assert git(root, "rev-parse", "main") != pushed   # the local trunk never moved
    row = delivery.land(ledger, actor(ledger, SENDER), "feat/x", merge=pushed)
    assert (row.state, row.merge) == ("landed", pushed)
    assert delivery.ship(ledger, actor(ledger, SENDER), "feat/x").state == "shipped"


def test_land_without_merge_takes_origins_trunk_when_the_local_one_lacks_the_work(direct):
    root, ledger = direct
    queued(root, ledger)
    pushed = pushed_from_a_side_tree(root)
    assert delivery.land(ledger, actor(ledger, SENDER), "feat/x").merge == pushed


def test_a_merge_on_neither_trunk_is_refused_with_the_sequence(direct):
    root, ledger = direct
    queued(root, ledger)
    with pytest.raises(MoveRefused, match="push HEAD:main"):
        delivery.land(ledger, actor(ledger, SENDER), "feat/x", merge="feat/x")


def test_unread_work_already_on_origin_is_refused_at_land(direct):
    root, ledger = direct
    queued(root, ledger)
    pushed = pushed_from_a_side_tree(root, extra="a quick fix nobody read")
    with pytest.raises(MoveRefused, match="nobody read: .* a quick fix nobody read"):
        delivery.land(ledger, actor(ledger, SENDER), "feat/x", merge=pushed)


def test_unread_work_above_the_named_merge_is_refused_at_land(direct):
    root, ledger = direct
    queued(root, ledger)
    pushed = pushed_from_a_side_tree(root, extra="a stray commit nobody read")
    merged = git(root, "rev-parse", f"{pushed}~1")
    with pytest.raises(MoveRefused, match="nobody read: .* a stray commit nobody read"):
        delivery.land(ledger, actor(ledger, SENDER), "feat/x", merge=merged)


def pushed_by_the_person(root, tmp_path, message):
    """Somebody else pushes straight to origin's trunk from their own clone."""
    other = Path(tmp_path) / "person"
    git(tmp_path, "clone", "-q", str(Path(tmp_path) / "origin.git"), str(other))
    commit(other, message, "person.txt")
    git(other, "push", "-q", "origin", "HEAD:main")


def test_work_origin_already_had_before_the_merge_does_not_block_land(direct, tmp_path):
    root, ledger = direct
    queued(root, ledger)
    pushed_by_the_person(root, tmp_path, "the person's own README tweak")
    pushed = pushed_from_a_side_tree(root)
    row = delivery.land(ledger, actor(ledger, SENDER), "feat/x", merge=pushed)
    assert (row.state, row.merge) == ("landed", pushed)


def test_a_row_claimed_before_its_branch_existed_lands_from_origin(direct):
    from flotilla.ledger import core, handover, reading

    root, ledger = direct
    claimed = core.claim(ledger, actor(ledger, "main session 1"), "feat/x")
    assert claimed.base == ""
    branch(root, "feat/x", "work")
    handed = handover.hand(ledger, actor(ledger, "main session 1"), "feat/x")
    reading.take(ledger, actor(ledger, "review session 1"), "feat/x")
    reading.accept(ledger, actor(ledger, "review session 1"), "feat/x", reviewed=handed.tip)
    delivery.queue(ledger, actor(ledger, SENDER), "feat/x")
    pushed = pushed_from_a_side_tree(root)
    assert delivery.land(ledger, actor(ledger, SENDER), "feat/x", merge=pushed).merge == pushed


def test_land_without_merge_takes_the_commit_that_carries_the_row_not_a_later_one(direct):
    root, ledger = direct
    queued(root, ledger)
    first = pushed_from_a_side_tree(root)
    queued(root, ledger, "feat/y")
    pushed_from_a_side_tree(root, "feat/y")
    assert delivery.land(ledger, actor(ledger, SENDER), "feat/x").merge == first


def test_reconcile_names_land_for_a_queued_row_already_on_origin(direct):
    root, ledger = direct
    queued(root, ledger)
    pushed_from_a_side_tree(root)
    lines = delivery.reconcile(ledger, actor(ledger, SENDER))
    assert any("feat/x" in line and "flotilla work land feat/x" in line for line in lines)
    assert not any("<the commit>" in line for line in lines)   # land finds it; a placeholder made the sender ask (W11)


def test_reconcile_stays_quiet_about_a_queued_row_not_on_origin(direct):
    root, ledger = direct
    queued(root, ledger)
    assert delivery.reconcile(ledger, actor(ledger, SENDER)) == []


def conflicting_branch_merged_with_trunk(root, name="feat/x"):
    """A branch and trunk change one file differently; the author merges trunk into the branch and resolves the
    conflict by hand, the way the twosuns fleet did before handing over. Returns the hand-made merge commit."""
    git(root, "checkout", "-q", "-b", name)
    commit(root, "the branch's side", "shared.txt", "branch\n")
    git(root, "checkout", "-q", "main")
    commit(root, "trunk's side", "shared.txt", "trunk\n")
    git(root, "push", "-q", "origin", "main")
    git(root, "checkout", "-q", name)
    done = subprocess.run(["git", *IDENTITY, "merge", "-q", "main"], cwd=root, capture_output=True, text=True)
    assert done.returncode != 0   # a real conflict
    (root / "shared.txt").write_text("both\n", encoding="utf-8")
    git(root, "add", "shared.txt")
    git(root, *IDENTITY, "commit", "-q", "--no-edit", "-m", f"merge main into {name}")
    resolved = git(root, "rev-parse", "HEAD")
    git(root, "checkout", "-q", "main")
    return resolved


def accepted_over(ledger, name, tip):
    from flotilla.ledger import core, handover, reading
    core.claim(ledger, actor(ledger, "main session 1"), name)
    handover.hand(ledger, actor(ledger, "main session 1"), name)
    reading.take(ledger, actor(ledger, "review session 1"), name)
    return reading.accept(ledger, actor(ledger, "review session 1"), name, reviewed=tip)


def test_a_hand_resolved_merge_the_reader_read_lands(direct):
    root, ledger = direct
    resolved = conflicting_branch_merged_with_trunk(root)
    accepted_over(ledger, "feat/x", resolved)
    delivery.queue(ledger, actor(ledger, SENDER), "feat/x")
    pushed = pushed_from_a_side_tree(root)
    row = delivery.land(ledger, actor(ledger, SENDER), "feat/x", merge=pushed)
    assert (row.state, row.merge) == ("landed", pushed)


def test_a_hand_resolved_merge_inside_the_read_range_lands(direct):
    root, ledger = direct
    conflicting_branch_merged_with_trunk(root)
    git(root, "checkout", "-q", "feat/x")
    tip = commit(root, "after the merge", "after.txt")
    git(root, "checkout", "-q", "main")
    accepted_over(ledger, "feat/x", tip)
    delivery.queue(ledger, actor(ledger, SENDER), "feat/x")
    pushed = pushed_from_a_side_tree(root)
    assert delivery.land(ledger, actor(ledger, SENDER), "feat/x", merge=pushed).state == "landed"


def senders_own_resolution(root, ledger):
    """The sender merges an accepted branch into a trunk that moved, resolves the conflict itself and pushes:
    the resolution is work nobody read (twosuns H21, b7513e3). Returns the pushed merge."""
    git(root, "checkout", "-q", "-b", "feat/x")
    commit(root, "the branch's side", "shared.txt", "branch\n")
    git(root, "checkout", "-q", "main")
    accepted_over(ledger, "feat/x", git(root, "rev-parse", "feat/x"))
    delivery.queue(ledger, actor(ledger, SENDER), "feat/x")
    commit(root, "trunk's side", "shared.txt", "trunk\n")
    git(root, "push", "-q", "origin", "main")
    git(root, "checkout", "-q", "-b", "integrate", "origin/main")
    done = subprocess.run(["git", *IDENTITY, "merge", "-q", "--no-ff", "-m", "merge feat/x", "feat/x"], cwd=root,
                          capture_output=True, text=True)
    assert done.returncode != 0
    (root / "shared.txt").write_text("the sender's guess\n", encoding="utf-8")
    git(root, "add", "shared.txt")
    git(root, *IDENTITY, "commit", "-q", "--no-edit", "-m", "merge feat/x")
    pushed = git(root, "rev-parse", "HEAD")
    git(root, "push", "-q", "origin", "HEAD:main")
    git(root, "checkout", "-q", "main")
    return pushed


def test_the_senders_own_hand_resolved_merge_is_still_unread(direct):
    root, ledger = direct
    pushed = senders_own_resolution(root, ledger)
    with pytest.raises(MoveRefused, match=rf"nobody read: {pushed[:7]}"):
        delivery.land(ledger, actor(ledger, SENDER), "feat/x", merge=pushed)


def pull(root):
    """The person pulls the main checkout, as the twosuns sessions kept asking them to."""
    git(root, "fetch", "-q", "origin")
    git(root, "merge", "-q", "--ff-only", "origin/main")


def test_a_pulled_main_checkout_does_not_turn_the_unread_check_off(direct):
    root, ledger = direct
    queued(root, ledger)
    pushed = pushed_from_a_side_tree(root, extra="unread work rides along")
    pull(root)
    assert git(root, "rev-parse", "main") == pushed   # the local trunk now carries the push
    with pytest.raises(MoveRefused, match="nobody read: .* unread work rides along"):
        delivery.land(ledger, actor(ledger, SENDER), "feat/x", merge=pushed)
    with pytest.raises(MoveRefused, match="nobody read: .* unread work rides along"):
        delivery.land(ledger, actor(ledger, SENDER), "feat/x")


def test_a_reviewed_merge_lands_from_a_pulled_main_checkout(direct):
    root, ledger = direct
    queued(root, ledger)
    pushed = pushed_from_a_side_tree(root)
    pull(root)
    assert delivery.land(ledger, actor(ledger, SENDER), "feat/x").merge == pushed


def test_a_reader_vouches_for_the_senders_resolution_and_it_lands(direct):
    from flotilla.ledger import outside
    root, ledger = direct
    pushed = senders_own_resolution(root, ledger)
    row = outside.vouch(ledger, actor(ledger, "review session 1"), "feat/x", commit=pushed)
    assert (row.state, row.vouched) == ("queued", [pushed])
    assert delivery.land(ledger, actor(ledger, SENDER), "feat/x", merge=pushed).state == "landed"


def test_a_vouch_counts_from_a_pulled_main_checkout_too(direct):
    from flotilla.ledger import outside
    root, ledger = direct
    pushed = senders_own_resolution(root, ledger)
    pull(root)
    outside.vouch(ledger, actor(ledger, "review session 1"), "feat/x", commit=pushed)
    assert delivery.land(ledger, actor(ledger, SENDER), "feat/x").merge == pushed


def test_only_a_reader_vouches_and_only_for_delivery_rows(direct):
    from flotilla.ledger import outside
    root, ledger = direct
    pushed = senders_own_resolution(root, ledger)
    for name in (SENDER, "main session 1"):
        with pytest.raises(MoveRefused):
            outside.vouch(ledger, actor(ledger, name), "feat/x", commit=pushed)
    with pytest.raises(MoveRefused, match="could not resolve"):
        outside.vouch(ledger, actor(ledger, "review session 1"), "feat/x", commit="no-such-commit")
    drive(root, ledger, "feat/y", to="claimed")
    with pytest.raises(MoveRefused, match="accepted, queued or landed"):
        outside.vouch(ledger, actor(ledger, "review session 1"), "feat/y", commit=pushed)


def test_the_sender_returns_a_queued_row_that_no_longer_merges(direct):
    from flotilla.ledger import handover, views
    root, ledger = direct
    queued(root, ledger)
    row = delivery.send_back(ledger, actor(ledger, SENDER), "feat/x", why="conflicts with main in shared.txt")
    assert (row.state, row.verdict) == ("fixing", "")
    assert row.history[-1]["evidence"]["why"].startswith("conflicts")
    assert views.who_moves(row, DIRECT, ledger.rows()) == "main session 1"
    git(root, "checkout", "-q", "feat/x")
    commit(root, "merged main, resolved", "resolved.txt")
    git(root, "checkout", "-q", "main")
    assert handover.hand(ledger, actor(ledger, "main session 1"), "feat/x").state == "handed"


def test_an_accepted_row_may_be_returned_too(direct):
    root, ledger = direct
    drive(root, ledger)
    assert delivery.send_back(ledger, actor(ledger, SENDER), "feat/x", why="trunk moved").state == "fixing"


def test_return_needs_a_reason_the_sender_and_a_row_past_its_read(direct):
    root, ledger = direct
    queued(root, ledger)
    with pytest.raises(MoveRefused, match="--why"):
        delivery.send_back(ledger, actor(ledger, SENDER), "feat/x", why=" ")
    with pytest.raises(MoveRefused):
        delivery.send_back(ledger, actor(ledger, "review session 1"), "feat/x", why="x")
    drive(root, ledger, "feat/y", to="handed")
    with pytest.raises(MoveRefused, match="not legal from `handed`"):
        delivery.send_back(ledger, actor(ledger, SENDER), "feat/y", why="x")


def test_a_returned_row_carries_the_new_reason_not_an_old_fix(direct):
    from flotilla.ledger import handover, letters, reading
    root, ledger = direct
    drive(root, ledger, to="handed")
    reading.fix(ledger, actor(ledger, "review session 1"), "feat/x", why="OLD: no test for an empty file")
    git(root, "checkout", "-q", "feat/x")
    tip = commit(root, "a test for an empty file", "empty.txt")
    git(root, "checkout", "-q", "main")
    handover.hand(ledger, actor(ledger, "main session 1"), "feat/x")
    reading.take(ledger, actor(ledger, "review session 1"), "feat/x")
    reading.accept(ledger, actor(ledger, "review session 1"), "feat/x", reviewed=tip)
    delivery.queue(ledger, actor(ledger, SENDER), "feat/x")
    row = delivery.send_back(ledger, actor(ledger, SENDER), "feat/x", why="NEW: conflicts with main in shared.txt")
    assert row.why == "NEW: conflicts with main in shared.txt"
    assert "NEW: conflicts" in letters._body(row) and "OLD" not in letters._body(row)


def test_a_post_that_may_land_never_vouches_whatever_the_project_gives_it(tmp_path):
    import dataclasses

    from flotilla.ledger import outside
    from flotilla.posts import TEMPLATE_DIR, load_post
    posts = {p.name: p for p in (load_post(path) for path in sorted(TEMPLATE_DIR.glob("*.md")))}
    posts["sender"] = dataclasses.replace(posts["sender"], may=posts["sender"].may | {"vouch"})
    root = repo_with_origin(tmp_path)
    ledger = make_ledger(root, tmp_path / "state", profile=DIRECT, posts=posts)
    pushed = senders_own_resolution(root, ledger)
    with pytest.raises(MoveRefused, match="may land"):
        outside.vouch(ledger, actor(ledger, SENDER), "feat/x", commit=pushed)


def test_the_unread_refusal_names_vouch_as_a_way_out(direct):
    root, ledger = direct
    pushed = senders_own_resolution(root, ledger)
    with pytest.raises(MoveRefused, match="flotilla work vouch"):
        delivery.land(ledger, actor(ledger, SENDER), "feat/x", merge=pushed)


def test_a_vouch_on_a_row_later_released_accounts_no_commit(direct):
    from flotilla.ledger import batch, core, outside
    root, ledger = direct
    pushed = senders_own_resolution(root, ledger)
    outside.vouch(ledger, actor(ledger, "review session 1"), "feat/x", commit=pushed)
    assert batch.Accounting(ledger, ledger.rows()).account(pushed) is not None
    core.release(ledger, actor(ledger, "main session 1"), "feat/x", why="dropped")
    assert batch.Accounting(ledger, ledger.rows()).account(pushed) is None


def test_a_vouch_on_a_row_that_later_ships_still_counts(direct):
    from flotilla.ledger import batch, judging, outside
    root, ledger = direct
    pushed = senders_own_resolution(root, ledger)
    outside.vouch(ledger, actor(ledger, "review session 1"), "feat/x", commit=pushed)
    delivery.land(ledger, actor(ledger, SENDER), "feat/x", merge=pushed)
    assert delivery.ship(ledger, actor(ledger, SENDER), "feat/x").state == "shipped"
    assert "vouched for in `feat/x`" in batch.Accounting(ledger, ledger.rows()).account(pushed)
    judging.close(ledger, actor(ledger, "main session 1"), "feat/x")   # closed is terminal, and still delivered
    assert "vouched for in `feat/x`" in batch.Accounting(ledger, ledger.rows()).account(pushed)


def test_return_clears_the_vouch(direct):
    from flotilla.ledger import batch, outside
    root, ledger = direct
    pushed = senders_own_resolution(root, ledger)
    outside.vouch(ledger, actor(ledger, "review session 1"), "feat/x", commit=pushed)
    row = delivery.send_back(ledger, actor(ledger, SENDER), "feat/x", why="the resolution was wrong")
    assert (row.state, row.vouched) == ("fixing", [])
    assert batch.Accounting(ledger, ledger.rows()).account(pushed) is None


def test_return_of_a_row_with_a_pr_says_the_pr_stays_open(pr_world):
    from flotilla.ledger import letters
    root, ledger, answers = pr_world
    row = drive(root, ledger)
    answers["pr"] = open_pr("feat/x", row.tip)
    delivery.queue(ledger, actor(ledger, SENDER), "feat/x", pr=12)
    before = ledger.rows()
    back = delivery.send_back(ledger, actor(ledger, SENDER), "feat/x", why="conflicts with main")
    assert "PR #12 stays open" in back.history[-1]["evidence"]["pr"]
    found = letters.changed(before, ledger.rows(), ledger.profile, ledger.posts, SENDER, lambda: None)
    assert any("PR #12 stays open" in letter.text for letter in found)


HUMAN = {**PROFILE, "flow": {"mode": "direct", "merge_authorized_by": "human"}}


def human_world(tmp_path):
    root = repo_with_origin(tmp_path)
    ledger = make_ledger(root, tmp_path / "state", profile=HUMAN)
    drive(root, ledger, "feat/x")
    return root, ledger


def test_where_a_human_authorizes_merges_nothing_is_queued_without_their_approval(tmp_path):
    """merge_authorized_by = human was held only by the sender post's text; now the ledger holds it (security
    review F24): the sender's queue waits for the person's own approve of the revision that was read."""
    root, ledger = human_world(tmp_path)
    with pytest.raises(MoveRefused, match="flotilla work approve feat/x"):
        delivery.queue(ledger, actor(ledger, "sender 1"), "feat/x")
    row = delivery.approve(ledger, "feat/x")
    assert row.approved == row.verdict and row.state == "accepted"
    assert delivery.queue(ledger, actor(ledger, "sender 1"), "feat/x").state == "queued"


def test_only_a_person_approves(tmp_path, monkeypatch):
    from flotilla.core import caller
    root, ledger = human_world(tmp_path)
    monkeypatch.setattr(caller, "person_refusal", lambda what: "`sender 1` is a background session; only a person")
    with pytest.raises(MoveRefused, match="background session"):
        delivery.approve(ledger, "feat/x")
    assert not ledger.rows()["r1"].approved


def test_an_approval_of_another_revision_does_not_count(tmp_path):
    """An approval names the revision the person approved; work that moved and was read again needs another."""
    from flotilla.ledger.actor import Actor
    root, ledger = human_world(tmp_path)
    with ledger.session() as session:
        row = session.need_open_row("feat/x")
        session.append(Actor("the person", None, "person"), row.id, "approve", row.state,
                       fields={"approved": "0" * 40})
    with pytest.raises(MoveRefused, match="flotilla work approve feat/x"):
        delivery.queue(ledger, actor(ledger, "sender 1"), "feat/x")


def test_where_the_sender_authorizes_no_approval_is_asked(tmp_path):
    root = repo_with_origin(tmp_path)
    ledger = make_ledger(root, tmp_path / "state", profile={**PROFILE, "flow": {"mode": "direct",
                                                                                   "merge_authorized_by": "sender"}})
    drive(root, ledger, "feat/x")
    assert delivery.queue(ledger, actor(ledger, "sender 1"), "feat/x").state == "queued"


def test_where_a_human_authorizes_merges_land_counts_only_approved_work(tmp_path):
    """The sender merges A (approved) and B (accepted, never approved) and lands A: B's commits counted as read in
    B, so B reached trunk with no approval (review of the approve move)."""
    from flotilla.ledger import batch
    root, ledger = human_world(tmp_path)
    drive(root, ledger, "feat/y")
    delivery.approve(ledger, "feat/x")
    merge(root, "feat/x")
    merge(root, "feat/y")
    assert batch.unaccounted(ledger, ledger.rows(), "main") != []   # feat/y's work rides unapproved
    delivery.approve(ledger, "feat/y")
    assert batch.unaccounted(ledger, ledger.rows(), "main") == []


def test_an_approved_field_written_by_any_other_move_is_not_an_approval(tmp_path):
    """The ledger is a file every session can append to: `approved` counts only from an approve the person made."""
    from flotilla.ledger.actor import Actor
    root, ledger = human_world(tmp_path)
    with ledger.session() as session:
        row = session.need_open_row("feat/x")
        session.append(Actor("sender 1", None, "census"), row.id, "wait", row.state,
                       fields={"approved": row.verdict, "waiting_on": "x"})
    assert ledger.rows()["r1"].approved == ""


def test_where_a_human_authorizes_merges_offledger_is_the_persons(tmp_path, monkeypatch):
    from flotilla.core import caller
    from flotilla.ledger import outside
    root, ledger = human_world(tmp_path)
    monkeypatch.setattr(caller, "person_refusal", lambda what: "`sender 1` is a background session")
    merged = merge(root, "feat/x")
    git(root, "push", "-q", "origin", "main")   # pushed outside the ledger, as the review's scenario does
    with pytest.raises(MoveRefused, match="background session"):
        outside.offledger(ledger, actor(ledger, "sender 1"), "feat/x", merge=merged, witness="review session 1")


def test_work_with_no_review_is_approved_at_its_tip(tmp_path):
    from flotilla.ledger import core, handover
    profile = {**HUMAN, "review": {"depth": "none"}}
    root = repo_with_origin(tmp_path)
    ledger = make_ledger(root, tmp_path / "state", profile=profile)
    branch(root, "feat/z", "work")
    core.claim(ledger, actor(ledger, "main session 1"), "feat/z")
    row = delivery.approve(ledger, "feat/z")
    assert row.approved and row.approved == git(root, "rev-parse", "feat/z")


def test_a_vouch_after_the_approval_is_not_under_it(tmp_path):
    """A reader could vouch a commit onto a row the person had already approved, and it rode under that approval."""
    from flotilla.ledger import batch, outside
    root, ledger = human_world(tmp_path)
    delivery.approve(ledger, "feat/x")
    merge(root, "feat/x")
    extra = commit(root, "a change made in the batch", "extra.txt")
    outside.vouch(ledger, actor(ledger, "review session 1"), "feat/x", commit=extra)
    assert extra in (batch.unaccounted(ledger, ledger.rows(), "main") or [])


def test_the_queue_refusal_quotes_a_branch_name_an_older_flotilla_filed(tmp_path, monkeypatch):
    """Final review of the scan fixes, minor: a row filed before branch names were checked can still carry `$(...)`;
    the refusal that tells the person what to type quotes it for the shell."""
    import re
    from flotilla.ledger import core
    root = repo_with_origin(tmp_path)
    ledger = make_ledger(root, tmp_path / "state", profile=HUMAN)
    with monkeypatch.context() as older:
        older.setattr(core, "SAFE_BRANCH", re.compile(r".+"))
        drive(root, ledger, "feat/x$(id)")
    with pytest.raises(MoveRefused) as refused:
        delivery.queue(ledger, actor(ledger, "sender 1"), "feat/x$(id)")
    assert "work approve 'feat/x$(id)'" in str(refused.value)


def test_a_pr_closed_without_merging_names_only_legal_moves(pr_world):
    root, ledger, answers = pr_world
    row = drive(root, ledger)
    answers["pr"] = open_pr("feat/x", row.tip)
    delivery.queue(ledger, actor(ledger, SENDER), "feat/x", pr=12)
    answers["pr"] = {"state": "CLOSED", "headRefOid": row.tip}
    with pytest.raises(MoveRefused) as caught:
        delivery.ship(ledger, actor(ledger, SENDER), "feat/x")
    text = str(caught.value)
    assert "queue it again" not in text and "flotilla work release feat/x" in text and "claim" in text


def test_a_failed_fetch_is_said_not_hidden_behind_fetched(direct):
    root, ledger = direct
    queued(root, ledger)
    merge(root, "feat/x")
    delivery.land(ledger, actor(ledger, SENDER), "feat/x")
    git(root, "remote", "set-url", "origin", str(root.parent / "no-such-origin"))
    with pytest.raises(NotYet) as caught:
        delivery.ship(ledger, actor(ledger, SENDER), "feat/x")
    assert "(fetched)" not in str(caught.value) and "fetch failed" in str(caught.value)


def test_a_commit_git_cannot_place_is_unknown_not_absent(pr_world):
    root, ledger, answers = pr_world
    row = drive(root, ledger)
    answers["pr"] = open_pr("feat/x", row.tip)
    delivery.queue(ledger, actor(ledger, SENDER), "feat/x", pr=12)
    answers["pr"] = {"state": "MERGED", "headRefOid": row.tip, "mergeCommit": {"oid": "f" * 40}}
    with pytest.raises(NotYet) as caught:
        delivery.ship(ledger, actor(ledger, SENDER), "feat/x")
    assert "could not tell whether" in str(caught.value) and "does not have" not in str(caught.value)
