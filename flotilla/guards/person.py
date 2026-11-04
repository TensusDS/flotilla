"""The person's own move is refused to Claude's tool calls: `flotilla work approve`.

Where a person authorizes merges, their approval is what lets work reach trunk (decision 183), and the person check
(`caller.person_refusal`) accepts a terminal or an interactive session. When the person's own session leads the
fleet, that session's model passes the check: it runs in an interactive session. What tells the person from the
model there is the door: a Bash tool call fires this hook, a command the person types with `!` does not (measured on
Claude Code 2.1.287). So a tool call that approves is refused, always, with no override and whatever guards the
profile turns on: a session cannot waive what only a person gives.

Seen: the flotilla CLI anywhere in the segment - by any path, behind an interpreter or a wrapper (`python3 .../flotilla`,
`timeout 60 .../flotilla`), or as the module (`-m flotilla.cli`) - whose move, the first word after `work` that names
one, is `approve`. The words are the ones bash hands the program - quotes and backslashes taken out,
redirections and their targets taken away (`argv`, measured against bash) - and the move is the one flotilla's own
parser reads. Not seen: what the shell's
ceiling hides (`flotilla.guards.CEILING`) and Python handed the code as text (`python3 -c ...`) - such a call still
meets Claude Code's permission prompt in the person's session.
"""

from __future__ import annotations

import contextlib
import fnmatch
import functools
import io
import re

import os

from flotilla.guards import Finding

GUARD = "person"
#: Every move `flotilla work` takes; a test holds it equal to the CLI's own list.
MOVES = ("accept", "adopt", "approve", "assign", "broke", "claim", "close", "fix", "hand", "hold", "inbatch", "land",
         "moved", "offledger", "queue", "reconcile", "recuse", "release", "requires", "reserve", "return", "ship",
         "show", "take", "unbroke", "unhold", "urgent", "vouch", "wait", "walkable", "walked")

#: Closes every refusal of a command flotilla cannot read: it is the guard working, and the way past it is plain
#: (twosuns field report C4; the person, 2026-10-27: a refusal by design must not read as a defect).
PLAINLY = ("This is a guard, not a fault, and there is nothing to report: write each flotilla command out in full, one "
           "per call - a loop over moves becomes separate calls.")


def _runs_flotilla(word: str, previous: str) -> bool:
    return os.path.basename(word) == "flotilla" or (previous == "-m" and word.split(".")[0] == "flotilla")


#: What bash expands inside a word, so the word the guard reads is not the word that runs: braces, ANSI-C and locale
#: quoting, extglob (third review of 0.6.10, I1). A line ending in a backslash continues the word on the next line.
EXPANDS = ("{", "$'", '$"', "@(", "+(", "!(", "?(", "*(")


#: `flotilla rig` moves that turn rented machines on or off, spend money, or widen what runs (rig design, section 4),
#: and who a paid session serves (`share`, `unshare`: the person's since 0.25.0, guarded here since 0.28.1).
RIG_MOVES = ("enable", "disable", "open", "close", "allow-image", "share", "unshare")

#: The moves a variable program could be running when another of its words is built at run time.
GUARDED = ("approve",) + RIG_MOVES


def _brace_expands(text: str) -> bool:
    """An unquoted `{a,b}` or `{1..3}`: bash turns it into several words, so the guard cannot say which word is the
    program or the command before it runs."""
    quote, depth, inner, escaped = None, 0, [], False
    for ch in text:
        if escaped:
            escaped = False
        elif quote:
            quote = None if ch == quote else quote
        elif ch == "\\":
            escaped = True
        elif ch in "'\"":
            quote = ch
        elif ch == "{":
            depth, inner = depth + 1, []
        elif ch == "}" and depth:
            depth -= 1
            if "," in inner or ".." in "".join(inner):
                return True
        elif depth:
            if ch in " \t\n":
                depth = 0
            else:
                inner.append(ch)
    return False


