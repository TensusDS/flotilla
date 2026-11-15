"""A seat on a borrowed model (fleet model design 5.3, plan release 3)."""

import json

import pytest

from flotilla.core.storage import LocalLogStore
from flotilla.fleet import borrow, helpers, launch
from flotilla.fleet.model import borrowed, recorded
from flotilla.ledger import core
from flotilla.ledger.errors import MoveRefused
from fleetkit import FakeClaude
from ledgerkit import PROFILE, actor, branch, commit, git, make_ledger, repo_with_origin

OWNER = "main session 1"


@pytest.fixture(autouse=True)
def ready_checkout(monkeypatch, tmp_path):
    from flotilla.core import claude_state
    monkeypatch.setattr(claude_state, "setup_problems", lambda main, **kw: ([], []))
    monkeypatch.setenv("FLOTILLA_STATE_DIR", str(tmp_path / "state"))


def world(tmp_path):
    fake = FakeClaude()
    root = repo_with_origin(tmp_path)
    ledger = make_ledger(root, tmp_path / "state", profile={**PROFILE, "permissions": {"mode": "auto"}},
                         run=fake, census=fake.census)
    tip = branch(root, "feat/x", "the owner's work")
    git(root, "checkout", "-q", "main")
    row = core.claim(ledger, actor(ledger, OWNER), "feat/x")
    return root, ledger, fake, row, tip


def lend(ledger, fake, for_task="feat/x", model="fable", brief="find why the storm test flakes", **more):
    return borrow.raise_borrowed(ledger, "main", model=model, for_task=for_task, brief=brief, by="orchestrator 1",
                                 census=fake.census, store=LocalLogStore(ledger.state_dir / "fleet"),
                                 caller="spawn by orchestrator 1", wait=0.1, poll=0.05, sleep=lambda s: None, **more)


def settings_of(command):
    return json.loads(command[command.index("--settings") + 1])


def test_borrowed_seat_records_and_caps_subagents(tmp_path):
    root, ledger, fake, row, tip = world(tmp_path)
    raised, warnings = lend(ledger, fake)
    command = fake.launched[-1]
    assert command[command.index("--model") + 1] == "fable"
    assert settings_of(command)["env"] == {"CLAUDE_CODE_SUBAGENT_MODEL": "opus",
                                           "CLAUDE_CODE_SUBAGENT_MODEL_FORCE": "1"}
    seat_row = next(r for r in ledger.rows().values() if r.owner == raised.seat.name)
    assert recorded(seat_row) == "fable"
    assert borrowed(seat_row) == {"for": "feat/x", "row": row.id, "by": "orchestrator 1"}
    assert git(raised.seat.tree, "rev-parse", "HEAD") == tip   # cut from the row's tip, not trunk
    prompt = command[-1]
    assert "find why the storm test flakes" in prompt and "on fable - not the fleet's model, opus" in prompt
    assert "flotilla helper done" in prompt and "you own no row" in prompt
    assert any("version could not be read" in line for line in warnings)   # the fake answers no version


def test_borrowed_seat_owns_no_row(tmp_path):
    root, ledger, fake, row, tip = world(tmp_path)
    raised, _ = lend(ledger, fake)
    after = ledger.rows()[row.id]
    assert (after.owner, after.state) == (OWNER, "claimed")
    assert [r.owner for r in ledger.rows().values() if r.is_open and r.state != "reserved"] == [OWNER]


def test_a_question_with_no_row_is_cut_from_trunk(tmp_path):
    root, ledger, fake, row, tip = world(tmp_path)
    raised, _ = lend(ledger, fake, for_task="why does the cache miss on cold start")
    assert git(raised.seat.tree, "rev-parse", "HEAD") == git(root, "rev-parse", "main")
    seat_row = next(r for r in ledger.rows().values() if r.owner == raised.seat.name)
    assert borrowed(seat_row)["row"] == ""
    assert "\"why does the cache miss on cold start\"" in fake.launched[-1][-1]


@pytest.mark.parametrize(("given", "said"), [
    ({"for_task": ""}, "name it with --for"),
    ({"for_task": "a\nb"}, "one line"),
    ({"brief": " "}, "--task"),
    ({"model": "opus"}, "the fleet's model already"),
    ({"model": "claude-opus-5-5"}, "the fleet's model already"),
    ({"model": "inherit"}, "not a model's name"),
])
def test_what_a_borrowed_seat_needs(tmp_path, given, said):
    root, ledger, fake, row, tip = world(tmp_path)
    with pytest.raises(MoveRefused, match=said):
        lend(ledger, fake, **given)
    assert fake.launched == []


def test_helper_of_borrowed_seat_runs_on_default(tmp_path):
    """A seat on a borrowed model owns no work in progress, so it raises no helper; a helper the row's owner
    raises runs on the fleet's model."""
    root, ledger, fake, row, tip = world(tmp_path)
    raised, _ = lend(ledger, fake)
    store = LocalLogStore(ledger.state_dir / "fleet")
    with pytest.raises(MoveRefused, match="belongs to main session 1"):
        helpers.raise_helper(ledger, actor(ledger, raised.seat.name), "feat/x", task="t", census=fake.census,
                             store=store, caller="x", wait=0.1, poll=0.05, sleep=lambda s: None)
    helpers.raise_helper(ledger, actor(ledger, OWNER), "feat/x", task="t", census=fake.census, store=store,
                         caller="x", wait=0.1, poll=0.05, sleep=lambda s: None)
    command = fake.launched[-1]
    assert command[command.index("--model") + 1] == "opus" and "--settings" not in command


