import datetime as dt
import json
import subprocess

from flotilla.core import releases

STABLE = "a" * 40
LISTING = "\n".join([
    f"{'1' * 40}\trefs/tags/v0.9.0",
    f"{'2' * 40}\trefs/tags/v0.28.1",
    f"{'3' * 40}\trefs/tags/v0.10.2",
    f"{'4' * 40}\trefs/tags/not-a-release",
    f"{'5' * 40}\trefs/tags/v1.0.0-rc1",
    f"{STABLE}\trefs/heads/stable",
]) + "\n"


def fake(stdout=LISTING, code=0, calls=None, raises=None):
    def run(argv, **kw):
        if calls is not None:
            calls.append((argv, kw))
        if raises:
            raise raises
        return subprocess.CompletedProcess(argv, code, stdout, "boom" if code else "")
    return run


def test_newest_is_by_version_not_by_text_and_stable_is_its_commit():
    found = releases.ask(fake())
    assert found.newest == "0.28.1"
    assert found.stable == STABLE
    assert found.error == ""


def test_ask_names_the_repository_and_never_prompts():
    calls = []
    releases.ask(fake(calls=calls))
    argv, kw = calls[0]
    assert argv[:2] == ["git", "ls-remote"]
    assert "https://github.com/TensusDS/flotilla" in argv
    assert kw["env"]["GIT_TERMINAL_PROMPT"] == "0"
    assert kw["timeout"] == 5.0


def test_a_failed_or_slow_ask_gives_no_newest_and_says_why():
    failed = releases.ask(fake(code=128))
    assert failed.newest is None and "boom" in failed.error
    slow = releases.ask(fake(raises=subprocess.TimeoutExpired("git", 5)))
    assert slow.newest is None and slow.error


def test_cached_reuses_a_fresh_answer_and_asks_again_when_old(tmp_path):
    now = dt.datetime(2026, 11, 4, 12, tzinfo=dt.timezone.utc)
    calls = []
    first = releases.cached(tmp_path, fake(calls=calls), now=now)
    again = releases.cached(tmp_path, fake(calls=calls), now=now + dt.timedelta(hours=23))
    assert first.newest == again.newest == "0.28.1" and len(calls) == 1
    releases.cached(tmp_path, fake(calls=calls), now=now + dt.timedelta(hours=25))
    assert len(calls) == 2
    releases.cached(tmp_path, fake(calls=calls), now=now + dt.timedelta(hours=25), fresh=True)
    assert len(calls) == 3


def test_a_failed_ask_is_not_kept(tmp_path):
    now = dt.datetime(2026, 11, 4, 12, tzinfo=dt.timezone.utc)
    calls = []
    releases.cached(tmp_path, fake(code=128, calls=calls), now=now)
    found = releases.cached(tmp_path, fake(calls=calls), now=now + dt.timedelta(minutes=1))
    assert found.newest == "0.28.1" and len(calls) == 2


def test_a_corrupt_cache_is_asked_again(tmp_path):
    (tmp_path / releases.CACHE).write_text("{not json", encoding="utf-8")
    now = dt.datetime(2026, 11, 4, 12, tzinfo=dt.timezone.utc)
    assert releases.cached(tmp_path, fake(), now=now).newest == "0.28.1"
    assert json.loads((tmp_path / releases.CACHE).read_text(encoding="utf-8"))["newest"] == "0.28.1"
