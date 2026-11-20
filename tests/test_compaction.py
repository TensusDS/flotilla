"""A post's compaction window (seat compaction design, section 1)."""

import pytest

from flotilla import posts as P
from flotilla.fleet import compaction


def post_file(folder, name: str, window_line: str = ""):
    text = ("---\n"
            f"name: {name}\n"
            f"name_pattern: \"{name} session {{n}}\"\n"
            "may: [claim]\n"
            "template_version: 1\n"
            + (window_line + "\n" if window_line else "")
            + "---\nBody.\n")
    path = folder / f"{name}.md"
    path.write_text(text, encoding="utf-8")
    return path


@pytest.mark.parametrize("name, line, expected", [
    ("main", "compact_window: 400000", 400_000),
    ("main", "compact_window: 150000", 150_000),
    ("main", "compact_window: off", None),
    ("orchestrator", "", 400_000),
    ("main", "", 200_000),
    ("reviewer", "", 200_000),
])
def test_window_values(tmp_path, name, line, expected):
    assert compaction.window_for(P.load_post(post_file(tmp_path, name, line))) == expected


def test_a_window_below_the_floor_is_refused_with_the_reason(tmp_path):
    with pytest.raises(P.PostError) as err:
        P.load_post(post_file(tmp_path, "main", "compact_window: 120000"))
    assert "150000" in str(err.value) and "thrash" in str(err.value)


@pytest.mark.parametrize("value", ["200k", "true", "\"\"", "-5", "1.5e5"])
def test_a_window_that_is_not_a_number_or_off_is_refused(tmp_path, value):
    with pytest.raises(P.PostError) as err:
        P.load_post(post_file(tmp_path, "main", f"compact_window: {value}"))
    assert "compact_window" in str(err.value) and "off" in str(err.value)


def test_shown():
    assert compaction.shown(200_000) == "compacts near 200k"
    assert compaction.shown(400_000) == "compacts near 400k"
    assert compaction.shown(None) == "compacts at Claude Code's default"


# -- every seat flotilla launches carries its window (design section 1) --

import dataclasses
import json
from pathlib import Path

from flotilla.fleet import launch

TEMPLATES = {p.name: p for p in (P.load_post(path) for path in P.TEMPLATE_DIR.glob("*.md"))}
MAIN = Path("/work/app")
OPUS = {"fleet": {"model": "opus"}}


def settings_of(command):
    return json.loads(command[command.index("--settings") + 1])


def test_window_merges_with_narrowed_plugins():
    merged = json.loads(compaction.with_window('{"enabledPlugins": {"x@y": false}}', 200_000))
    assert merged == {"enabledPlugins": {"x@y": False}, "autoCompactWindow": 200_000}
    assert compaction.with_window("", None) == ""
    assert compaction.with_window('{"a": 1}', None) == '{"a": 1}'


def test_argv_carries_the_posts_window():
    orchestrator = TEMPLATES["orchestrator"]
    seat = launch.seat_for(MAIN, orchestrator, "orchestrator 1")
    command = launch.argv(seat, orchestrator, OPUS, main=MAIN, settings_json='{"enabledPlugins": {"p@m": false}}')
    assert settings_of(command) == {"enabledPlugins": {"p@m": False}, "autoCompactWindow": 400_000}
    reviewer = TEMPLATES["reviewer"]
    command = launch.argv(launch.seat_for(MAIN, reviewer, "review session 1"), reviewer, OPUS, main=MAIN)
    assert settings_of(command) == {"autoCompactWindow": 200_000}


def test_a_post_set_off_launches_without_a_window():
    off = dataclasses.replace(TEMPLATES["reviewer"], compact_window="off")
    command = launch.argv(launch.seat_for(MAIN, off, "review session 1"), off, OPUS, main=MAIN)
    assert "--settings" not in command


def test_dry_run_line_names_the_window():
    reviewer = TEMPLATES["reviewer"]
    command = launch.argv(launch.seat_for(MAIN, reviewer, "review session 1"), reviewer, OPUS, main=MAIN)
    assert compaction.of_command(command) == "compacts near 200k"
    off = dataclasses.replace(reviewer, compact_window="off")
    assert compaction.of_command(launch.argv(launch.seat_for(MAIN, off, "review session 1"), off, OPUS,
                                             main=MAIN)) == "compacts at Claude Code's default"


