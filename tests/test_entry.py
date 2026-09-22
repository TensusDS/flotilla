import ast
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENTRY = ROOT / "scripts" / "flotilla"


def run(entry, *args, cwd=None):
    return subprocess.run([sys.executable, str(entry), *args], capture_output=True, text=True, cwd=cwd)


def test_entry_parses_with_an_old_grammar():
    # An old python3 must reach the version message, not a SyntaxError.
    ast.parse(ENTRY.read_text(encoding="utf-8"), feature_version=(3, 7))


def test_entry_is_executable():
    assert os.access(ENTRY, os.X_OK)


def test_version_prints_the_package_version():
    from flotilla import __version__
    done = run(ENTRY, "version")
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == __version__


def test_entry_works_from_any_directory_and_through_a_symlink(tmp_path):
    link = tmp_path / "flotilla"
    link.symlink_to(ENTRY)
    done = run(link, "version", cwd=tmp_path)
    assert done.returncode == 0, done.stderr


def test_old_interpreter_gets_a_message_not_a_traceback(tmp_path):
    source = ENTRY.read_text(encoding="utf-8").replace("MINIMUM = (3, 11)", "MINIMUM = (99, 0)")
    assert "MINIMUM = (99, 0)" in source, "the gate constant moved; update this test"
    fake = tmp_path / "flotilla"
    fake.write_text(source, encoding="utf-8")
    done = run(fake, "version")
    assert done.returncode == 3
    assert "flotilla needs Python 99.0 or newer" in done.stderr
    assert "Traceback" not in done.stderr


def test_unknown_subcommand_is_a_usage_error():
    done = run(ENTRY, "no-such-command")
    assert done.returncode == 2
    # Python also exits 2 when the script is missing; only argparse says "invalid choice".
    assert "invalid choice" in done.stderr