def argv(text: str) -> list[str]:
    """The words bash hands a program from one segment: quotes and backslashes taken out, every redirection and its
    target taken away, a comment ended. Only redirections are removed here, never a word bash keeps, so a quoted
    `'>'` stays an argument. Expansions are not performed: `check` refuses a segment that holds them first.
    Measured against bash itself (tests/test_guards_argv.py)."""
    words, word, started, i, n = [], [], False, 0, len(text)
    drop_next = False

    def end():
        nonlocal word, started, drop_next
        if started:
            if drop_next:
                drop_next = False
            else:
                words.append("".join(word))
        word, started = [], False

    while i < n:
        ch = text[i]
        if ch in " \t\n":
            end()
            i += 1
        elif ch == "\\":
            if i + 1 < n and text[i + 1] != "\n":
                word.append(text[i + 1])
            started = True
            i += 2
        elif ch == "'":
            close = text.find("'", i + 1)
            close = n if close < 0 else close
            word.append(text[i + 1:close])
            started, i = True, close + 1
        elif ch == '"':
            i += 1
            while i < n and text[i] != '"':
                if text[i] == "\\" and i + 1 < n and text[i + 1] in '$`"\\\n':
                    if text[i + 1] != "\n":
                        word.append(text[i + 1])
                    i += 2
                else:
                    word.append(text[i])
                    i += 1
            started, i = True, i + 1
        elif ch == "#" and not started:
            break
        elif ch in "()":                       # subshell and grouping syntax: an operator, never part of a word
            end()
            i += 1
        elif ch in "<>" or (ch == "&" and i + 1 < n and text[i + 1] == ">"):
            if started and not "".join(word).isdigit():
                end()
            word, started = [], False          # a descriptor before the operator belongs to it
            j = i + 1
            while j < n and text[j] in "<>&|-":
                j += 1
            operator, i = text[i:j], j
            if operator.endswith("&-"):        # `>&-` closes a descriptor: no target
                continue
            if operator.endswith("&"):         # `2>&1`, `>&2`: the target is the descriptor glued to it
                k = i
                while k < n and text[k].isdigit():
                    k += 1
                if k > i:
                    i = k
                    continue
            drop_next = True                   # the target is the next word, glued or not
        else:
            word.append(ch)
            started = True
            i += 1
    end()
    return words


def _reads(words: list[str]) -> list[tuple[str, str]]:
    """For each flotilla in the segment, the command and its move: the first two words after it that are not
    options, which is where argparse reads them. Reading only there keeps an ordinary `ls * *` further along from
    looking like a move, and a decoy before a second flotilla from hiding the real one (review of 0.6.10)."""
    found = []
    for at, word in enumerate(words):
        if _runs_flotilla(word, words[at - 1] if at else ""):
            plain = [item for item in words[at + 1:] if not item.startswith("-")][:2]
            found.append(tuple(plain + [""] * (2 - len(plain))))
    return found


def _is(word: str, name: str) -> bool:
    """The word is `name`. A glob that bash might expand to it never reaches here: `_opaque` refuses any glob in a
    command or move first, because fnmatch is not bash and the guard does not guess."""
    return word == name


def _opaque(word: str) -> bool:
    """A word whose meaning bash decides at run time: a variable, a substitution, or a glob - fnmatch is not bash
    (`[^x]` negates in bash and is a literal caret to fnmatch), so the guard never guesses what a glob becomes."""
    return any(sign in word for sign in "$`*?[")


def _may_be(word: str, name: str) -> bool:
    """The word is `name`, or a glob bash might expand to it. Only for a program word the guard cannot read: there it
    asks whether a command it guards is in reach. fnmatch is not bash, so any bracket counts; a variable does not,
    or every `$PYTHON $SCRIPT` would be refused (a variable program is a stated limit, README "Guards")."""
    if word == name:
        return True
    if "$" in word or "`" in word or not _opaque(word):
        return False
    return "[" in word or fnmatch.fnmatchcase(name, word)


