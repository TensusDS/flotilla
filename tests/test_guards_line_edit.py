import pytest

from flotilla.guards import line_edit
from guardkit import first


@pytest.mark.parametrize("command", [
    "sed -i '12d' f.txt",
    "sed -i.bak -e '3,5s/a/b/' f.txt",
    "sed -Ei '10i inserted' f.txt",
    "sed -i '' '5d' f.txt",
    "gsed --in-place=.orig '1~2d' f.txt",
    "sed -i -e 's/a/b/' -e '7d' f.txt",
    "sed -i '5!d' f.txt",
])
def test_an_in_place_edit_by_line_number_is_refused(command, tmp_path):
    found = line_edit.check(first(command, tmp_path))
    assert found.refuse and "by its text" in found.text


@pytest.mark.parametrize("command", [
    "sed -n '5p' f.txt",
    "sed -i 's/one/two/' f.txt",
    "sed -i '/^old$/d' f.txt",
    "sed -i '$d' f.txt",
    "sed -i 's/x/1;2/' f.txt",
    "sed -i -f script.sed f.txt",
])
def test_everything_else_passes(command, tmp_path):
    assert line_edit.check(first(command, tmp_path)) is None


def test_a_digit_is_an_address_only_before_a_command():
    assert line_edit.numeric_address("3,5s/a/b/")
    assert not line_edit.numeric_address("s/a/b/;2/")
