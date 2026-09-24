import re
from pathlib import Path

from flotilla import cli

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "skills" / "onboard" / "SKILL.md"


def frontmatter(text):
    block = text.split("---", 2)[1]
    return dict(line.split(":", 1) for line in block.strip().splitlines() if ":" in line)


def test_skill_frontmatter():
    meta = frontmatter(SKILL.read_text(encoding="utf-8"))
    assert meta["name"].strip() == "onboard"
    assert 0 < len(meta["description"].strip()) <= 1024


def test_every_onboard_subcommand_the_skill_names_exists():
    named = set(re.findall(r"flotilla onboard ([a-z]+)", SKILL.read_text(encoding="utf-8")))
    parser = cli.build_parser()
    onboard = next(a for a in parser._subparsers._group_actions[0].choices["onboard"]._actions
                   if a.dest == "action")
    assert named and named <= set(onboard.choices), named - set(onboard.choices)
