import argparse

from flotilla import cli
from flotilla.watch.commands import run_watch_command
from watchkit import NOW, context, onboarded, row, rows, sess

HANDED = rows(row(state="handed", reader="review session 1"))


def args(tmp_path, once=True):
    return argparse.Namespace(once=once, root=str(tmp_path))


def run(tmp_path, ctx, capsys, once=True):
    onboarded(tmp_path)
    code = run_watch_command(args(tmp_path, once), gather=lambda root, sid: ctx, now=NOW)
    return code, capsys.readouterr().out


def test_nothing_needs_attention_exits_0(tmp_path, capsys):
    code, out = run(tmp_path, context(tmp_path, me=None, sessions=[sess("main session 1")]), capsys)
    assert code == 0 and "attention: none" in out and "census: 1 live session(s)" in out


def test_attention_exits_1(tmp_path, capsys):
    fleet_now = [sess("review session 1"), sess("main session 1", state="working")]
    code, out = run(tmp_path, context(tmp_path, me=None, sessions=fleet_now, rows_=HANDED), capsys)
    assert code == 1 and "review session 1 holds the move" in out


def test_census_down_exits_2_and_never_says_none(tmp_path, capsys):
    code, out = run(tmp_path, context(tmp_path, me=None, census_error="`claude` is not on PATH"), capsys)
    assert code == 2 and "could not be asked" in out and "none" not in out


def test_without_once_it_refuses(tmp_path, capsys):
    code, out = run(tmp_path, context(tmp_path, me=None), capsys, once=False)
    assert code == 2 and "--once" in out


def test_a_project_not_onboarded_exits_2(tmp_path, capsys):
    code = run_watch_command(args(tmp_path), gather=lambda root, sid: None, now=NOW)
    assert code == 2 and "not onboarded" in capsys.readouterr().out


def test_the_cli_parses_watch():
    parsed = cli.build_parser().parse_args(["watch", "--once", "--root", "/x"])
    assert parsed.command == "watch" and parsed.once and parsed.root == "/x"
