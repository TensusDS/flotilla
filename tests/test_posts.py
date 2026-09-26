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
