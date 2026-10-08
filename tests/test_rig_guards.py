import io
import json

import pytest

from flotilla import hooks
from flotilla.guards import person
from guardkit import onboarded

CLI = str(hooks.CLI)


def ask(root, command, monkeypatch, tmp_path):
    monkeypatch.setenv("FLOTILLA_STATE_DIR", str(tmp_path / "state"))
    payload = {"cwd": str(root), "tool_name": "Bash", "tool_input": {"command": command, "description": "d"},
               "permission_mode": "auto", "session_id": "sid"}
    out = io.StringIO()
    assert hooks.run_hook("guard", io.StringIO(json.dumps(payload)), out=out) == 0
    return json.loads(out.getvalue())["hookSpecificOutput"] if out.getvalue() else None


@pytest.mark.parametrize("move", ["enable --provider vast", "disable", "open --hours 3 --budget 2", "close",
                                  "allow-image --for r1", "-- enable --provider vast", "-- disable"])
@pytest.mark.parametrize("word", ["rig", "r?g", "r[i]g", "ri*"])
def test_the_persons_rig_moves_are_refused_to_a_tool_call(tmp_path, monkeypatch, move, word):
    answer = ask(onboarded(tmp_path), f"{CLI} {word} {move}", monkeypatch, tmp_path)
    assert answer and answer["permissionDecision"] == "deny" and "person" in answer["permissionDecisionReason"]


def test_reading_the_rig_stays_open_to_a_tool_call(tmp_path, monkeypatch):
    root = onboarded(tmp_path)
    for command in (f"{CLI} rig", f"{CLI} rig status", f"{CLI} rig reap"):
        answer = ask(root, command, monkeypatch, tmp_path)
        assert not answer or answer.get("permissionDecision") != "deny", (command, answer)


def edit(tool, path, monkeypatch, tmp_path):
    monkeypatch.setenv("FLOTILLA_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    field = "notebook_path" if tool == "NotebookEdit" else "file_path"
    payload = {"cwd": str(tmp_path), "tool_name": tool, "tool_input": {field: str(path)}, "session_id": "sid"}
    out = io.StringIO()
    assert hooks.run_hook("edit", io.StringIO(json.dumps(payload)), out=out) == 0
    return json.loads(out.getvalue())["hookSpecificOutput"] if out.getvalue() else None


@pytest.mark.parametrize("tool", ["Edit", "Write", "MultiEdit", "NotebookEdit"])
@pytest.mark.parametrize("where", ["machine.toml", "rig/rig.jsonl", "rig/reaper.py", "rig/machine-key", "KEY"])
def test_the_persons_files_are_refused_to_edit_tools(tmp_path, monkeypatch, tool, where):
    target = tmp_path / "config" / "flotilla" / "rig" / "vast.key" if where == "KEY" else tmp_path / "state" / where
    answer = edit(tool, target, monkeypatch, tmp_path)
    assert answer and answer["permissionDecision"] == "deny" and "person" in answer["permissionDecisionReason"]


def test_a_symlink_to_a_protected_file_is_refused_too(tmp_path, monkeypatch):
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "machine.toml").write_text("rig = \"off\"\n")
    link = tmp_path / "harmless.toml"
    link.symlink_to(tmp_path / "state" / "machine.toml")
    answer = edit("Write", link, monkeypatch, tmp_path)
    assert answer and answer["permissionDecision"] == "deny"


def test_ordinary_files_are_left_alone_everywhere(tmp_path, monkeypatch):
    for target in (tmp_path / "src" / "app.py", tmp_path / "state" / "lane" / "lane.jsonl", tmp_path / "README.md"):
        assert edit("Edit", target, monkeypatch, tmp_path) is None


def test_a_malformed_edit_payload_is_never_a_crash():
    out = io.StringIO()
    assert hooks.run_hook("edit", io.StringIO("not json"), out=out) == 0 and out.getvalue() == ""


