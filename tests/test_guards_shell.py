from pathlib import Path

from flotilla.guards import shell


def words_of(command, cwd=None):
    return [segment.words for segment in shell.segments(command, cwd)]


def test_a_command_after_a_separator_is_its_own_segment():
    found = words_of("uv sync && git push origin main; echo done\ngit status")
    assert ("git", "push", "origin", "main") in found and ("git", "status") in found


def test_leading_assignments_and_wrappers_are_peeled():
    segment = next(s for s in shell.segments('FLOTILLA_GATE_OVERRIDE="a; b" sudo git push origin main', None)
                   if s.program == "git")
    assert segment.words == ("git", "push", "origin", "main")
    assert segment.assignments == {"FLOTILLA_GATE_OVERRIDE": "a; b"}


def test_shell_keywords_do_not_hide_the_command():
    found = words_of("for f in a b; do git checkout -- $f; done")
    assert ("git", "checkout", "--", "$f") in found


def test_a_heredoc_body_is_data():
    command = "git commit -F- <<'EOF'\ngit push origin main\nEOF\ngit status"
    found = words_of(command)
    assert ("git", "push", "origin", "main") not in found and ("git", "status") in found


def test_cd_to_a_literal_directory_is_followed(tmp_path):
    (tmp_path / "sub").mkdir()
    segment = [s for s in shell.segments("cd sub && git status", tmp_path) if s.program == "git"][0]
    assert segment.cwd == (tmp_path / "sub").resolve()


def test_cd_through_a_variable_makes_the_directory_unknown(tmp_path):
    segment = [s for s in shell.segments("cd $D && git status", tmp_path) if s.program == "git"][0]
    assert segment.cwd is None


def test_git_names_its_verb_and_directory(tmp_path):
    (tmp_path / "repo").mkdir()
    segment = shell.segments("git --no-pager -C repo -c core.x=1 checkout -- f.txt", tmp_path)[0]
    assert segment.git() == ("checkout", ["--", "f.txt"], (tmp_path / "repo").resolve())
    assert shell.segments("git --version", tmp_path)[0].git() is None


def test_an_unbalanced_quote_is_read_plainly_not_dropped():
    assert any(words[:2] == ("git", "push") for words in words_of("git push origin main # don't"))


def test_the_ceiling_is_written_down():
    from flotilla.guards import CEILING
    assert "eval" in CEILING and "sh -c" in CEILING and "pre-push" in CEILING


def test_a_quoted_message_describing_a_command_is_not_a_command(tmp_path):
    for command in ('git commit -am "docs: guard; git push origin main now needs a receipt"',
                    'git commit -am "docs: a; git checkout -- . loses work"',
                    'echo "x | sed -i 1d f.txt"'):
        found = [s.words[:2] for s in shell.segments(command, tmp_path)]
        assert ("git", "push") not in found and ("git", "checkout") not in found and ("sed", "-i") not in found


def test_an_apostrophe_before_a_real_command_does_not_hide_it():
    assert ("git", "push", "origin", "main") in words_of("echo don't; git push origin main")


def test_a_subshell_written_without_spaces_is_read():
    assert ("git", "push", "origin", "main") in words_of("(git push origin main)")
    assert ("git", "push", "origin", "main") in words_of("(cd /tmp && git push origin main)")