@functools.lru_cache(maxsize=1)
def _parser():
    from flotilla.cli import build_parser
    return build_parser()


def _parsed(words: list[str]) -> list[tuple[str, str]]:
    """For each flotilla in the segment, the command and move flotilla's own parser reads - the reading that decides
    what runs, so no ordering, option value or abbreviation can differ from it. A line it refuses runs nothing."""
    found = []
    for at, word in enumerate(words):
        if _runs_flotilla(word, words[at - 1] if at else ""):
            try:
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    space = _parser().parse_args(words[at + 1:])
            except SystemExit:
                continue
            command = getattr(space, "command", "") or ""
            move = getattr(space, "move", "") if command == "work" else getattr(space, "action", "")
            found.append((command, move or ""))
    return found


def _rig_move(words: list[str]) -> str:
    for command, move in _reads(words) + _parsed(words):
        if _is(command, "rig") and any(_is(move, name) for name in RIG_MOVES):
            return move
    return ""


def _moves(words: list[str]) -> list[str]:
    return [("approve" if _is(move, "approve") else move) for command, move in _reads(words) + _parsed(words)
            if _is(command, "work") and move]


def _move(words: list[str]) -> str:
    """The move of a `flotilla ... work <move>` in the segment - `approve` if any reading names it - or ""."""
    moves = _moves(words)
    return "approve" if "approve" in moves else (moves[0] if moves else "")


def _runs_any_flotilla(words: list[str]) -> bool:
    return any(_runs_flotilla(word, words[at - 1] if at else "") for at, word in enumerate(words))


def _scan(text: str) -> tuple[bool, list[int]]:
    """Read `text` with bash's quoting: whether a substitution - `$(`, a backtick, `<(`, `>(` - is live somewhere (bash
    runs it in the caller's own shell, before flotilla starts), and where the first unquoted standalone `--` begins.
    Single quotes and `$'..'` hide everything; double quotes hide all but `$(` and backticks; a backslash hides the
    next character. Never a toggle on every quote: an apostrophe inside double quotes is a letter."""
    live, dashes, state, i, n = False, [], "plain", 0, len(text)
    while i < n:
        ch = text[i]
        if state == "single":
            if ch == "'":
                state = "plain"
        elif state == "ansi":
            if ch == "\\":
                i += 1
            elif ch == "'":
                state = "plain"
        elif state == "double":
            if ch == "\\":
                i += 1
            elif ch == '"':
                state = "plain"
            elif ch == "`" or text.startswith("$(", i):
                live = True
        else:
            if ch == "\\":
                i += 1
            elif ch == "'":
                state = "single"
            elif text.startswith("$'", i):
                state, i = "ansi", i + 1
            elif ch == '"':
                state = "double"
            elif ch == "`" or text.startswith(("$(", "<(", ">("), i):
                live = True
            elif (text.startswith("--", i) and (i == 0 or text[i - 1] in " \t")
                  and (i + 2 == n or text[i + 2] in " \t")):
                dashes.append(i)
        i += 1
    return live, dashes


def heredoc_check(command: str, segments) -> Finding | None:
    """A heredoc whose delimiter is not quoted expands its body in the caller's own shell, quotes or not; the
    segments never show that body. A flotilla line with `$(` or a backtick in such a body is refused."""
    if "<<" not in command or not any(_runs_any_flotilla(argv(s.text)) for s in segments):
        return None
    from flotilla.guards.shell import heredoc
    lines = command.split("\n")
    for at, line in enumerate(lines):
        found = heredoc(line)
        if found:
            label, quoted = found
            if quoted:           # a delimiter quoted in any part: the body is data
                continue
            for body in lines[at + 1:]:
                if body.strip() == label:
                    break
                if "`" in body or "$(" in body:
                    return Finding(GUARD, True, "flotilla: a heredoc in this flotilla line holds a command "
                                                "substitution, which your own shell runs before flotilla starts. "
                                                "Quote the delimiter (<<'EOF') if the body is data.")
    return None


