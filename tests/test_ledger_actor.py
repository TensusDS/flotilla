import pytest

from flotilla.core.census import CensusUnavailable, Session
from flotilla.ledger.actor import Actor, require_may, resolve_actor
from flotilla.ledger.errors import ActorUnknown, MoveRefused
from flotilla.posts import TEMPLATE_DIR, load_post

POSTS = {p.name: p for p in (load_post(path) for path in sorted(TEMPLATE_DIR.glob("*.md")))}


def session(name, pid):
    return Session(name=name, session_id=name, kind="background", pid=pid, short_id=None, status="busy",
                   state="working", cwd="/", started_at_ms=None)


def test_as_name_resolves_its_post():
    found = resolve_actor(POSTS, as_name="review session 2", census=lambda: [])
    assert found.post.name == "reviewer" and found.via == "as"


def test_the_census_finds_the_calling_session():
    found = resolve_actor(POSTS, census=lambda: [session("main session 4", 20)],
                          parent_of={40: 30, 30: 20, 20: 1}.get, start_pid=40)
    assert (found.name, found.via, found.post.name) == ("main session 4", "census", "main")


def test_an_unreachable_census_is_unknown_not_a_guess():
    def broken():
        raise CensusUnavailable("`claude` is not on PATH")
    with pytest.raises(ActorUnknown, match="--as"):
        resolve_actor(POSTS, census=broken)


def test_a_process_outside_any_session_is_unknown():
    with pytest.raises(ActorUnknown, match="not inside a Claude Code session"):
        resolve_actor(POSTS, census=lambda: [session("elsewhere", 99)], parent_of={40: 1}.get, start_pid=40)


def test_a_project_without_posts_refuses_every_move():
    with pytest.raises(MoveRefused, match="onboard"):
        require_may(Actor("main session 1", None, "as"), "claim", {})


def test_a_name_matching_no_post_is_refused():
    with pytest.raises(MoveRefused, match="matches no post"):
        require_may(resolve_actor(POSTS, as_name="Max", census=lambda: []), "claim", POSTS)


def test_a_move_the_post_may_not_make_names_who_may():
    with pytest.raises(MoveRefused, match=r"may not `accept`.*reviewer"):
        require_may(resolve_actor(POSTS, as_name="main session 1", census=lambda: []), "accept", POSTS)


def test_an_allowed_move_passes():
    require_may(resolve_actor(POSTS, as_name="review session 1", census=lambda: []), "accept", POSTS)


def test_a_live_session_cannot_act_under_another_name():
    with pytest.raises(ActorUnknown, match="cannot act as"):
        resolve_actor(POSTS, as_name="review session 1", census=lambda: [session("main session 1", 20)],
                      parent_of={40: 20, 20: 1}.get, start_pid=40)


def test_as_records_who_really_called():
    outside = resolve_actor(POSTS, as_name="review session 1", census=lambda: [session("elsewhere", 99)],
                            parent_of={40: 1}.get, start_pid=40)
    assert outside.caller.startswith("none")

    def broken():
        raise CensusUnavailable("`claude` is not on PATH")
    assert resolve_actor(POSTS, as_name="review session 1", census=broken).caller.startswith("unknown")
    same = resolve_actor(POSTS, as_name="main session 1", census=lambda: [session("main session 1", 20)],
                         parent_of={40: 20, 20: 1}.get, start_pid=40)
    assert same.caller == "main session 1"
