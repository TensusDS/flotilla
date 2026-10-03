from flotilla.core import caller
from flotilla.core.census import Session


def session(name, kind, pid):
    return Session(name=name, session_id=name, kind=kind, pid=pid, short_id=name[:6], status=None, state=None, cwd="",
                   started_at_ms=None)


def test_every_session_above_the_caller_is_found_nearest_first():
    sessions = [session("main session 1", "background", 10), session("nested", "interactive", 20)]
    parents = {300: 20, 20: 15, 15: 10, 10: 1}
    found = caller.sessions_above(sessions, parent_of=parents.get, start_pid=300)
    assert [item.name for item in found] == ["nested", "main session 1"]
    assert caller.sessions_above(sessions, parent_of={400: 1}.get, start_pid=400) == []


def test_only_an_interactive_session_with_no_background_session_above_it_is_a_person(monkeypatch):
    def chain(*sessions):
        monkeypatch.setattr(caller, "calling_sessions", lambda: list(sessions))
    chain(session("other-project-13", "interactive", 20))
    assert caller.person_refusal("does it") == ""
    chain(session("nested", "interactive", 20), session("main session 1", "background", 10))
    assert "`main session 1` is a background session" in caller.person_refusal("does it")
    chain(session("odd", "", 20))
    assert "is not a session a person works in" in caller.person_refusal("does it")


def test_a_session_of_any_kind_but_interactive_anywhere_above_is_refused(monkeypatch):
    monkeypatch.setattr(caller, "calling_sessions", lambda: [session("nested", "interactive", 20),
                                                             session("odd", "", 15)])
    assert "is not a session a person works in" in caller.person_refusal("does it")
