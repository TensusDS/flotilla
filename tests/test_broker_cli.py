import argparse

from flotilla import cli
from flotilla.broker import commands, present, queue
from flotilla.core import repo
from guardkit import plain_repo
from watchkit import onboarded

KEYED = {}


def world(tmp_path, monkeypatch):
    root = plain_repo(tmp_path)
    onboarded(root)
    monkeypatch.setenv("FLOTILLA_STATE_DIR", str(tmp_path / "state"))
    return root, tmp_path / "state", repo.identify(root).key


def ask(state, key, now, session="main session 1", command="touch x.txt"):
    return queue.ask(state, key, session=session, session_id="s", tool="Bash", tool_input={"command": command},
                     suggestions=[], wait=540, now=now)


def run(root, *argv, clock=None, sleep=None):
    args = cli.build_parser().parse_args(["permit", *argv, "--root", str(root)])
    return commands.run_permit_command(args, clock=clock or (lambda: 1100.0), sleep=sleep or (lambda s: None),
                                      caller=lambda root: (None, ""))


def test_next_shows_the_oldest_question_with_its_three_answers(tmp_path, monkeypatch, capsys):
    root, state, key = world(tmp_path, monkeypatch)
    asked = ask(state, key, 1000)
    assert run(root, "next") == 0
    out = capsys.readouterr().out
    assert "main session 1 asks: Bash touch x.txt" in out and f"permit answer {asked.id} session" in out
    assert "allow Bash(touch x.txt)" in out


def test_next_with_nothing_waiting_exits_3(tmp_path, monkeypatch, capsys):
    root, _, _ = world(tmp_path, monkeypatch)
    assert run(root, "next") == 3 and "no question waits" in capsys.readouterr().out


def test_next_waits_for_a_question_to_appear(tmp_path, monkeypatch, capsys):
    root, state, key = world(tmp_path, monkeypatch)
    now = {"t": 1100.0}

    def sleep(seconds):
        now["t"] += seconds
        if now["t"] >= 1103:
            ask(state, key, 1103)
    assert run(root, "next", "--wait", "60", clock=lambda: now["t"], sleep=sleep) == 0
    assert "asks: Bash touch x.txt" in capsys.readouterr().out


def test_answer_records_and_a_second_answer_is_refused(tmp_path, monkeypatch, capsys):
    root, state, key = world(tmp_path, monkeypatch)
    asked = ask(state, key, 1000)
    assert run(root, "answer", asked.id, "deny", "--why", "not today") == 0
    assert queue.answer_of(state, key, asked.id)["why"] == "not today"
    assert run(root, "answer", asked.id, "allow") == 2 and "already closed" in capsys.readouterr().out


def test_a_second_question_waits_its_turn(tmp_path, monkeypatch, capsys):
    root, state, key = world(tmp_path, monkeypatch)
    first = ask(state, key, 1000)
    second = ask(state, key, 1010, session="minor session 1", command="touch y.txt")
    run(root, "next")
    assert f"question {first.id}" in capsys.readouterr().out
    run(root, "answer", first.id, "allow")
    capsys.readouterr()
    run(root, "next")
    assert f"question {second.id}" in capsys.readouterr().out


def test_list_names_every_live_question(tmp_path, monkeypatch, capsys):
    root, state, key = world(tmp_path, monkeypatch)
    ask(state, key, 1000)
    ask(state, key, 1010, session="minor session 1", command="touch y.txt")
    assert run(root, "list") == 0
    out = capsys.readouterr().out
    assert "main session 1" in out and "minor session 1" in out


def test_for_the_session_is_said_in_words():
    asked = queue.Question("1", 0, 540, 1, "m", "s", "Bash", {"command": "touch x"},
                           [{"type": "setMode", "mode": "acceptEdits"},
                            {"type": "addDirectories", "directories": ["/w"]}])
    assert present.for_the_session(asked) == "allow Bash(touch x); let it work in /w"   # never the mode


def test_a_worker_session_cannot_answer(tmp_path, monkeypatch, capsys):
    root, state, key = world(tmp_path, monkeypatch)
    asked = ask(state, key, 1000)
    args = cli.build_parser().parse_args(["permit", "answer", asked.id, "allow", "--root", str(root)])
    code = commands.run_permit_command(args, clock=lambda: 1100.0, caller=lambda root: ("main session 2", "main"))
    assert code == 2 and "only the orchestrator" in capsys.readouterr().out
    assert queue.answer_of(state, key, asked.id) is None


def test_the_orchestrator_can_answer(tmp_path, monkeypatch):
    root, state, key = world(tmp_path, monkeypatch)
    asked = ask(state, key, 1000)
    args = cli.build_parser().parse_args(["permit", "answer", asked.id, "allow", "--root", str(root)])
    assert commands.run_permit_command(args, clock=lambda: 1100.0,
                                       caller=lambda root: ("orchestrator 1", "orchestrator")) == 0


def test_a_wildcard_command_is_offered_once_only(tmp_path):
    asked = queue.Question("1", 0, 540, 1, "m", "s", "Bash", {"command": "rm -rf build/*"}, [])
    assert "once only" in present.for_the_session(asked)


def test_an_answer_from_outside_any_session_needs_a_terminal(tmp_path, monkeypatch, capsys):
    """A process a session detached (`setsid`) reaches pid 1 without passing any session: without a terminal it is
    not a person, whatever its parent chain says (security review F9)."""
    from flotilla.core import caller
    root, state, key = world(tmp_path, monkeypatch)
    asked = ask(state, key, 1000)
    monkeypatch.setattr(caller, "calling_sessions", lambda: [])
    monkeypatch.setattr(caller, "has_terminal", lambda: False)
    assert run(root, "answer", asked.id, "allow") == 2
    assert "a terminal" in capsys.readouterr().out
    assert [item.id for item in queue.live(state, key, now=1100.0)] == [asked.id]
    monkeypatch.setattr(caller, "has_terminal", lambda: True)
    assert run(root, "answer", asked.id, "allow") == 0
