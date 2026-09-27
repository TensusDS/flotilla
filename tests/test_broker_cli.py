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
    return commands.run_permit_command(args, clock=clock or (lambda: 1100.0), sleep=sleep or (lambda s: None))


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
    assert present.for_the_session(asked) == "switch the session to acceptEdits mode; let it work in /w"
