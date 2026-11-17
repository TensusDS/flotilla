"""Claude Code's own processes, every project's, as `flotilla lane` shows them (field notes 2026-11-16)."""

import os

from flotilla.core import resources
from flotilla.lane import commands


def proc(root, pid, argv, pages):
    entry = root / str(pid)
    entry.mkdir()
    (entry / "cmdline").write_bytes(b"\0".join(word.encode() for word in argv) + b"\0")
    (entry / "statm").write_text(f"999 {pages} 0 0 0 0 0\n", encoding="utf-8")


def test_claude_codes_processes_are_counted_by_their_first_word(tmp_path):
    page = os.sysconf("SC_PAGE_SIZE")
    per = 256 * 2**20 // page
    proc(tmp_path, 10, ["claude", "--resume"], per)
    proc(tmp_path, 11, ["/home/u/.local/share/claude/versions/2.1.326", "--bg"], per)   # the native binary
    proc(tmp_path, 12, ["claude bg-pty-host --bg-pty-host /tmp/x"], per)                 # a title it set itself
    proc(tmp_path, 13, ["/usr/bin/node", "/usr/lib/node_modules/@anthropic-ai/claude-code/cli.js"], per)
    proc(tmp_path, 14, ["/home/u/.local/bin/claude", "daemon", "run"], per)
    proc(tmp_path, 20, ["bash", "-c", "claude"], per)                                    # a shell: not Claude Code
    proc(tmp_path, 21, ["python3", "-m", "pytest"], per)
    proc(tmp_path, 22, ["claude-lookalike"], per)
    (tmp_path / "self").mkdir()
    gone = tmp_path / "30"
    gone.mkdir()   # exited between the listing and the read
    assert resources.claude_memory(proc_root=tmp_path) == (5, 5 * 256)


def test_no_proc_reads_as_unknown(tmp_path):
    assert resources.claude_memory(proc_root=tmp_path / "absent") is None


def test_the_sessions_line_names_the_line_and_the_floor():
    line = commands.sessions_line(2150, found=(42, 9216), line_mb=1638)
    assert "42 Claude Code process(es) hold 9.0 GB" in line and "under 1638 MB" in line and "2150 MB" in line
    unknown = commands.sessions_line(1500, found=None, line_mb=0)
    assert "not read on this platform" in unknown and "Claude Code stops" not in unknown
