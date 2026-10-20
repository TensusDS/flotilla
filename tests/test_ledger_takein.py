"""Work taken into the ledger from before flotilla (spec 2026-10-19-organize-design, section 5): the row's pinned
post, its adoption point, and the take-in move itself."""

import pytest

from flotilla.ledger import batch, findings
from flotilla.ledger.actor import Actor
from flotilla.ledger.model import Row
from flotilla.ledger.transitions import moves_from
from ledgerkit import PROFILE, commit, git, make_ledger, repo_with_origin

DIRECT = {**PROFILE, "flow": {"mode": "direct"}}
PERSON = Actor("the person", None, "person", "the person")


def ledger_with(tmp_path, review="every", profile=None):
    root = repo_with_origin(tmp_path)
    data = profile or {**PROFILE, "review": {"depth": review}}
    return root, make_ledger(root, tmp_path / "state", profile=data)


def write_row(ledger, **fields):
    """A row as the take-in move writes one, appended directly (the move is tested below)."""
    state = fields.pop("state", "claimed")
    with ledger.session() as s:
        return s.append(PERSON, f"r{len(s.rows) + 1}", "take-in", state, fields=fields)


def test_a_pinned_post_decides_review_whatever_the_owner(tmp_path):
    """main-only: a minor seat's row skips review - unless the row was taken in and pinned to main."""
    _, ledger = ledger_with(tmp_path, review="main-only")
    plain = Row(id="r1", owner="minor session 1", state="claimed")
    pinned = Row(id="r2", owner="minor session 1", state="claimed", judged_as="main")
    assert "queue" in moves_from("claimed", ledger.profile, ledger.owner_post(plain))
    assert ledger.owner_post(pinned) == "main"
    assert "queue" not in moves_from("claimed", ledger.profile, ledger.owner_post(pinned))


def _older_branch_and_a_trunk_commit(root):
    """`feat/old` forks from the first commit; trunk then gains A, made before flotilla, and is pushed."""
    fork = git(root, "rev-parse", "HEAD")
    git(root, "checkout", "-q", "-b", "feat/old")
    commit(root, "old work", "old.txt")
    git(root, "checkout", "-q", "main")
    before = commit(root, "made before flotilla", "a.txt")
    git(root, "push", "-q", "origin", "main")
    return fork, before


def test_trunk_commits_before_the_adoption_point_are_not_direct_commits(tmp_path):
    root, ledger = ledger_with(tmp_path, profile=DIRECT)
    fork, before = _older_branch_and_a_trunk_commit(root)
    write_row(ledger, branch="feat/old", owner="(taken in)", base=fork, adopted_at=before)
    named = [item["sha"] for item in findings.findings(ledger) if item["kind"] == "direct_commit"]
    assert before not in named


def test_a_far_back_base_does_not_vouch_for_trunk_merged_in_below_the_adoption_point(tmp_path):
    root, ledger = ledger_with(tmp_path, profile=DIRECT)
    fork, before = _older_branch_and_a_trunk_commit(root)
    git(root, "checkout", "-q", "feat/old")
    git(root, "-c", "user.email=t@example.invalid", "-c", "user.name=t", "merge", "-q", "--no-ff", "-m", "merge trunk",
        "main")
    tip = git(root, "rev-parse", "HEAD")
    git(root, "checkout", "-q", "main")
    write_row(ledger, state="accepted", branch="feat/old", owner="(taken in)", base=fork, adopted_at=before,
              verdict=tip, reader="review session 1")
    assert before not in batch.Accounting(ledger, ledger.rows()).read
    assert tip in batch.Accounting(ledger, ledger.rows()).read


def test_the_new_fields_fold_into_the_row(tmp_path):
    _, ledger = ledger_with(tmp_path)
    row = write_row(ledger, branch="feat/a", owner="(taken in)", base="b" * 40, judged_as="main",
                    adopted_at="a" * 40, retarget="main", closes=[42, 43])
    assert (row.judged_as, row.adopted_at, row.retarget, row.closes) == ("main", "a" * 40, "main", [42, 43])