def check(segment) -> Finding | None:
    words = argv(segment.text)
    text = segment.text
    readings = _reads(words) + _parsed(words)
    live, dashes = _scan(text)
    if live and any(_is(command, "rig") for command, _move in readings):
        return Finding(GUARD, True, "flotilla: this rig line holds a command substitution (`$(..)`, a backtick, "
                                    "`<(..)`), which your own shell runs before flotilla starts - and some rig moves "
                                    "spend money. Quote it in single quotes if it is data. " + PLAINLY)
    if words and os.path.basename(words[0]) == "flotilla":
        for at in dashes:      # the `--` ending a `flotilla rig run`, as flotilla's own parser reads the words before it
            if ("rig", "run") in _parsed(argv(text[:at])):
                text = text[:at]   # what follows is the remote command: data to this guard
                break
    if _runs_any_flotilla(words) and (any(sign in text for sign in EXPANDS) or text.rstrip().endswith("\\")):
        return Finding(GUARD, True, "flotilla: this flotilla command holds what bash expands or continues "
                                    "(braces, $'...', $\"...\", an extglob, a trailing backslash), so the move it "
                                    "runs cannot be read before it runs - and `approve` is the person's own move. "
                                    "" + PLAINLY)
    rest = words[1:]
    braces = _brace_expands(text)
    if braces and "flotilla" in text:
        return Finding(GUARD, True, "flotilla: a brace here may expand into flotilla or its command (`{a,b}`), so the "
                                    "move it runs cannot be read before it runs. " + PLAINLY)
    built = (braces or any("$" in word or "`" in word for word in rest)) and any(word in GUARDED for word in rest)
    named = built or any(_may_be(word, "rig") or _may_be(word, "work") for word in rest)
    if (words and _opaque(words[0]) and named) or any(
            _opaque(command) or ((_is(command, "rig") or _is(command, "work")) and _opaque(move))
            for command, move in _reads(words)):
        return Finding(GUARD, True, "flotilla: the program, command or move here is a variable or a substitution, so "
                                    "what it runs cannot be read before it runs - and some flotilla moves are the "
                                    "person's own. " + PLAINLY)
    rig_move = _rig_move(words)
    if rig_move:
        return Finding(GUARD, True, f"flotilla: `rig {rig_move}` is the person's own move - it turns rented machines "
                                    "on or off, spends money, or changes who a paid session serves - and a Claude "
                                    "tool call is never the person's. Show "
                                    "the person the command; they type it with `!` in front in their own Claude Code "
                                    "session, or run it in a terminal.")
    if any(_is(command, "attention") and move for command, move in _parsed(words)) or any(
            _is(command, "attention") and move and not move.startswith("-") for command, move in _reads(words)):
        return Finding(GUARD, True, "flotilla: how the person is asked (`attention all` or `digest`) is the person's "
                                    "own choice, and a Claude tool call is never the person's. Show the person the "
                                    "command; they type it with `!` in front in their own Claude Code session, or run "
                                    "it in a terminal.")
    if _move(words) != "approve":
        return None
    return Finding(GUARD, True, "flotilla: approving work for trunk is the person's own move, and a Claude tool call "
                                "is never the person's. Show the person the command; they type it with `!` in front "
                                "in their own Claude Code session, or run it in a terminal.")


#: Programs that run code handed to them as text, on their command line or on their standard input.
_INTERPRETER = re.compile(r"(?:sh|bash|zsh|dash|ksh|fish|python(?:\d+(?:\.\d+)?)?|perl|ruby|node)")
#: A move only the person makes, spelled as the CLI reads it - in a command line or an argv list (`'rig', 'open'`).
#: `approve` alone is not one: `gh pr review --approve` and a grep for the word are ordinary work (review of bebab1b).
_PERSONS_MOVE = re.compile(r"\bwork\b[\s'\",\[\]+]+approve\b"
                           r"|\brig\b[\s'\",\[\]+]+(?:enable|disable|open|close|allow-image|unshare|share)\b"
                           r"|\battention\b[\s'\",\[\]+]+(?:all|digest)\b")
