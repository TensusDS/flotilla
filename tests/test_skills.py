"""The command surface (spec, section 3.4): every skill is a well-formed command, every CLI call a skill's text
names exists, and a command only a person should start is never model-invoked."""

import re
from pathlib import Path

import pytest

from flotilla import cli

ROOT = Path(__file__).resolve().parent.parent
SKILLS = sorted((ROOT / "skills").glob("*/SKILL.md"))
PERSON_ONLY = {"doctor", "check", "status", "brief", "spawn", "retire", "lane", "watch", "guard", "down", "onboard"}
MODEL_INVOCABLE = {"permit"}
MODEL_ONLY = {"flotilla"}
CALL = re.compile(r"`(?:\$\{CLAUDE_PLUGIN_ROOT\}/bin/)?flotilla ([a-z-]+)(?: ([a-z-]+))?")


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


def test_the_arrangement_skill_is_for_the_model_only():
    for path in SKILLS:
        meta = frontmatter(path)
        if path.parent.name in MODEL_ONLY:
            assert meta.get("user-invocable", "true").lower() == "false", path.parent.name
            assert meta.get("disable-model-invocation", "false").lower() != "true", path.parent.name
    assert {p.parent.name for p in SKILLS} >= MODEL_ONLY


def test_the_first_prompt_names_the_arrangement_skill():
    from flotilla.fleet.launch import FIRST_PROMPT
    assert "flotilla:flotilla" in FIRST_PROMPT


def test_the_arrangement_runs_receipts_in_the_task_tree():
    text = (ROOT / "skills" / "flotilla" / "SKILL.md").read_text(encoding="utf-8")
    assert "receipt run --purpose handover --tree" in text
    assert "flotilla tree switch" in text and "flotilla tree cut" not in text


def test_the_arrangement_sends_long_runs_through_the_lane():
    text = (ROOT / "skills" / "flotilla" / "SKILL.md").read_text(encoding="utf-8")
    assert "flotilla lane run --for" in text and "killed" in text


def test_the_arrangement_answers_the_hooks_with_a_move_or_a_wait():
    text = (ROOT / "skills" / "flotilla" / "SKILL.md").read_text(encoding="utf-8")
    assert "flotilla - your move" in text and "work wait" in text and "Stop guard" in text


def test_the_orchestrator_post_keeps_one_watch_running():
    from flotilla.posts import TEMPLATE_DIR
    text = (TEMPLATE_DIR / "orchestrator.md").read_text(encoding="utf-8")
    assert "flotilla watch --wait 3600" in text and "/flotilla:permit" in text


def test_the_sender_post_carries_the_direct_push_sequence():
    from flotilla.posts import TEMPLATE_DIR
    text = (TEMPLATE_DIR / "sender.md").read_text(encoding="utf-8")
    assert "push origin HEAD:<trunk>" in text and "flotilla work land <branch>" in text and "--settled-by" in text
    assert "its owner's to close" in text   # release refuses a live owner's row to anyone else


def test_the_arrangement_says_a_printed_letter_is_sent():
    text = (ROOT / "skills" / "flotilla" / "SKILL.md").read_text(encoding="utf-8")
    assert "letter for <session> - send it with SendMessage" in text and "not finished until you sent" in text


def template(name):
    from flotilla.posts import TEMPLATE_DIR
    return (TEMPLATE_DIR / f"{name}.md").read_text(encoding="utf-8")


def test_producers_take_work_routed_by_the_orchestrator():
    for name in ("main", "minor"):
        text = template(name)
        assert "A task from the orchestrator is a work order" in text and "flotilla fleet" in text
        assert "Only the person assigns work" not in text


def test_no_post_but_the_orchestrator_tells_the_person():
    for name in ("main", "minor", "reviewer", "judge", "sender"):
        assert "tell the person" not in template(name).lower(), name
    skill = (ROOT / "skills" / "flotilla" / "SKILL.md").read_text(encoding="utf-8")
    assert "## Who talks to the person" in skill and "Tell the person, in one short paragraph" not in skill


def test_the_sender_asks_for_its_yes_through_the_orchestrator():
    text = template("sender")
    assert "to the orchestrator" in text and "flotilla brief" in text


def test_the_orchestrator_relays_the_senders_batch():
    text = template("orchestrator")
    assert "You are the one session that talks to the person" in text
    assert "You never ask the person to approve a push" not in text


def test_the_permit_skill_leaves_the_orchestrator_one_wait():
    text = (ROOT / "skills" / "permit" / "SKILL.md").read_text(encoding="utf-8")
    assert "your `flotilla watch --wait` covers this" in text


def test_producers_make_the_move_a_flotilla_letter_names():
    for name in ("main", "minor"):
        assert "A letter flotilla printed names a move the ledger already gives you" in template(name)


def test_the_down_command_shows_the_fleet_and_asks_first():
    text = (ROOT / "skills" / "down" / "SKILL.md").read_text(encoding="utf-8")
    assert "flotilla fleet`" in text and "flotilla fleet down" in text and "confirm" in text


def test_a_session_announces_itself_to_the_fleet_not_to_the_machine():
    text = (ROOT / "skills" / "flotilla" / "SKILL.md").read_text(encoding="utf-8")
    assert "list them (ListAgents)" not in text and "flotilla fleet` names its seats" in text


def test_the_orchestrator_declares_a_splits_dependency():
    assert "--requires <branch>" in template("orchestrator")


