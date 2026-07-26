from __future__ import annotations

import io
import re

from gaming.interactive import theme

_ANSI = re.compile(r"\033\[[0-9;]*m")


class _TTY(io.StringIO):
    """A stream that claims to be an interactive terminal."""

    def isatty(self) -> bool:
        return True


class _Pipe(io.StringIO):
    """A stream that is explicitly not a terminal (redirect/pipe/CI)."""

    def isatty(self) -> bool:
        return False


def test_style_is_a_noop_when_not_a_tty():
    assert theme.style("hello", "title", _Pipe()) == "hello"


def test_style_colours_on_a_tty():
    out = theme.style("hello", "title", _TTY())
    assert out != "hello"
    assert _ANSI.sub("", out) == "hello"


def test_style_respects_no_color(monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    assert theme.style("hello", "title", _TTY()) == "hello"


def test_unknown_role_is_left_unstyled():
    assert theme.style("hello", "not-a-role", _TTY()) == "hello"


def test_table_renders_plain_ascii_when_piped():
    out = theme.render_table(
        [theme.Column("HOST"), theme.Column("MS", align="right")],
        [["10.0.0.1", "1.5"]],
        stream=_Pipe(),
    )
    assert "\033" not in out
    assert "HOST" in out and "10.0.0.1" in out


def test_table_colour_does_not_change_layout():
    """The central invariant: colour must be purely additive.

    Stripping ANSI from the TTY rendering must yield byte-for-byte the piped
    rendering. If padding were computed on styled text, the invisible escape
    bytes would count toward column width and the plain output would misalign.
    """
    columns = [
        theme.Column("HOST"),
        theme.Column("HEALTH", style_fn=theme.verdict_style),
        theme.Column("AVG", align="right"),
    ]
    rows = [
        ["185.143.232.14", "GOOD", "18.4"],
        ["77.36.164.9", "MEDIUM", "128.3"],
        ["2.144.12.88", "BAD", "0.0"],
    ]
    coloured = theme.render_table(columns, rows, stream=_TTY())
    plain = theme.render_table(columns, rows, stream=_Pipe())

    assert "\033" in coloured  # the TTY version really is styled
    assert _ANSI.sub("", coloured) == plain


def test_table_right_aligns_numeric_columns():
    out = theme.render_table(
        [theme.Column("HOST"), theme.Column("AVG", align="right")],
        [["a", "1.5"], ["b", "128.25"]],
        stream=_Pipe(),
    )
    lines = out.splitlines()
    # Both values end at the same column; the shorter one is left-padded.
    assert lines[2].endswith("   1.5")
    assert lines[3].endswith("128.25")


def test_table_empty_rows_render_placeholder():
    out = theme.render_table(
        [theme.Column("HOST")], [], stream=_Pipe(), empty="Nothing here."
    )
    assert out.strip() == "Nothing here."


def test_table_tolerates_ragged_rows():
    """A short row must not crash rendering of an otherwise-good scan."""
    out = theme.render_table(
        [theme.Column("A"), theme.Column("B"), theme.Column("C")],
        [["1"], ["1", "2", "3"]],
        stream=_Pipe(),
    )
    assert "1" in out and "3" in out


def test_table_has_no_trailing_whitespace():
    out = theme.render_table(
        [theme.Column("HOST"), theme.Column("NOTE")],
        [["10.0.0.1", "x"], ["10.0.0.100", "yy"]],
        stream=_Pipe(),
    )
    for line in out.splitlines():
        assert line == line.rstrip(), f"trailing whitespace in {line!r}"


def test_heading_degrades_to_ascii_underline():
    out = theme.heading("Section", _Pipe())
    assert out == "Section\n-------"


def test_menu_renders_identically_plain_and_stripped():
    """The interactive menu must be unchanged for piped/scripted callers."""
    from gaming.interactive.menu import render_menu

    coloured = render_menu(_TTY())
    plain = render_menu(_Pipe())
    assert _ANSI.sub("", coloured) == plain
    # Option numbers survive so scripted input still maps to the same actions.
    for key in ("1)", "9)", "0)"):
        assert key in plain