def test_old_claude_warns_cap(tmp_path):
    def run(cmd, **kw):
        import subprocess
        return subprocess.CompletedProcess(cmd, 0, "2.1.250 (Claude Code)\n", "")
    assert "older than 2.1.257" in borrow.cap_warning(run)
    assert borrow.cap_warning(lambda cmd, **kw: __import__("subprocess").CompletedProcess(cmd, 0, "2.1.325", "")) == ""


def test_a_borrowed_seat_finishes_with_helper_done(tmp_path):
    root, ledger, fake, row, tip = world(tmp_path)
    raised, _ = lend(ledger, fake)
    commit(raised.seat.tree, "the flake's cause", "cause.txt")
    released, letter = helpers.done(ledger, actor(ledger, raised.seat.name), summary="a timer races the lock")
    assert released.state == "released" and released.history[-1]["evidence"]["borrowed_for"] == "feat/x"
    assert letter.startswith("letter for orchestrator 1") and "a timer races the lock" in letter
    assert f"flotilla retire \"{raised.seat.name}\"" in letter
    assert ledger.rows()[row.id].state == "claimed"   # the row it was lent for is untouched


def test_the_settings_keep_the_plugins_turned_off(tmp_path):
    from flotilla.fleet import plugins
    narrowed = json.dumps({"enabledPlugins": {"serena@official": False}})
    assert json.loads(borrow.settings(narrowed, "opus")) == {
        "enabledPlugins": {"serena@official": False},
        "env": {"CLAUDE_CODE_SUBAGENT_MODEL": "opus", "CLAUDE_CODE_SUBAGENT_MODEL_FORCE": "1"}}
    assert json.loads(plugins.with_env("", {"A": "1"})) == {"env": {"A": "1"}}


def test_argv_takes_a_borrowed_model_over_the_posts(tmp_path):
    from flotilla.posts import TEMPLATE_DIR, load_post
    post = load_post(TEMPLATE_DIR / "main.md")
    seat = launch.Seat("main", "main session 3", 3, tmp_path / "t", "fleet/main-3")
    command = launch.argv(seat, post, {**PROFILE, "permissions": {"mode": "auto"}}, main=tmp_path, model="fable")
    assert command[command.index("--model") + 1] == "fable"


def _close_items(ledger, live):
    from flotilla.watch import fleet
    return [item for item in fleet.borrowed_done(ledger.rows(), live) if item.kind == fleet.BORROWED]


def test_close_item_only_after_done(tmp_path):
    """Plan review focus 5: the "close it" item never fires while the task is open, nor twice."""
    root, ledger, fake, row, tip = world(tmp_path)
    raised, _ = lend(ledger, fake)
    name = raised.seat.name
    assert _close_items(ledger, {name, OWNER}) == []
    helpers.done(ledger, actor(ledger, name), summary="found it")
    items = _close_items(ledger, {name, OWNER})
    assert len(items) == 1 and "said it is done" in items[0].text and f"flotilla retire \"{name}\"" in items[0].text
    assert items == _close_items(ledger, {name, OWNER})   # the same item on the next pass: shown once by its key


def test_close_item_when_for_row_closes(tmp_path):
    root, ledger, fake, row, tip = world(tmp_path)
    raised, _ = lend(ledger, fake)
    with ledger.session() as s:   # the row it was lent for is closed by its owner
        s.append(actor(ledger, OWNER), row.id, "close", "closed", evidence={"why": "not needed"})
    items = _close_items(ledger, {raised.seat.name, OWNER})
    assert len(items) == 1 and "the row it was lent for is closed" in items[0].text


def test_no_close_item_after_retire(tmp_path):
    from flotilla.fleet import retire
    root, ledger, fake, row, tip = world(tmp_path)
    raised, _ = lend(ledger, fake)
    helpers.done(ledger, actor(ledger, raised.seat.name), summary="found it")
    retire.retire(ledger, raised.seat.name, caller="test", census=fake.census, wait=0.1, poll=0.05,
                  sleep=lambda s: None)
    live = {item.name for item in fake.census()}
    assert raised.seat.name not in live and _close_items(ledger, live) == []


def test_a_question_seat_has_no_row_to_close(tmp_path):
    root, ledger, fake, row, tip = world(tmp_path)
    raised, _ = lend(ledger, fake, for_task="why does the cache miss")
    assert _close_items(ledger, {raised.seat.name}) == []


def test_person_line_counts_borrowed(tmp_path):
    from flotilla.watch import inbox
    root, ledger, fake, row, tip = world(tmp_path)
    raised, _ = lend(ledger, fake)
    seats = inbox.lent(ledger.rows(), {raised.seat.name, OWNER})
    assert seats == [(raised.seat.name, "fable", "feat/x")]
    assert inbox.person_line([], seats) == ("flotilla: 1 seat on fable for feat/x - the orchestrator closes it once "
                                            "its task is done")
    entry = inbox.Entry("row:1@t", "question", "feat/y", "why", "", "main session 1", False, False)
    line = inbox.person_line([entry], seats)
    assert line.startswith("flotilla: 1 waits on you (1 a question)") and line.endswith("also 1 seat on fable for feat/x")
    assert inbox.person_line([], []) == "" and inbox.lent(ledger.rows(), set()) == []
    two = seats + [("main session 9", "sonnet", "a question")]
    assert inbox.lent_text(two) == "2 seats on borrowed models (fable for feat/x; sonnet for a question)"


def test_the_fleet_view_names_what_a_seat_was_lent_for(tmp_path):
    from flotilla.fleet import retire
    root, ledger, fake, row, tip = world(tmp_path)
    raised, _ = lend(ledger, fake)
    view = {item["name"]: item for item in retire.fleet_view(ledger, fake.census())}
    assert (view[raised.seat.name]["model"], view[raised.seat.name]["borrowed_for"]) == ("fable", "feat/x")