@pytest.mark.parametrize("name", ["judged_as", "adopted_at", "retarget", "closes"])
def test_every_new_field_is_a_row_field(name):
    from flotilla.ledger.model import ROW_FIELDS
    assert name in ROW_FIELDS


# --- the take-in move --------------------------------------------------------------------------------------------

from flotilla.core import caller  # noqa: E402
from flotilla.ledger import handover, reading, steering, takein, views  # noqa: E402
from flotilla.ledger.errors import MoveRefused  # noqa: E402
from ledgerkit import actor  # noqa: E402


class World:
    def __init__(self, root, ledger, fork):
        self.root, self.ledger, self.fork = root, ledger, fork


@pytest.fixture()
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(caller, "person_refusal", lambda what: "")
    root = repo_with_origin(tmp_path)
    fork = git(root, "rev-parse", "HEAD")
    git(root, "checkout", "-q", "-b", "feat/a")
    commit(root, "work from before flotilla", "a.txt")
    git(root, "checkout", "-q", "main")
    return World(root, make_ledger(root, tmp_path / "state"), fork)


def test_take_in_writes_a_claimed_row_under_the_reserved_owner(world):
    row = takein.take_in(world.ledger, "feat/a", base=world.fork, pr=42, retarget="main", closes=[43])
    assert (row.state, row.owner, row.pr, row.base) == ("claimed", takein.TAKEN_IN, "42", world.fork)
    assert (row.judged_as, row.retarget, row.closes) == ("main", "main", [43])
    assert row.adopted_at == git(world.root, "rev-parse", "origin/main")
    assert row.history[-1]["move"] == "take-in"


def test_take_in_is_the_persons(world, monkeypatch):
    monkeypatch.setattr(caller, "person_refusal", lambda what: "`x` is a background session")
    with pytest.raises(MoveRefused, match="background"):
        takein.take_in(world.ledger, "feat/a", base=world.fork)
    assert world.ledger.rows() == {}


@pytest.mark.parametrize("branch, base", [("feat/a", ""), ("feat/$(x)", "f" * 40)])
def test_take_in_refuses_an_empty_base_and_an_unsafe_branch(world, branch, base):
    with pytest.raises(MoveRefused):
        takein.take_in(world.ledger, branch, base=base or "")
    assert world.ledger.rows() == {}


def test_take_in_refuses_a_branch_an_open_row_holds(world):
    takein.take_in(world.ledger, "feat/a", base=world.fork)
    with pytest.raises(MoveRefused, match="already claimed"):
        takein.take_in(world.ledger, "feat/a", base=world.fork)


def test_the_reserved_owner_is_no_post_and_no_session(world):
    from flotilla.posts import post_for_session
    assert post_for_session(world.ledger.posts, takein.TAKEN_IN) is None
    assert takein.TAKEN_IN not in world.ledger.live_names()


def test_a_taken_in_row_is_orphaned_and_adopted_by_a_seat(world):
    takein.take_in(world.ledger, "feat/a", base=world.fork)
    entries = views.roster(world.ledger.rows(), world.ledger.profile, world.ledger.live_names())
    assert {entry["who"]: entry["state"] for entry in entries}[takein.TAKEN_IN] == "orphaned"
    row = steering.adopt(world.ledger, actor(world.ledger, "orchestrator 1"), "feat/a", to="main session 1")
    assert (row.owner, row.adopted_from, row.judged_as) == ("main session 1", takein.TAKEN_IN, "main")


def test_a_returned_taken_in_row_reaches_a_seat(world):
    takein.take_in(world.ledger, "feat/a", base=world.fork)
    steering.adopt(world.ledger, actor(world.ledger, "orchestrator 1"), "feat/a", to="minor session 1")
    handover.hand(world.ledger, actor(world.ledger, "minor session 1"), "feat/a")
    reading.take(world.ledger, actor(world.ledger, "review session 1"), "feat/a")
    row = reading.fix(world.ledger, actor(world.ledger, "review session 1"), "feat/a", why="name the file")
    assert (row.state, row.owner, row.judged_as) == ("fixing", "minor session 1", "main")
