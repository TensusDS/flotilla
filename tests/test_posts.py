import pytest

from flotilla import posts as P

GOOD = """---
name: reviewer
description: Reads one branch: the diff, the tiers, the verdict.
model: inherit
name_pattern: "review session {n}"
may: [take, accept, fix]
writes_one_copy: false
template_version: 3
---
Body text.
"""


def write(folder, name, text):
    path = folder / f"{name}.md"
    path.write_text(text, encoding="utf-8")
    return path


def templates():
    return {p.name: p for p in (P.load_post(path) for path in sorted(P.TEMPLATE_DIR.glob("*.md")))}


def test_every_template_is_a_valid_post():
    assert set(templates()) == {"orchestrator", "sender", "reviewer", "judge", "main", "minor", "helper"}


def test_templates_follow_the_spec():
    posts = templates()
    assert [name for name, post in posts.items() if post.writes_one_copy] == ["sender"]
    assert {"take", "accept", "fix"} <= posts["reviewer"].may
    for author in ("main", "minor"):
        assert "accept" not in posts[author].may and "hand" in posts[author].may
    assert "queue" in posts["sender"].may and "queue" not in posts["main"].may


def test_frontmatter_values(tmp_path):
    post = P.load_post(write(tmp_path, "reviewer", GOOD))
    assert post.may == frozenset({"take", "accept", "fix"}) and post.writes_one_copy is False
    assert post.template_version == 3 and post.name_pattern == "review session {n}"
    assert post.body.strip() == "Body text."


def test_an_unknown_move_is_refused_by_name(tmp_path):
    with pytest.raises(P.PostError, match="merge"):
        P.load_post(write(tmp_path, "reviewer", GOOD.replace("[take, accept, fix]", "[take, merge]")))


def test_a_pattern_without_a_number_is_refused(tmp_path):
    with pytest.raises(P.PostError, match="name_pattern"):
        P.load_post(write(tmp_path, "reviewer", GOOD.replace("review session {n}", "reviewer")))


def test_a_name_that_is_not_the_file_name_is_refused(tmp_path):
    with pytest.raises(P.PostError, match="file name"):
        P.load_post(write(tmp_path, "sender", GOOD))


def test_matching_a_session_name(tmp_path):
    P.install_templates(tmp_path)
    posts = P.load_posts(tmp_path)
    assert P.post_for_session(posts, "review session 3").name == "reviewer"
    assert P.post_for_session(posts, "main session 12").name == "main"
    assert P.post_for_session(posts, "review session x") is None


def test_ambiguous_patterns_are_refused(tmp_path):
    folder = tmp_path / ".flotilla" / "posts"
    folder.mkdir(parents=True)
    write(folder, "reviewer", GOOD)
    write(folder, "minor", GOOD.replace("name: reviewer", "name: minor"))
    with pytest.raises(P.PostError, match="more than one"):
        P.post_for_session(P.load_posts(tmp_path), "review session 1")


def test_install_never_overwrites_a_project_post(tmp_path):
    assert len(P.install_templates(tmp_path)) == 7
    edited = tmp_path / ".flotilla" / "posts" / "reviewer.md"
    edited.write_text(edited.read_text(encoding="utf-8") + "\nProject note.\n", encoding="utf-8")
    assert P.install_templates(tmp_path) == []
    assert edited.read_text(encoding="utf-8").endswith("Project note.\n")


def test_no_posts_directory_is_no_posts(tmp_path):
    assert P.load_posts(tmp_path) == {}


def test_a_symlinked_posts_directory_is_refused(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / ".flotilla").mkdir()
    (tmp_path / ".flotilla" / "posts").symlink_to(outside)
    with pytest.raises(P.PostError, match="symlink"):
        P.install_templates(tmp_path)
    assert list(outside.iterdir()) == []


def test_the_orchestrator_may_mark_work_urgent():
    from flotilla.posts import MOVES, TEMPLATE_DIR, load_post
    assert "urgent" in MOVES
    assert "urgent" in load_post(TEMPLATE_DIR / "orchestrator.md").may


def test_the_judge_may_take_a_broke_back():
    from flotilla.posts import MOVES, TEMPLATE_DIR, load_post
    assert "unbroke" in MOVES
    assert "unbroke" in load_post(TEMPLATE_DIR / "judge.md").may


