from flotilla.core.text import strip_ansi

PYTEST = "\x1b[32m\x1b[32m\x1b[1m12 passed\x1b[0m\x1b[32m in 0.02s\x1b[0m\x1b[0m"


def test_strip_ansi_removes_colour_codes():
    assert strip_ansi(PYTEST) == "12 passed in 0.02s"


def test_strip_ansi_leaves_plain_text_and_keeps_link_text():
    assert strip_ansi("12 passed in 0.02s") == "12 passed in 0.02s"
    assert strip_ansi("see \x1b]8;;https://x.invalid\x07the docs\x1b]8;;\x07 now") == "see the docs now"


def test_visible_shows_every_control_and_reordering_character_as_an_escape():
    from flotilla.core.text import visible
    tricky = "npm test\r\x1b[2K\x1b[Gok\n\tx\x7f\x85\u202ey\u200bz\ufeff"
    shown = visible(tricky)
    assert shown == "npm test\\r\\x1b[2K\\x1b[Gok\\n\\tx\\x7f\\x85\\u202ey\\u200bz\\ufeff"
    assert all(ch.isprintable() and ord(ch) < 0x2000 or ch == " " for ch in shown)
    assert visible("plain text, ünïcode ok") == "plain text, ünïcode ok"


def test_visible_also_shows_line_separators_fillers_and_tag_characters():
    """Asked by Unicode category, not by a list of code points: U+2028 ends a line for Python's splitlines, a Hangul
    filler or a braille blank looks like a space, and a tag character is invisible to the person but read by the
    model (security review of 203ac1c)."""
    from flotilla.core.text import visible
    assert visible("a\u2028b\u2029c") == "a\\u2028b\\u2029c"
    assert visible(f"x{chr(0x3164)}y{chr(0x2800)}z{chr(0xa0)}w") == "x\\u3164y\\u2800z\\xa0w"
    assert visible("ok\U000e0041") == "ok\\U000e0041"
    assert visible(f"soft{chr(0xad)}hy{chr(0x61c)}phen") == "soft\\xadhy\\u061cphen"