@pytest.mark.parametrize("word", ["w?rk", "w[o]rk", "wo*"])
def test_a_globbed_work_still_reaches_approve(tmp_path, monkeypatch, word):
    answer = ask(onboarded(tmp_path), f"{CLI} {word} approve feat/x", monkeypatch, tmp_path)
    assert answer and answer["permissionDecision"] == "deny"


@pytest.mark.parametrize("command", ["{cli} $R open --hours 1 --budget 1", "{cli} `echo rig` open --hours 1",
                                     "{cli} rig $M --hours 1", "{cli} work $M feat/x",
                                     "$F work approve feat/x"])
def test_an_opaque_command_word_is_refused(tmp_path, monkeypatch, command):
    answer = ask(onboarded(tmp_path), command.format(cli=CLI), monkeypatch, tmp_path)
    assert answer and answer["permissionDecision"] == "deny" and "cannot be read" in answer["permissionDecisionReason"]


@pytest.mark.parametrize("command", ["{cli} lane run -- ls * *", "{cli} lane run -- echo approve *",
                                     "{cli} lane run -- echo $HOME", "{cli} work show feat/approve-button",
                                     "$EDITOR work.txt"])
def test_ordinary_globs_after_the_move_are_not_refused(tmp_path, monkeypatch, command):
    answer = ask(onboarded(tmp_path), command.format(cli=CLI), monkeypatch, tmp_path)
    assert not answer or answer.get("permissionDecision") != "deny", answer


def test_the_guard_reads_moves_where_the_parsers_put_them():
    """The guard reads a move as the second word after `flotilla` that is not an option; that holds only while the
    `rig` and `work` parsers take no option of their own before their move (third review of the 2a plan)."""
    import argparse
    from flotilla import cli as flotilla_cli
    parser = flotilla_cli.build_parser()
    commands = next(action for action in parser._actions if isinstance(action, argparse._SubParsersAction))
    for name in ("rig", "work"):
        options = {option for action in commands.choices[name]._actions for option in action.option_strings}
        assert options <= {"-h", "--help"}, (name, options)


@pytest.mark.parametrize("command", ["{cli} r[^x]g open --hours 1 --budget 1", "{cli} rig op[^x]n --hours 1",
                                     "{cli} w[^x]rk approve feat/x", "{cli} work a[^x]prove feat/x"])
def test_a_bash_glob_python_would_read_otherwise_is_refused(tmp_path, monkeypatch, command):
    """`[^x]` is a negation to bash and a literal caret to fnmatch: the guard does not guess what bash expands a glob
    in the command or the move to - it refuses (background scan of the 2a branch)."""
    answer = ask(onboarded(tmp_path), command.format(cli=CLI), monkeypatch, tmp_path)
    assert answer and answer["permissionDecision"] == "deny"


# A variable program alone is not seen at all (guards.CEILING); these lines name flotilla too, so the hook fires.
@pytest.mark.parametrize("command", ["echo flotilla && $F r?g open --hours 1",
                                     "echo flotilla && $F r[i]g open --hours 1",
                                     "echo flotilla && $F r[^x]g open --hours 1", "$F w?rk approve feat/x",
                                     "$F wo* approve feat/x"])
def test_a_globbed_command_after_an_opaque_program_is_refused(tmp_path, monkeypatch, command):
    answer = ask(onboarded(tmp_path), command, monkeypatch, tmp_path)
    assert answer and answer["permissionDecision"] == "deny"


@pytest.mark.parametrize("command", ["echo flotilla && $PYTHON $SCRIPT --flag", "echo flotilla && $EDITOR notes.txt",
                                     "echo flotilla && $PY -m pytest tests/*.py",
                                     'echo flotilla && $PY "${files[@]}"'])
def test_an_opaque_program_with_no_rig_or_work_in_reach_is_not_refused(tmp_path, monkeypatch, command):
    assert ask(onboarded(tmp_path), command, monkeypatch, tmp_path) is None


