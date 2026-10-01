import pytest

from flotilla.guards import line_edit
from flotilla.guards import shell


@pytest.mark.parametrize("command", [
    "sed -i '12d' f.txt",
    "sed -i.bak -e '3,5s/a/b/' f.txt",
    "sed -Ei '10i inserted' f.txt",
    "sed -i '' '5d' f.txt",
    "gsed --in-place=.orig '1~2d' f.txt",
    "sed -i -e 's/a/b/' -e '7d' f.txt",
    "sed -i '5!d' f.txt",
    "sed -i '3,/end/d' f.txt",    # the start is a counted line: stale after an edit above it
    "sed -i '1,/x/{5d}' f.txt",   # review of 0.5.0, M4: the from-the-top range must not hide a counted line inside
    "sed -i '0,/x/{ 2d; }' f.txt",
])
def test_an_in_place_edit_by_line_number_is_refused(command, tmp_path):
    found = [f for f in (line_edit.check(s) for s in shell.segments(command, tmp_path)) if f]
    assert found and all(f.refuse and "by its text" in f.text for f in found)


@pytest.mark.parametrize("command", [
    "sed -n '5p' f.txt",
    "sed -i 's/one/two/' f.txt",
    "sed -i '/^old$/d' f.txt",
    "sed -i '$d' f.txt",
    "sed -i 's/x/1;2/' f.txt",
    "sed -i -f script.sed f.txt",
    # field test W4: `0,/re/` is GNU sed's "from the top to the first match" - both ends are found, not counted;
    # `1,/re/` is the portable spelling. Nothing can be inserted above line 1, so that start never goes stale.
    "sed -i '0,/^version/s//release/' f.txt",
    "sed -i -e '1,/^---$/d' f.txt",
])
def test_everything_else_passes(command, tmp_path):
    # every segment the hook would judge, not only the first: a plain split cuts a quoted script at `;`
    assert all(line_edit.check(s) is None for s in shell.segments(command, tmp_path))


def test_a_digit_is_an_address_only_before_a_command():
    assert line_edit.numeric_address("3,5s/a/b/")
    assert not line_edit.numeric_address("s/a/b/;2/")
