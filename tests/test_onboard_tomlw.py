import tomllib

import pytest

from flotilla.onboard.tomlw import TomlWriteError, render_toml

PROFILE = {
    "schema": 1,
    "trunk": {"branch": "main"},
    "repos": [{"name": "app", "path": ".", "push_after": []}],
    "tests": {"tier": [
        {"name": "unit", "command": "uv run pytest -q", "required_for": ["handover", "push"]},
        {"name": "e2e", "command": "npm run e2e", "required_for": ["push"]},
    ]},
    "fleet": {"default": {"main": 1, "review": 1}},
    "guards": {"revert": True, "line_edit": False},
    "ratio": 0.5,
}


def test_profile_round_trips():
    assert tomllib.loads(render_toml(PROFILE)) == PROFILE


def test_strings_with_quotes_backslashes_controls_and_accents_round_trip():
    text = 'say "hi" \\ path\\to\nnext\tcol caf' + chr(233) + chr(1) + chr(127)
    assert tomllib.loads(render_toml({"s": text}))["s"] == text


def test_keys_that_are_not_bare_are_quoted():
    data = {"with space": 1, "dotted.key": {"x": 2}}
    assert tomllib.loads(render_toml(data)) == data


def test_booleans_are_written_as_booleans():
    out = render_toml({"flag": True, "n": 1})
    assert "flag = true" in out and "n = 1" in out


def test_header_becomes_comments():
    out = render_toml({"a": 1}, header="Written by flotilla.\nEdit freely.")
    assert out.startswith("# Written by flotilla.\n# Edit freely.\n")
    assert tomllib.loads(out) == {"a": 1}


def test_table_inside_an_array_item_round_trips():
    data = {"tests": {"tier": [{"name": "a", "env": {"CI": "1"}}, {"name": "b"}]}}
    assert tomllib.loads(render_toml(data)) == data


def test_empty_list_is_an_empty_array():
    assert tomllib.loads(render_toml({"x": []})) == {"x": []}


@pytest.mark.parametrize("bad", [None, float("nan"), float("inf"), [1, {"a": 1}], [[1]], object()])
def test_unrepresentable_values_are_refused_by_name(bad):
    with pytest.raises(TomlWriteError, match="value"):
        render_toml({"value": bad})
