import pytest

from flotilla.ledger.errors import MoveRefused
from flotilla.ledger.transitions import moves_from, next_state

PR = {"flow": {"mode": "pr"}, "review": {"depth": "every"}}
DIRECT = {"flow": {"mode": "direct"}, "review": {"depth": "every"}}
LOCAL = {"flow": {"mode": "local"}, "review": {"depth": "every"}}


def test_the_review_path():
    assert next_state("claimed", "hand", PR) == "handed"
    assert next_state("handed", "fix", PR) == "fixing"
    assert next_state("fixing", "hand", PR) == "handed"
    assert next_state("handed", "accept", PR) == "accepted"


def test_pr_mode_ships_without_landing():
    assert "ship" in moves_from("queued", PR) and "land" not in moves_from("queued", PR)


def test_direct_mode_lands_then_ships():
    assert "land" in moves_from("queued", DIRECT) and "ship" not in moves_from("queued", DIRECT)
    assert "ship" in moves_from("landed", DIRECT) and "close" not in moves_from("landed", DIRECT)


def test_without_origin_landed_is_the_end_of_the_road():
    assert "ship" not in moves_from("landed", LOCAL) and "close" in moves_from("landed", LOCAL)


def test_review_every_branch_forbids_queueing_a_claim():
    assert "queue" not in moves_from("claimed", PR)


def test_review_none_queues_a_claim():
    assert "queue" in moves_from("claimed", {**PR, "review": {"depth": "none"}})


def test_review_main_only_depends_on_the_owner_post():
    profile = {**PR, "review": {"depth": "main-only"}}
    assert "queue" in moves_from("claimed", profile, owner_post="minor")
    assert "queue" not in moves_from("claimed", profile, owner_post="main")


def test_a_required_judge_must_walk_before_close():
    assert "close" not in moves_from("shipped", {**PR, "judge": {"required": True}})
    assert "close" in moves_from("shipped", PR)


def test_a_refusal_names_what_is_legal():
    with pytest.raises(MoveRefused, match=r"Legal from here: .*hand"):
        next_state("claimed", "accept", PR)


def test_finished_rows_take_no_moves_not_even_annotations():
    assert moves_from("closed", PR) == {}
    with pytest.raises(MoveRefused, match="finished"):
        next_state("closed", "wait", PR)
