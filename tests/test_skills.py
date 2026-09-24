"""The command surface (spec, section 3.4): every skill is a well-formed command, every CLI call a skill's text
names exists, and a command only a person should start is never model-invoked."""

import re
from pathlib import Path

import pytest

from flotilla import cli

ROOT = Path(__file__).resolve().parent.parent
SKILLS = sorted((ROOT / "skills").glob("*/SKILL.md"))
PERSON_ONLY = {"doctor", "check"}
MODEL_INVOCABLE = {"onboard"}
CALL = re.compile(r"`(?:\$\{CLAUDE_PLUGIN_ROOT\}/scripts/)?flotilla ([a-z-]+)(?: ([a-z-]+))?")


def frontmatter(path):
    block = path.read_text(encoding="utf-8").split("---", 2)[1]
    return {k.strip(): v.strip() for k, v in (line.split(":", 1) for line in block.strip().splitlines() if ":" in line)}


def subcommands(parser):
    action = next(a for a in parser._actions if a.__class__.__name__ == "_SubParsersAction")
    return action.choices


def test_the_commands_of_this_sub_project_exist():
    assert {p.parent.name for p in SKILLS} >= PERSON_ONLY | MODEL_INVOCABLE


@pytest.mark.parametrize("path", SKILLS, ids=lambda p: p.parent.name)
def test_each_skill_is_a_well_formed_command(path):
    meta = frontmatter(path)
    assert meta.get("name") == path.parent.name
    assert 0 < len(meta.get("description", "")) <= 1024


def test_every_cli_call_a_skill_names_exists():
    top = subcommands(cli.build_parser())
    onboard_actions = subcommands(top["onboard"])
    for path in SKILLS:
        for command, sub in CALL.findall(path.read_text(encoding="utf-8")):
            assert command in top, f"{path.parent.name}: `flotilla {command}` is not a CLI command"
            if command == "onboard" and sub:
                assert sub in onboard_actions, f"{path.parent.name}: `flotilla onboard {sub}` does not exist"


def test_person_only_commands_are_never_model_invoked():
    for path in SKILLS:
        flag = frontmatter(path).get("disable-model-invocation", "false").lower()
        if path.parent.name in PERSON_ONLY:
            assert flag == "true", path.parent.name
        if path.parent.name in MODEL_INVOCABLE:
            assert flag != "true", path.parent.name