def test_the_judge_post_says_how_to_reach_what_it_must_see():
    from flotilla.posts import TEMPLATE_DIR, load_post
    body = " ".join(load_post(TEMPLATE_DIR / "judge.md").body.split())
    assert "ask the orchestrator for a way to start near it" in body          # H16
    assert "Walking a different path is not walking this one" in body
    assert "--strictPort" in body and "read the page's own revision stamp" in body   # H27
    assert "What the test browser cannot perceive" in body and "not walked as if perceived" in body   # H39


def test_the_main_post_says_a_wiring_row_requires_its_part():
    from flotilla.posts import TEMPLATE_DIR, load_post
    body = " ".join(load_post(TEMPLATE_DIR / "main.md").body.split())
    assert "wires in a part another row builds" in body and "--requires <that branch>" in body   # H17


@pytest.mark.parametrize("name", ["main", "minor"])
def test_a_builder_post_says_how_to_get_help_when_stuck(name):
    """Stuck: mark the row, write the facts, raise a blind expert and a researcher, run their probes, then the
    person after two rounds. Help that reads the builder's guesses agrees with them."""
    from flotilla.posts import TEMPLATE_DIR, load_post
    body = " ".join(load_post(TEMPLATE_DIR / f"{name}.md").body.split())
    assert "Being stuck is about the code, never about a verdict" in body
    assert "into each helper's brief and keep no file" in body
    assert "commit what you have first" in body and "with your home tree as its working directory" in body
    assert '--on "consult"' in body and "--clear" in body and "Unless the row already waits on something" in body
    assert "no guesses about the cause" in body
    assert "nothing of your guesses - not in the question, and not in where you send it to look" in body
    assert "An expert:" in body and "it runs only probes that change nothing" in body and "A researcher:" in body
    assert "Run their probes yourself" in body and "name it in the commit message" in body
    assert 'record it with `--on "the person"`' in body and "git-path" not in body


@pytest.mark.parametrize("name", ["main", "minor"])
def test_the_consult_mark_written_as_the_post_shows_it_asks_the_person_nothing(name):
    """A `;`, `(` or `?` in the reason takes the call off flotilla's own-command path, and in ask mode it becomes a
    permission question for the person - the one thing a technical problem should not cost them."""
    import re
    from flotilla.broker.decide import own_command
    from flotilla.hooks import CLI
    from flotilla.posts import TEMPLATE_DIR, load_post
    body = " ".join(load_post(TEMPLATE_DIR / f"{name}.md").body.split())
    shown = re.search(r'`(flotilla work wait <branch> --on "consult" --why "[^`]*")`', body).group(1)
    filled = re.sub(r"<([^>]*)>", r"\1", shown.replace("<branch>", "fix/totals"))   # the placeholder's own words
    command = str(CLI) + filled[len("flotilla"):]
    assert own_command({"tool_name": "Bash", "tool_input": {"command": command}}), command
    # the rule the post states is the broker's own: every sign it lets through passes, the next ones do not
    assert "only letters, digits, spaces and , . - / :" in body
    allowed = command.replace("did not work", "did not work, see src/x.py - 3 left: totals.py")
    assert own_command({"tool_name": "Bash", "tool_input": {"command": allowed}}), allowed
    for sign in "?![]#>()*;":
        assert not own_command({"tool_name": "Bash", "tool_input": {"command": command.replace("goal", f"goal{sign}")}})


def test_a_post_reads_its_model_and_permission_mode(tmp_path):
    from flotilla.posts import load_post
    path = tmp_path / "lead.md"
    path.write_text("---\nname: lead\nname_pattern: \"lead {n}\"\nmay: [assign]\nmodel: opus\n"
                    "permission_mode: plan\n---\nbody\n", encoding="utf-8")
    post = load_post(path)
    assert (post.model, post.permission_mode) == ("opus", "plan")


def test_a_post_refuses_an_unknown_permission_mode(tmp_path):
    import pytest
    from flotilla.posts import PostError, load_post
    path = tmp_path / "lead.md"
    path.write_text("---\nname: lead\nname_pattern: \"lead {n}\"\nmay: [assign]\npermission_mode: yolo\n---\n",
                    encoding="utf-8")
    with pytest.raises(PostError, match="permission_mode"):
        load_post(path)


def test_template_posts_inherit_the_model_and_the_permission_mode():
    from flotilla.posts import TEMPLATE_DIR, load_post
    for path in TEMPLATE_DIR.glob("*.md"):
        post = load_post(path)
        assert (post.model, post.permission_mode) == ("inherit", ""), path.name


def test_the_helper_post_never_delivers():
    helper = templates()["helper"]
    assert helper.name_pattern == "helper {n}"
    assert set(helper.may) == {"reserve", "release", "wait"}
    assert "flotilla helper done" in helper.body and "own tree" in helper.body


