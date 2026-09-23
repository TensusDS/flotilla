"""The repository is English-only (spec, section 12). Cyrillic in tests is written as escapes."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import check_no_cyrillic as gate  # noqa: E402


def test_clean_text_has_no_hits(tmp_path):
    (tmp_path / "a.md").write_text("plain English\n", encoding="utf-8")
    assert gate.find_cyrillic(tmp_path, ["a.md"]) == []


def test_cyrillic_is_reported_with_file_and_line(tmp_path):
    (tmp_path / "a.md").write_text("first\nsecond \u0436\u0443\u043a\n", encoding="utf-8")
    hits = gate.find_cyrillic(tmp_path, ["a.md"])
    assert len(hits) == 1
    assert hits[0].startswith("a.md:2: ")


def test_binary_and_missing_files_are_skipped(tmp_path):
    (tmp_path / "b.bin").write_bytes(b"\xff\xfe\x00\x01")
    assert gate.find_cyrillic(tmp_path, ["b.bin", "missing.txt"]) == []


def test_this_repository_is_clean():
    done = subprocess.run([sys.executable, str(ROOT / "tools" / "check_no_cyrillic.py")],
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stdout + done.stderr


def test_repository_tracks_no_bytecode():
    done = subprocess.run(["git", "-C", str(ROOT), "ls-files"], capture_output=True, text=True, check=True)
    tracked = [p for p in done.stdout.splitlines() if p.endswith(".pyc") or "__pycache__" in p]
    assert tracked == []