#: The same moves' words anywhere in the code - a command built in pieces - judged only where flotilla is named too.
_MOVE_WORDS = re.compile(r"\bapprove\b|\brig\b[\s\S]*?\b(?:enable|disable|open|close|allow-image|unshare|share)\b"
                         r"|\battention\b[\s\S]*?\b(?:all|digest)\b")
#: flotilla as the program a line runs - `flotilla`, `.../bin/flotilla`, `flotilla.cli`, `-m flotilla` - never a path
#: or a name it is part of (`flotilla-field`, `.flotilla/`, `~/.config/flotilla`): those are reads, not runs.
_FLOTILLA = re.compile(r"(?:^|[\s'\"(\[,;|&`=])(?:\S*/bin/)?flotilla(?![\w./-])|\bflotilla\.cli\b"
                       r"|-m[\s'\",]+flotilla\b")
#: flotilla at the end of any path (`~/plug/flotilla`): enough to refuse a person's move beside it, too wide to ask
#: about on its own - `ls ~/.config/flotilla` reads a folder.
_FLOTILLA_ANY = re.compile(r"(?:^|[\s'\"(\[,;|&`=])(?:\S*/)?flotilla(?![\w./-])|\bflotilla\.\w"
                           r"|-m[\s'\",]+flotilla\b|\b(?:from|import)\s+flotilla\b")
WRAPPED_REFUSAL = ("flotilla: this line hands code to an interpreter (`bash -c`, `python3 -c`, a pipe, a heredoc or a "
                   "here-string), and that code runs a move only the person makes - approving work, turning rented "
                   "machines on or off or spending on them, or how the person is asked. A Claude tool call never runs "
                   "it, wrapped or not: show the person the line; they type it with `!` in front in their own Claude "
                   "Code session, or run it in a terminal.")
WRAPPED_ASK = ("flotilla: this line hands flotilla commands to an interpreter as text (`bash -c`, `python3 -c`), "
               "which the guard cannot read move by move, so the person decides whether it runs. Written out as "
               "plain flotilla commands, one per call, it needs no one's yes.")
UNREADABLE_MOVE_ASK = ("flotilla: this code spells a move only the person makes (`work approve`, `rig enable`, "
                       "`attention digest`...) through a program the guard cannot name, so the person decides "
                       "whether it runs.")


def _judge(text: str, *, ask_on_flotilla: bool) -> Finding | None:
    """Refused: a person's move spelled as the CLI spells it beside flotilla named any way or a variable, or the
    move's words anywhere in code that names flotilla (a command built in pieces). Asked: such a move through a
    program the guard cannot name, or - on a command line's code - flotilla run as a program."""
    anywhere = bool(_FLOTILLA_ANY.search(text))
    if _PERSONS_MOVE.search(text):
        if anywhere or "$" in text or "`" in text:
            return Finding(GUARD, True, WRAPPED_REFUSAL)
        return Finding(GUARD, False, UNREADABLE_MOVE_ASK, ask=True)   # review of bebab1b: never a silent pass
    if anywhere and _MOVE_WORDS.search(text):
        return Finding(GUARD, True, WRAPPED_REFUSAL)
    if ask_on_flotilla and _FLOTILLA.search(text):
        return Finding(GUARD, False, WRAPPED_ASK, ask=True)
    return None


def _stricter(*found: Finding | None) -> Finding | None:
    return next((item for item in found if item is not None and item.refuse),
                next((item for item in found if item is not None), None))


def _local(text: str) -> str:
    """The text before the `--` that ends a `flotilla rig run`: what follows runs on a rented machine, as data to this
    guard (the same cut `check` makes)."""
    _live, dashes = _scan(text)
    for at in dashes:
        if ("rig", "run") in _parsed(argv(text[:at])) or any(_is(command, "rig") and _is(move, "run")
                                                              for command, move in _reads(argv(text[:at]))):
            return text[:at]
    return text