# A word bash builds at run time beside a variable program may become `work` or `rig`; a guarded move beside them
# is enough to refuse (commit security review of the 0.9.0 branch).
@pytest.mark.parametrize("command", ["$F wor$'k' approve feat/x", "$F w$(echo or)k approve feat/x",
                                     "$F $W approve feat/x", "$F w`echo or`k approve feat/x",
                                     "echo flotilla && $F $R open --hours 1"])
def test_a_built_word_beside_a_variable_program_and_a_guarded_move_is_refused(tmp_path, monkeypatch, command):
    answer = ask(onboarded(tmp_path), command, monkeypatch, tmp_path)
    assert answer and answer["permissionDecision"] == "deny"


# bash takes redirections out of argv before argparse reads it; the guard must read the same words (final review, C1)
@pytest.mark.parametrize("command", ["{cli} rig 2>/dev/null open --hours 1 --budget 9 --for r1",
                                     "{cli} rig >/tmp/o open --hours 1", "{cli} rig > /tmp/o open --hours 1",
                                     "{cli} 2>/dev/null rig open --hours 1", "{cli} rig <<<x open --hours 1",
                                     "{cli} rig 1>x close", "{cli} work 2>/dev/null approve feat/x",
                                     "{cli} work 2>&1 approve feat/x", "{cli} rig >&2 open --hours 1",
                                     "{cli} rig &>/dev/null open --hours 1", "{cli} work >>log approve feat/x"])
def test_a_redirection_between_the_words_does_not_hide_the_move(tmp_path, monkeypatch, command):
    answer = ask(onboarded(tmp_path), command.format(cli=CLI), monkeypatch, tmp_path)
    assert answer and answer["permissionDecision"] == "deny"


def test_a_redirection_after_an_ordinary_move_is_still_ordinary(tmp_path, monkeypatch):
    assert ask(onboarded(tmp_path), f"{CLI} work list 2>&1 > out.txt", monkeypatch, tmp_path) is None


@pytest.mark.parametrize("command", ["a 2>&1 b", "a >&2 b", "a <&3 b", "a &>log b", "a &>>log b"])
def test_an_ampersand_inside_a_redirection_separates_nothing(command):
    from flotilla.guards import shell
    assert [part.strip() for part in shell._split_outside_quotes(command)] == [command]


def test_a_lone_ampersand_still_separates():
    from flotilla.guards import shell
    assert [part.strip() for part in shell._split_outside_quotes("a & b")] == ["a", "b"]
    assert [part.strip() for part in shell.SEPARATORS.split("a & b")] == ["a", "b"]


@pytest.mark.parametrize("command", ["echo \\>& git push origin main", "echo '>'& git push origin main",
                                     'echo ">"& git push origin main'])
def test_an_escaped_or_quoted_angle_before_an_ampersand_still_separates(command):
    from flotilla.guards import shell
    assert any(segment.words[:2] == ("git", "push") for segment in shell.segments(command, None))


def test_flotillas_own_parser_reads_the_move():
    assert person._parsed(["flotilla", "rig", "open", "--hours", "1", "--budget", "1"]) == [("rig", "open")]
    assert person._parsed(["/x/bin/flotilla", "work", "approve", "feat/x"]) == [("work", "approve")]
    assert person._parsed(["flotilla", "rig", "no-such-move"]) == []


def test_the_quote_aware_split_cuts_after_an_escaped_angle():
    from flotilla.guards import shell
    assert [part.strip() for part in shell._split_outside_quotes("echo \\>& git push")] == ["echo \\>", "git push"]


@pytest.mark.parametrize("command", ["(flotilla work approve feat/x)", "(flotilla rig open --hours 1)",
                                     "x=$(true) && (flotilla rig close)"])