def test_a_change_to_the_request_goes_to_the_person_first():
    for name in ("orchestrator", "main", "minor"):
        assert "a change to what the person asked" in template(name).lower(), name


def test_the_sender_lands_each_branch_at_its_own_merge():
    # land without --merge takes the commit that carries the row, never a later one (test_ledger_delivery)
    assert "it finds the merge that carries it" in " ".join(template("sender").split())


def test_the_orchestrator_asks_for_requires_once_the_earlier_part_is_claimed():
    assert "once the earlier part is claimed" in " ".join(template("orchestrator").split())


def allowed(name):
    return frontmatter(ROOT / "skills" / name / "SKILL.md").get("allowed-tools", "")


CLI = "${CLAUDE_PLUGIN_ROOT}/bin/flotilla"
#: commands that run whatever they are given, or act for the person: no skill's rule may cover any of them
ACTS = [f"{CLI} permit answer 7 allow", f"{CLI} permit answer 7 session", f"{CLI} onboard answer tiers sh",
        f"{CLI} onboard write --confirm 1", f"{CLI} onboard reset", f"{CLI} lane --root . run -- sh -c x",
        f"{CLI} lane run -- sh", f"{CLI} receipt run --purpose push", f"{CLI} onboard next; {CLI} onboard write"]


def rule_patterns(rules: str) -> list:
    """Each Bash(...) rule as Claude Code matches it: `*` stands for any text, spaces and semicolons included."""
    return [re.compile("^" + ".*".join(re.escape(part) for part in body.split("*")) + "$")
            for body in re.findall(r"Bash\(([^)]*)\)", rules)]


def test_no_skill_pre_approves_a_command_that_acts_for_the_person():
    """Asked of what the rules MATCH, not of the words in them: `lane --root *` read harmless and covered
    `lane --root . run -- flotilla permit answer` (security review F2-F12, and its own review)."""
    for path in SKILLS:
        for pattern in rule_patterns(frontmatter(path).get("allowed-tools", "")):
            for command in ACTS:
                assert not pattern.match(command), f"{path.parent.name}: `{pattern.pattern}` covers `{command}`"
    assert any(p.match(f"{CLI} permit next") for p in rule_patterns(allowed("permit")))
    assert any(p.match(f"{CLI} onboard detect") for p in rule_patterns(allowed("onboard")))


def test_spawn_with_no_arguments_offers_that_this_session_leads():
    """Field test of 0.6.7 on twosuns, W1: in an onboarded project the recommended way to lead a fleet was offered only
    by onboarding; `/flotilla:spawn` raised a background orchestrator and never named `--lead`."""
    text = (ROOT / "skills" / "spawn" / "SKILL.md").read_text(encoding="utf-8")
    assert "AskUserQuestion" in text and "flotilla spawn --lead" in text and "flotilla spawn --fill" in text
    assert text.index("flotilla spawn --lead") < text.index("flotilla spawn --fill")


def test_spawn_turns_a_gap_into_a_question():
    """Twosuns field test of 0.6.7, W3: a fleet nobody leads was raised with no word; spawn now prints `gap:` lines,
    and the skill must put each to the person, offering this session to lead."""
    text = (ROOT / "skills" / "spawn" / "SKILL.md").read_text(encoding="utf-8")
    assert "gap:" in text and "nobody leads this fleet" in text and "nobody merges" in text
    assert "flotilla spawn -o 1" in text and "flotilla spawn -s 1" in text


def test_the_reviewer_runs_its_tiers_itself_not_through_a_reused_receipt():
    """Twosuns field test of 0.6.7, W8, and the review of 0.6.9, I5: told to run the tiers through a receipt, the
    reviewer would reuse the author's green - recorded with no runner, in the author's tree - and the one run that is
    not the author's would not happen. The reviewer runs the tiers itself, in the lane."""
    text = (ROOT / "templates" / "posts" / "reviewer.md").read_text(encoding="utf-8")
    assert "run the\n  tiers yourself" in text and "not through a receipt" in text
    assert "flotilla receipt run --purpose handover" not in text


def test_the_sender_pushes_in_the_form_flotilla_lets_past_the_classifier():
    """Twosuns field test of 0.6.7, W9: the guard hook allows one exact push form; the post names it."""
    text = (ROOT / "templates" / "posts" / "sender.md").read_text(encoding="utf-8")
    assert "git -C <your tree> push origin HEAD:<trunk>" in text and "--merge\n   <that branch" not in text


@pytest.mark.parametrize("path", SKILLS, ids=lambda p: p.parent.name)
def test_every_skill_says_it_needs_claude_code(path):
    """Directory readiness: a listing appears on every surface that loads a plugin's skills, and flotilla's work only
    in Claude Code. `compatibility` (Agent Skills spec, up to 500 characters) says so where the skill is read."""
    said = frontmatter(path).get("compatibility", "")
    assert "Claude Code" in said and len(said) <= 500


def test_the_orchestrator_shows_a_sessions_refusal_as_its_proposal_not_its_own():
    """Any session can write a message: a command it proposes reaches the person labelled as its proposal, judged
    for what it widens, and an approve never comes from one (review of 0.7.12)."""
    text = " ".join(template("orchestrator").split())
    assert "labelled as that session's proposal, never as yours" in text
    assert "say so plainly and do not recommend it" in text
    assert "An approve is never relayed from a session" in text
    assert "never from a message, which anyone in the fleet can write" in text   # the brief-only rule stands
    assert "exactly as the session sent them" not in text