def test_every_raise_path_gets_the_window_and_records_it(tmp_path, monkeypatch):
    """spawn, a borrowed seat and a helper all launch through `launch.argv`; each reserve records the window."""
    from flotilla.core import claude_state
    from flotilla.fleet import retire
    monkeypatch.setattr(claude_state, "setup_problems", lambda main, **kw: ([], []))
    monkeypatch.setenv("FLOTILLA_STATE_DIR", str(tmp_path / "state"))
    import test_fleet_borrow
    import test_fleet_helpers
    for part in ("b", "h", "s"):
        (tmp_path / part).mkdir()
    root, ledger, fake, row, tip = test_fleet_borrow.world(tmp_path / "b")
    raised, _ = test_fleet_borrow.lend(ledger, fake)
    assert settings_of(fake.launched[-1])["autoCompactWindow"] == 200_000
    seat_row = next(r for r in ledger.rows().values() if r.owner == raised.seat.name)
    assert compaction.recorded(seat_row) == 200_000
    root, ledger, fake, row, tip = test_fleet_helpers.world(tmp_path / "h")
    test_fleet_helpers.raise_one(ledger, fake)
    assert settings_of(fake.launched[-1])["autoCompactWindow"] == 200_000
    import test_fleet_retire
    from fleetkit import FakeClaude
    fake = FakeClaude()
    root, ledger, seat = test_fleet_retire.raised_world(tmp_path / "s", fake)
    assert settings_of(fake.launched[-1])["autoCompactWindow"] == 200_000
    assert retire.fleet_view(ledger, fake.census())[0]["window"] == 200_000


def test_a_seat_raised_before_windows_reads_unknown(tmp_path, monkeypatch):
    from flotilla.core import claude_state
    from fleetkit import FakeClaude
    import test_fleet_retire
    monkeypatch.setattr(claude_state, "setup_problems", lambda main, **kw: ([], []))
    fake = FakeClaude()
    root, ledger, seat = test_fleet_retire.raised_world(tmp_path, fake)
    row = next(iter(ledger.rows().values()))
    row.history[0]["evidence"] = {"model": "opus"}
    assert compaction.recorded(row) == compaction.UNKNOWN
    assert "raised before compaction windows" in compaction.fleet_note(compaction.UNKNOWN)
    assert compaction.fleet_note(None) == "compacts at Claude Code's default"
    assert compaction.fleet_note(200_000) == "compacts near 200k"


# -- what every seat is told (design sections 2 and 5) --

def test_system_prompt_carries_notes_and_no_acks():
    for post in TEMPLATES.values():
        seat = launch.seat_for(MAIN, post, post.name_pattern.replace("{n}", "1"))
        text = launch.system_prompt(seat, post, main=MAIN)
        assert launch.WORKING_NOTES in text and launch.NO_ACKS in text
    for must in ("--memo", "flotilla work note", "--post", "files touched", "next step", "8 lines"):
        assert must in launch.WORKING_NOTES
    for must in ("no reply", "acknowledge", "letter", "no reply needed"):
        assert must in launch.NO_ACKS


def test_skill_start_sends_one_message_to_the_orchestrator():
    text = (Path(P.__file__).resolve().parent.parent / "skills" / "flotilla" / "SKILL.md").read_text(encoding="utf-8")
    assert "send each live one" not in text
    start = text[text.index("## When you start"):text.index("## State lives")]
    assert "one message" in start and "orchestrator" in start
    assert "## Working notes" in text and "flotilla work note" in text
    assert "no reply needed" in text


def test_the_orchestrator_template_names_its_window_and_sends_no_acknowledgements():
    orchestrator = TEMPLATES["orchestrator"]
    assert orchestrator.compact_window == 400_000 and orchestrator.template_version == 34
    assert "--post" in orchestrator.body and "acknowledge" in orchestrator.body
    assert all(post.compact_window == "" for name, post in TEMPLATES.items() if name != "orchestrator")