def test_a_subshell_glued_to_flotilla_does_not_hide_it(tmp_path, monkeypatch, command):
    answer = ask(onboarded(tmp_path), command, monkeypatch, tmp_path)
    assert answer and answer["permissionDecision"] == "deny"


@pytest.mark.parametrize("command", ["{,flotilla} work approve feat/x", "{flotilla,work} approve feat/x",
                                     "/usr/bin/{flotilla,} rig open --hours 1", "$F {work,} approve feat/x",
                                     "$F w{o,}rk approve feat/x"])
def test_a_brace_that_may_become_flotilla_or_its_command_is_refused(tmp_path, monkeypatch, command):
    answer = ask(onboarded(tmp_path), command, monkeypatch, tmp_path)
    assert answer and answer["permissionDecision"] == "deny"


@pytest.mark.parametrize("command", ['git commit -m "fix {a,b} in flotilla"', "echo 'flotilla {1..3}'"])
def test_a_quoted_or_escaped_brace_is_no_expansion(command):
    from flotilla.guards import shell
    assert not any(person.check(segment) for segment in shell.segments(command, None))


@pytest.mark.parametrize("command", ["{cli} rig run -- node -e 'console.log({{a:1}})'",
                                     "{cli} rig run --get shots -- sh -c 'echo $X; open x'",
                                     "{cli} rig run -- printf %s {{a,b}}",
                                     "{cli} rig run -- echo 'literal $(not run)'"])
def test_a_rig_run_line_with_braces_and_dollars_after_the_dashes_is_not_refused(tmp_path, monkeypatch, command):
    assert ask(onboarded(tmp_path), command.format(cli=CLI), monkeypatch, tmp_path) is None


@pytest.mark.parametrize("command", ["{cli} r{{i,}}g open --hours 1 -- x", "{cli} rig {{run,open}} --hours 1 --"])
def test_braces_before_the_dashes_are_still_refused(tmp_path, monkeypatch, command):
    answer = ask(onboarded(tmp_path), command.format(cli=CLI), monkeypatch, tmp_path)
    assert answer and answer["permissionDecision"] == "deny"


@pytest.mark.parametrize("command", [
    "{cli} rig run -- $(flotilla rig {{open,}} --hours 8 --budget 50 --why x)",
    "{cli} rig run -- `flotilla rig enable --provider vast`",
    "{cli} rig run -- \"$(flotilla work approve x)\"",
    "{cli} rig run -- cat <(flotilla rig open --hours 1)",
    "{cli} rig run -- echo \"it's $(flotilla rig open --hours 8 --budget 50 --why x)\"",
    "{cli} rig run -- \\' $(flotilla rig {{open,}} --hours 8) \\'",
    "{cli} rig run -- $'\\'' $(flotilla rig open)",
    "{cli} rig run --why \"$(flotilla rig open --hours 1)\" -- true",
    "{cli} rig run --put `flotilla rig enable` -- true"])
def test_a_substitution_in_a_rig_line_is_refused(tmp_path, monkeypatch, command):
    answer = ask(onboarded(tmp_path), command.format(cli=CLI), monkeypatch, tmp_path)
    assert answer and answer["permissionDecision"] == "deny"


def test_rig_keeps_the_permission_prompt():
    from flotilla.broker import decide
    assert "rig" not in decide.OWN_SUBCOMMANDS


@pytest.mark.parametrize("command", ["{cli} rig run -- cat <<EOF\n$(flotilla rig open --hours 8 --budget 50)\nEOF",
                                     "{cli} rig run -- cat <<-EOF\n\t`flotilla rig enable --provider vast`\n\tEOF",
                                     "{cli} work list <<EOF\n'$(flotilla work approve x)'\nEOF"])
def test_a_substitution_in_a_heredoc_body_of_a_flotilla_line_is_refused(tmp_path, monkeypatch, command):
    answer = ask(onboarded(tmp_path), command.format(cli=CLI), monkeypatch, tmp_path)
    assert answer and answer["permissionDecision"] == "deny"


