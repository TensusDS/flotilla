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
