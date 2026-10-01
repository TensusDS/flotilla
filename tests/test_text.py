from flotilla.core.text import strip_ansi

PYTEST = "\x1b[32m\x1b[32m\x1b[1m12 passed\x1b[0m\x1b[32m in 0.02s\x1b[0m\x1b[0m"


def test_strip_ansi_removes_colour_codes():
    assert strip_ansi(PYTEST) == "12 passed in 0.02s"


def test_strip_ansi_leaves_plain_text_and_keeps_link_text():
    assert strip_ansi("12 passed in 0.02s") == "12 passed in 0.02s"
    assert strip_ansi("see \x1b]8;;https://x.invalid\x07the docs\x1b]8;;\x07 now") == "see the docs now"


def test_visible_shows_every_control_and_reordering_character_as_an_escape():
    from flotilla.core.text import visible
    tricky = "npm test\r\x1b[2K\x1b[Gok\n\tx\x7f\x85‮y​z﻿"
    shown = visible(tricky)
    assert shown == "npm test\\r\\x1b[2K\\x1b[Gok\\n\\tx\\x7f\\x85\\u202ey\\u200bz\\ufeff"
    assert all(ch.isprintable() and ord(ch) < 0x2000 or ch == " " for ch in shown)
    assert visible("plain text, ünïcode ok") == "plain text, ünïcode ok"
