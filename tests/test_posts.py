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
    assert set(templates()) == {"orchestrator", "sender", "reviewer", "judge", "main", "minor"}


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
    assert len(P.install_templates(tmp_path)) == 6
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
