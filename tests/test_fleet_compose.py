import pytest

from flotilla.fleet import compose
from flotilla.posts import TEMPLATE_DIR, load_post

POSTS = {p.name: p for p in (load_post(path) for path in TEMPLATE_DIR.glob("*.md"))}


def test_counts_name_posts_and_accept_the_profile_alias():
    assert compose.normalise({"review": 2, "main": 1, "minor": 0}, POSTS) == {"reviewer": 2, "main": 1}


def test_a_count_for_a_post_the_project_lacks_is_refused():
    with pytest.raises(compose.CompositionError, match="no post `tester`"):
        compose.normalise({"tester": 1}, POSTS)
    with pytest.raises(compose.CompositionError, match="whole number"):
        compose.normalise({"main": -1}, POSTS)


def test_acceptors_are_raised_before_producers_and_custom_posts_last():
    counts = {"main": 1, "reviewer": 2, "sender": 1, "zeta": 1, "alpha": 1}
    assert compose.raise_order(counts) == ["sender", "reviewer", "main", "alpha", "zeta"]


def test_a_second_sender_is_refused():
    assert compose.one_copy_problems({"sender": 1}, POSTS, {"sender": 1})
    assert compose.one_copy_problems({"sender": 2}, POSTS, {})
    assert not compose.one_copy_problems({"sender": 1}, POSTS, {})


def test_a_fleet_above_three_without_orchestrator_or_sender_is_warned():
    found = compose.warnings({"main": 2, "reviewer": 2}, POSTS, {})
    assert any("orchestrator" in line for line in found) and any("sender" in line for line in found)
    assert compose.warnings({"main": 1, "reviewer": 1}, POSTS, {}) == []


def test_the_orchestrator_is_raised_last():
    order = compose.raise_order({"orchestrator": 1, "reviewer": 2, "minor": 1, "alpha": 1})
    assert order[-1] == "orchestrator" and order[:3] == ["reviewer", "minor", "alpha"]