def test_a_post_reads_the_plugins_it_keeps(tmp_path):
    from flotilla.posts import load_post
    path = tmp_path / "lead.md"
    path.write_text("---\nname: lead\nname_pattern: \"lead {n}\"\nmay: [assign]\n"
                    "plugins: [playwright@claude-plugins-official, \"serena@claude-plugins-official\"]\n---\n",
                    encoding="utf-8")
    assert load_post(path).plugins == ("playwright@claude-plugins-official", "serena@claude-plugins-official")
    path.write_text("---\nname: lead\nname_pattern: \"lead {n}\"\nmay: [assign]\n---\n", encoding="utf-8")
    assert load_post(path).plugins == ()


def test_a_post_refuses_plugins_that_are_not_a_list(tmp_path):
    import pytest
    from flotilla.posts import PostError, load_post
    path = tmp_path / "lead.md"
    path.write_text("---\nname: lead\nname_pattern: \"lead {n}\"\nmay: [assign]\nplugins: playwright\n---\n",
                    encoding="utf-8")
    with pytest.raises(PostError, match="plugins"):
        load_post(path)


def test_only_the_judge_template_keeps_a_plugin_and_it_is_the_browser():
    posts = templates()
    assert posts["judge"].plugins == ("playwright@claude-plugins-official",)
    assert posts["judge"].template_version == 6
    assert all(post.plugins == () for name, post in posts.items() if name != "judge")


def test_a_session_of_a_fleet_before_its_name_is_known_by_its_former_post(tmp_path):
    """Twosuns field test of 0.6.7: rows owned by `main session 19`, raised before the profile named the fleet,
    matched no post once the patterns read `twosuns-main session {n}` - so nobody could say whose post the gone owner
    held. The former name is recognised for that question only."""
    P.install_templates(tmp_path)
    posts = P.load_posts(tmp_path, project="twosuns")
    assert P.post_for_session(posts, "main session 19") is None            # a former name holds no post now
    assert P.post_or_former(posts, "main session 19") == "main"
    assert P.post_or_former(posts, "twosuns-main session 2") == "main"
    assert P.post_or_former(posts, "worldcore-main session 2") == ""        # another project's seat is not ours
    assert P.post_or_former(P.load_posts(tmp_path), "main session 19") == "main"


def test_a_post_reads_its_effort(tmp_path):
    from flotilla.posts import load_post
    path = tmp_path / "lead.md"
    path.write_text("---\nname: lead\nname_pattern: \"lead {n}\"\nmay: [assign]\neffort: xhigh\n---\nbody\n",
                    encoding="utf-8")
    assert load_post(path).effort == "xhigh"


def test_a_post_refuses_an_effort_claude_code_does_not_take(tmp_path):
    """A typo would load, and Claude Code would run every seat of the post at its default with only a warning."""
    import pytest
    from flotilla.posts import PostError, load_post
    path = tmp_path / "lead.md"
    path.write_text("---\nname: lead\nname_pattern: \"lead {n}\"\nmay: [assign]\neffort: hight\n---\n",
                    encoding="utf-8")
    with pytest.raises(PostError, match="low, medium, high, xhigh, max"):
        load_post(path)


def test_template_posts_leave_the_effort_to_claude_code():
    from flotilla.posts import TEMPLATE_DIR, load_post
    for path in TEMPLATE_DIR.glob("*.md"):
        assert load_post(path).effort == "", path.name


def test_the_orchestrator_cleans_as_its_last_step():
    """The person (2026-10-27): the person never runs the collector; the orchestrator stands the fleet down when
    asked, and cleaning is its last step - also when it is the last session of the fleet, before it closes."""
    from flotilla.posts import TEMPLATE_DIR, load_post
    post = load_post(TEMPLATE_DIR / "orchestrator.md")
    body = " ".join((TEMPLATE_DIR / "orchestrator.md").read_text(encoding="utf-8").split())
    assert post.template_version == 19
    assert "run `flotilla fleet down` yourself" in body
    assert "run `flotilla fleet down` once more as your last step" in body
    assert "run `flotilla fleet clean`" not in body


def test_the_down_command_says_what_cleaning_removes():
    from pathlib import Path
    text = (Path(__file__).resolve().parents[1] / "skills" / "down" / "SKILL.md").read_text(encoding="utf-8")
    assert "Trees are kept" not in text and "exists elsewhere are removed" in text
    assert "Never remove a tree yourself" in text
