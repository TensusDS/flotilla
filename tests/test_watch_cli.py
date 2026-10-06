import argparse

from flotilla import cli
from flotilla.watch.commands import run_watch_command
from watchkit import NOW, context, onboarded, row, rows, sess

HANDED = rows(row(state="handed", reader="review session 1"))


def args(tmp_path, once=True):
    return argparse.Namespace(once=once, wait=0.0, interval=20.0, confirm=5.0, root=str(tmp_path))


def run(tmp_path, ctx, capsys, once=True):
    onboarded(tmp_path)
    code = run_watch_command(args(tmp_path, once), gather=lambda root, sid: ctx, now=NOW, sleep=lambda s: None)
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
    assert code == 2 and "--once" in out and "--wait" in out


def test_a_project_not_onboarded_exits_2(tmp_path, capsys):
    code = run_watch_command(args(tmp_path), gather=lambda root, sid: None, now=NOW)
    assert code == 2 and "not onboarded" in capsys.readouterr().out


def test_the_cli_parses_watch():
    parsed = cli.build_parser().parse_args(["watch", "--once", "--root", "/x"])
    assert parsed.command == "watch" and parsed.once and parsed.root == "/x"


def waiting(seconds=60.0, interval=20.0):
    return argparse.Namespace(once=False, wait=seconds, interval=interval, confirm=5.0, root=None)


def run_wait(tmp_path, capsys, contexts, seconds=60.0):
    onboarded(tmp_path)
    given = iter(contexts)
    moments = iter(range(0, 10_000, 20))
    ns = waiting(seconds)
    ns.root = str(tmp_path)
    code = run_watch_command(ns, gather=lambda root, sid: next(given), now=NOW, sleep=lambda s: None,
                             clock=lambda: float(next(moments)))
    return code, capsys.readouterr().out


QUIET = [sess("main session 1", state="working")]
DROPPED = [sess("review session 1"), sess("main session 1", state="working")]


def test_wait_returns_when_something_new_needs_attention(tmp_path, capsys):
    calm = context(tmp_path, me=None, sessions=QUIET)
    news = context(tmp_path, me=None, sessions=DROPPED, rows_=HANDED)
    code, out = run_wait(tmp_path, capsys, [calm, calm, news, news])   # the last: the confirming sample
    assert code == 0 and "attention (new):" in out and "review session 1 holds the move" in out


def test_what_was_there_at_the_start_does_not_wake_it(tmp_path, capsys):
    same = context(tmp_path, me=None, sessions=DROPPED, rows_=HANDED)
    code, out = run_wait(tmp_path, capsys, [same] * 10)
    assert code == 0 and "nothing new in 60 s" in out


def test_a_census_lost_mid_wait_exits_2(tmp_path, capsys):
    calm = context(tmp_path, me=None, sessions=QUIET)
    lost = context(tmp_path, me=None, census_error="`claude` is not on PATH")
    code, out = run_wait(tmp_path, capsys, [calm, lost])
    assert code == 2 and "could not be asked" in out and "nothing new" not in out


def test_the_cli_parses_watch_wait():
    parsed = cli.build_parser().parse_args(["watch", "--wait", "3600"])
    assert parsed.wait == 3600 and parsed.interval == 20 and not parsed.once


def dropping(tmp_path, state, status=None):
    return context(tmp_path, me=None, rows_=HANDED,
                   sessions=[sess("review session 1", state=state, status=status),
                             sess("main session 1", state="working")])


def test_a_ball_dropped_again_after_work_resumed_wakes_it(tmp_path, capsys):
    code, out = run_wait(tmp_path, capsys, [dropping(tmp_path, "blocked"), dropping(tmp_path, "working"),
                                            dropping(tmp_path, "blocked"), dropping(tmp_path, "blocked")])
    assert code == 0 and "review session 1 holds the move" in out


def test_a_census_word_that_flickers_is_not_news(tmp_path, capsys):
    code, out = run_wait(tmp_path, capsys, [dropping(tmp_path, "blocked"), dropping(tmp_path, "done", "idle"),
                                            dropping(tmp_path, "blocked")])
    assert code == 0 and "nothing new" in out


def test_a_question_waiting_on_the_person_needs_attention(tmp_path, capsys):
    asking = rows(row(state="handed", reader="review session 1", waiting_on="the person",
                      note="which colour should the snake be?"))
    code, out = run(tmp_path, context(tmp_path, me=None, sessions=[sess("review session 1")], rows_=asking), capsys)
    assert code == 1 and "waits on the person: which colour should the snake be?" in out


def test_once_does_not_report_a_ball_the_second_sample_clears(tmp_path, capsys):
    onboarded(tmp_path)
    given = iter([dropping(tmp_path, "blocked"), dropping(tmp_path, "working")])
    ns = argparse.Namespace(once=True, wait=0.0, interval=20.0, confirm=5.0, root=str(tmp_path))
    code = run_watch_command(ns, gather=lambda root, sid: next(given), now=NOW, sleep=lambda s: None)
    out = capsys.readouterr().out
    assert code == 0 and "attention: none" in out


def test_once_reports_a_ball_both_samples_agree_on(tmp_path, capsys):
    onboarded(tmp_path)
    given = iter([dropping(tmp_path, "blocked"), dropping(tmp_path, "blocked")])
    ns = argparse.Namespace(once=True, wait=0.0, interval=20.0, confirm=5.0, root=str(tmp_path))
    code = run_watch_command(ns, gather=lambda root, sid: next(given), now=NOW, sleep=lambda s: None)
    assert code == 1 and "review session 1 holds the move" in capsys.readouterr().out


def test_wait_does_not_wake_for_a_ball_the_second_sample_clears(tmp_path, capsys):
    calm = context(tmp_path, me=None, sessions=QUIET)
    code, out = run_wait(tmp_path, capsys, [calm, dropping(tmp_path, "blocked"), dropping(tmp_path, "working"),
                                            calm, calm])
    assert code == 0 and "nothing new" in out


def test_once_says_when_the_confirming_sample_could_not_be_taken(tmp_path, capsys):
    onboarded(tmp_path)
    lost = context(tmp_path, me=None, census_error="`claude` is not on PATH")
    given = iter([dropping(tmp_path, "blocked"), lost])
    ns = argparse.Namespace(once=True, wait=0.0, interval=20.0, confirm=5.0, root=str(tmp_path))
    code = run_watch_command(ns, gather=lambda root, sid: next(given), now=NOW, sleep=lambda s: None)
    assert code == 2 and "second sample" in capsys.readouterr().out


def test_a_real_drop_after_a_flicker_wakes_it(tmp_path, capsys):
    busy = dropping(tmp_path, "working")
    idle = dropping(tmp_path, "blocked")
    code, out = run_wait(tmp_path, capsys, [busy, idle, busy, idle, idle, idle], seconds=100.0)
    assert code == 0 and "review session 1 holds the move" in out


def test_a_wait_that_woke_on_news_exits_0_so_the_wake_is_not_read_as_a_failure(tmp_path, capsys):
    """Twosuns orchestrator, 0.7.16: every wake exited 1, and Claude Code showed each one as a failed task. The news
    is in the output; the exit says only whether the watch could ask (2) or not."""
    calm = context(tmp_path, me=None, sessions=QUIET)
    news = context(tmp_path, me=None, sessions=[sess("review session 1"), sess("main session 1", state="working")],
                   rows_=HANDED)
    code, out = run_wait(tmp_path, capsys, [calm, news, news])
    assert code == 0 and out.splitlines()[1] == "attention (new):"
