"""Shared terminal styling and table rendering (stdlib only).

One place for the ANSI palette and the column-aligned table used by the
interactive menu, ``gaming sources``, ``gaming validate-seed``, and the scan
reports — previously each of those grew its own ``ljust`` loop, so column
padding and header style drifted apart.

Colour is opt-in per stream and always routed through
:func:`gaming.interactive.progress._supports_color`, the same predicate the
banner already used. That means every style helper here degrades to clean plain
ASCII when output is piped, redirected, run under CI, or when ``NO_COLOR`` is
set — the rendered text is byte-for-byte the uncoloured version, not merely
"colour that happens to be off".

Nothing here animates or repositions the cursor; a table written to a pipe is
just text.
"""

from __future__ import annotations

import sys
from typing import TextIO

from .progress import _supports_color

# ---- palette -------------------------------------------------------------
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"

CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
GRAY = "\033[90m"
WHITE = "\033[97m"

# Semantic roles, so call sites ask for meaning rather than a colour and the
# palette can change in one place.
_ROLES = {
    "title": BOLD + CYAN,
    "header": BOLD,
    "prompt": CYAN,
    "muted": GRAY,
    "ok": GREEN,
    "warn": YELLOW,
    "error": RED,
    "accent": CYAN,
    "rule": GRAY,
}


def style(text: str, role: str, stream: TextIO | None = None) -> str:
    """Wrap ``text`` in the ANSI codes for ``role`` if ``stream`` supports colour.

    Returns ``text`` unchanged on a non-TTY (pipe, redirect, CI) or when the
    role is unknown, so callers can style unconditionally.
    """
    stream = stream or sys.stdout
    if not _supports_color(stream):
        return text
    code = _ROLES.get(role)
    return f"{code}{text}{RESET}" if code else text


def heading(text: str, stream: TextIO | None = None, *, underline: bool = True) -> str:
    """A section heading: styled title plus an underline rule of matching width.

    The underline is plain ASCII so the heading still reads as a heading when
    colour is unavailable — that is the only cue a piped reader gets.
    """
    stream = stream or sys.stdout
    out = style(text, "title", stream)
    if not underline:
        return out
    return out + "\n" + style("─" * len(text) if _supports_color(stream)
                              else "-" * len(text), "rule", stream)


def bullet(text: str, stream: TextIO | None = None) -> str:
    mark = "•" if _supports_color(stream or sys.stdout) else "*"
    return f"  {style(mark, 'accent', stream)} {text}"


def key_value(label: str, value: str, *, width: int = 0,
              stream: TextIO | None = None) -> str:
    """A padded ``label: value`` line, label muted so the value stands out."""
    padded = label.ljust(width) if width else label
    return f"{style(padded, 'muted', stream)}  {value}"


# ---- tables --------------------------------------------------------------
class Column:
    """A table column.

    ``align`` is ``"left"`` or ``"right"`` — numeric columns (latency, counts)
    read far better right-aligned. ``style_fn`` optionally maps a cell's raw
    text to a styled version; it is applied *after* padding is computed, so
    invisible ANSI bytes never disturb column alignment.
    """

    __slots__ = ("label", "align", "style_fn")

    def __init__(self, label: str, *, align: str = "left", style_fn=None) -> None:
        self.label = label
        self.align = align
        self.style_fn = style_fn


def render_table(
    columns: list[Column | str],
    rows: list[list[str]],
    *,
    stream: TextIO | None = None,
    indent: str = "",
    empty: str = "No results.",
) -> str:
    """Render ``rows`` as an aligned table with a styled header and rule.

    Widths are computed from the *unstyled* text, so a coloured cell occupies
    exactly the same number of visible columns as a plain one. This is the
    single table renderer for the whole terminal UI.
    """
    stream = stream or sys.stdout
    cols = [c if isinstance(c, Column) else Column(c) for c in columns]
    if not rows:
        return f"{indent}{style(empty, 'muted', stream)}\n"

    # Normalise ragged rows rather than raising -- a display helper should not
    # be able to crash a scan that already succeeded.
    ncols = len(cols)
    norm = [[str(cell) for cell in row[:ncols]] + [""] * (ncols - len(row))
            for row in rows]

    widths = [
        max(len(cols[i].label), *(len(row[i]) for row in norm))
        for i in range(ncols)
    ]

    def pad(text: str, i: int) -> str:
        if cols[i].align == "right":
            return text.rjust(widths[i])
        return text.ljust(widths[i])

    header = "  ".join(
        style(pad(cols[i].label, i), "header", stream) for i in range(ncols)
    )
    rule = "  ".join(style("-" * widths[i], "rule", stream) for i in range(ncols))

    lines = [indent + header.rstrip(), indent + rule]
    for row in norm:
        cells = []
        for i, raw in enumerate(row):
            text = pad(raw, i)
            fn = cols[i].style_fn
            if fn is not None:
                # Style the visible token only, preserving the padding we just
                # computed so the column stays aligned.
                styled = fn(raw, stream)
                if styled != raw:
                    text = text.replace(raw, styled, 1)
            cells.append(text)
        # Trailing padding on the final column is invisible but shows up in
        # diffs, `cat -A`, and copied text -- strip it.
        lines.append((indent + "  ".join(cells)).rstrip())
    return "\n".join(lines) + "\n"


def verdict_style(raw: str, stream: TextIO | None = None) -> str:
    """``style_fn`` for verdict columns — reuses the existing verdict palette."""
    from .progress import colorize

    return colorize(raw, stream)