def test_a_quoted_heredoc_body_is_data(tmp_path, monkeypatch):
    command = f"{CLI} rig run -- cat <<'EOF'\n$(not run here)\nEOF"
    assert ask(onboarded(tmp_path), command, monkeypatch, tmp_path) is None


@pytest.mark.parametrize("command", ["env -u flotilla -u rig -u run -- {{flotilla,}} rig open --hours 1 --budget 5",
                                     "env -u flotilla -u rig -u run -- {{flotilla,}} work approve r1",
                                     "env -u flotilla -u rig -u run -- /usr/bin/fl{{o,}}tilla rig enable",
                                     "{cli} -- wo{{r,}}k approve x"])
def test_a_decoy_rig_run_does_not_hide_braces(tmp_path, monkeypatch, command):
    answer = ask(onboarded(tmp_path), command.format(cli=CLI), monkeypatch, tmp_path)
    assert answer and answer["permissionDecision"] == "deny"


@pytest.mark.parametrize("command", ["cat <<< X\n{cli} work approve r1\nX",
                                     "cat <<E\"OF\"\nx\nEOF\n{cli} work approve r1\nE",
                                     "true <<<EOF\n{cli} rig enable --provider vast\nEOF"])
def test_a_heredoc_misread_does_not_hide_a_line_bash_runs(tmp_path, monkeypatch, command):
    answer = ask(onboarded(tmp_path), command.format(cli=CLI), monkeypatch, tmp_path)
    assert answer and answer["permissionDecision"] == "deny"


@pytest.mark.parametrize("command", ["echo '<<X'\n{cli} work approve r1\nX",
                                     "echo $((1<<2))\n{cli} work approve r1\n2",
                                     "# <<X\n{cli} work approve r1\nX",
                                     "echo \"a <<X\"\n{cli} rig open --hours 1\nX",
                                     "echo a\\<<X\n{cli} work approve r1\nX",
                                     "(( y = 1<<3 ))\n{cli} work approve r1\n3"])
def test_a_heredoc_that_is_not_one_does_not_hide_the_lines_after_it(tmp_path, monkeypatch, command):
    answer = ask(onboarded(tmp_path), command.format(cli=CLI), monkeypatch, tmp_path)
    assert answer and answer["permissionDecision"] == "deny"


def test_a_real_heredoc_body_is_still_skipped():
    from flotilla.guards import shell
    assert shell.without_heredoc_bodies("cat <<EOF\nflotilla work approve r1\nEOF\necho done") == "cat <<EOF\necho done"
    assert shell.heredoc("cat 0<<-'E O'") == ("E O", True) and shell.heredoc("cat <<E\"OF\"") == ("EOF", True)


@pytest.mark.parametrize("command", ["if (( 1<<2 )); then :; fi\n{cli} work approve r1\n2",
                                     "echo 'a\n<<X'\n{cli} work approve r1\nX",
                                     "case a in <<X) ;; esac\n{cli} work approve r1\nX",
                                     "cat <<EOF\n{cli} rig open --hours 1\nEOF"])
def test_the_person_guard_reads_every_line_whatever_looks_like_a_heredoc(tmp_path, monkeypatch, command):
    answer = ask(onboarded(tmp_path), command.format(cli=CLI), monkeypatch, tmp_path)
    assert answer and answer["permissionDecision"] == "deny"


def test_a_substitution_in_a_comment_of_a_rig_line_is_not_refused(tmp_path, monkeypatch):
    assert ask(onboarded(tmp_path), f"{CLI} rig run -- echo hi # $(x) `y`", monkeypatch, tmp_path) is None


def test_a_hash_inside_a_word_is_no_comment(tmp_path, monkeypatch):
    answer = ask(onboarded(tmp_path), f"{CLI} rig run -- echo a#$(flotilla rig open --hours 1)", monkeypatch, tmp_path)
    assert answer and answer["permissionDecision"] == "deny"