#: Per interpreter, the short option letters and the long options that hand it code as text (0.28.1). The reading
#: fails closed: one such option anywhere on the line, or a script that is a file descriptor or a process substitution,
#: makes the line code - an option the reader does not know never turns code into a script's arguments (the commit
#: security review of c4d060a: `bash --rcfile x -c ...`, `node --eval=...`, `fish --command`, `python3 <(...)`).
_CODE_FLAGS = {"shell": ("c", ("--command",)), "python": ("c", ()), "perl": ("eE", ()), "ruby": ("e", ()),
               "node": ("ep", ("--eval", "--print"))}
_FED_SCRIPT = re.compile(r"^(?:[<>]\(|/dev/stdin$|/dev/fd/|/proc/[^/]+/fd/)")


def _family(name: str) -> str:
    base = os.path.basename(name)
    if base.startswith("python"):
        return "python"
    return base if base in ("perl", "ruby", "node") else "shell"


def _hands_code(name: str, after: list[str]) -> bool:
    """Whether the interpreter's command line hands it code as text (`-c`, `-lc`, `-X dev -c`, `-e`, `--eval=...`),
    as against only running a script or a module (`python3 tools/x.py`, `python -m pytest`), whose arguments are
    data the guard reads whole. Any code option anywhere counts, whatever stands before it."""
    short, long_ = _CODE_FLAGS[_family(name)]
    for word in after:
        if _FED_SCRIPT.match(word):
            return True
        if word.startswith("--"):
            if word.split("=", 1)[0] in long_:
                return True
            continue
        if word.startswith("-") and len(word) > 1 and any(letter in short for letter in word[1:]):
            return True
    return False


def _interpreter_at(words: list[str]) -> int | None:
    return next((at for at, word in enumerate(words) if _INTERPRETER.fullmatch(os.path.basename(word))), None)


def whole_text(segments) -> str:
    """The command as the stdin check reads it: every segment, heredoc bodies included, each cut at its own `rig
    run`'s `--` - never the line cut at the first one, which would drop what follows (fourth security review)."""
    return "\n".join(_local(segment.text) for segment in segments)


def wrapped_check(segment, command: str, raw: str = "") -> Finding | None:
    """`command` is `whole_text` of the line's segments; `raw` the line as typed, which alone still shows its pipes.

    Code an interpreter is handed: every word after it on its command line, whatever its options are called
    (`-o pipefail -c`, `-Ic`, `-X dev -c`, `--eval`, `-p`, `-E`); and, when it reads its code from standard input -
    a pipe, a heredoc, a here-string - the whole command, bodies included. A person's move there is refused; flotilla
    named on a command line asks the person (the person, 2026-11-03). Code on standard input that only names flotilla
    is a helper script the guards do not read, as a script the session writes and runs is not."""
    words = argv(_local(segment.text))
    at = _interpreter_at(words)
    if at is None:
        return None
    after = words[at + 1:]
    # code handed as text asks when it names flotilla; a script's or a module's arguments are data the guard reads
    # whole - flotilla run by path or as a module is read by `check` - so they are judged for a person's move only
    # a process substitution is gone from the words once they are split: it is read in the segment's own text
    hands = _hands_code(words[at], after) or bool(re.search(r"[<>]\(", segment.text))
    on_line = None if all(word.startswith("-") for word in after) and not hands else \
        _judge(" ".join(after), ask_on_flotilla=hands)
    # whatever its arguments (`bash -s arg`, `python3 /dev/stdin`), an interpreter may read a heredoc, a here-string
    # or a pipe: then the whole command, bodies included, is its code too (third security review)
    fed = all(word.startswith("-") for word in after) or any(sign in (raw or command) for sign in ("<<", "|"))
    return _stricter(on_line, _judge(command, ask_on_flotilla=False) if fed else None)
