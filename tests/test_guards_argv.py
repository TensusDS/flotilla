"""The person guard reads the arguments bash hands flotilla - measured against bash itself, not assumed."""

import shutil
import subprocess

import pytest

from flotilla.guards import person

CASES = [
    "flotilla rig open --hours 1",
    "flotilla rig 2>/dev/null open --hours 1",
    "flotilla rig>/dev/null open",
    "flotilla rig >o open",
    "flotilla rig > o open",
    "flotilla 2>/dev/null rig open",
    "flotilla rig <<<x open",
    "flotilla rig 1>o close",
    "flotilla work 2>&1 approve feat/x",
    "flotilla rig >&2 open",
    "flotilla rig &>/dev/null open",
    "flotilla work >>log approve feat/x",
    "flotilla work 2>/dev/null approve<x feat/x",
    "flotilla work --as '>' approve feat/x",
    "flotilla work --as \">\" approve feat/x",
    "flotilla work --as \\> approve feat/x",
    "flotilla w'or'k app\"rove\" feat/x",
    "flotilla wo\\rk approve feat/x",
    "flotilla work 'a b' \"c\\\"d\" e\\ f",
    "flotilla rig open # a comment",
    "flotilla rig op#en",
    "flotilla rig '#' open",
    "flotilla work 2> o approve x",
    "flotilla work 12>o approve x",
    "flotilla work a2>o approve x",
    "flotilla rig <> o open",
    "flotilla rig >| o open",
    "flotilla rig 3<&0 open",
    "flotilla rig >&- open",
    "flotilla rig 2>&- open",
    "flotilla rig >& o open",
    "flotilla rig >&o open",
    "flotilla work approve feat/x 2>&1 >out",
    "FOO=1 flotilla rig open",
]


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
@pytest.mark.parametrize("command", CASES)
def test_the_guard_reads_the_arguments_bash_hands_the_program(tmp_path, command):
    (tmp_path / "x").write_text("")
    out = tmp_path / "argv"
    script = f'flotilla() {{ for a in "$@"; do printf "%s\\0" "$a"; done >> "{out}"; }}\n{command}\n'
    subprocess.run(["bash", "-c", script], cwd=tmp_path, check=True, capture_output=True, timeout=10)
    told = out.read_text().split("\0")[:-1] if out.exists() else []
    read = person.argv(command)
    assert read[read.index("flotilla") + 1:] == told
