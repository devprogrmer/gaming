"""Dependency-free live progress bar for scans.

Renders an in-place progress bar to a stream using a carriage return, so the
user sees live scan progress without needing ``watch``, ``tail``, or any
third-party library. Running verdict counts (GOOD / MEDIUM / BAD) are shown
alongside the bar.

Colour is used only when the target stream is a TTY and the platform/terminal
is likely to support ANSI; otherwise it degrades to plain text.
"""

from __future__ import annotations

import os
import sys
from typing import TextIO

from .classify import BAD, GOOD, MEDIUM

_RESET = "\033[0m"
_COLORS = {
    GOOD: "\033[32m",  # green
    MEDIUM: "\033[33m",  # yellow
    BAD: "\033[31m",  # red
}


def _supports_color(stream: TextIO) -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if not hasattr(stream, "isatty") or not stream.isatty():
        return False
    # Windows 10+ terminals and most others support ANSI; the CLI already
    # reconfigures stdio to UTF-8. Honour an explicit opt-out only.
    return True


def colorize(label: str, stream: TextIO | None = None) -> str:
    """Wrap a verdict label in colour if the stream supports it."""
    stream = stream or sys.stdout
    if not _supports_color(stream):
        return label
    color = _COLORS.get(label, "")
    return f"{color}{label}{_RESET}" if color else label


class ProgressBar:
    """A single-line, in-place progress bar with verdict tallies."""

    def __init__(
        self,
        total: int,
        *,
        stream: TextIO | None = None,
        width: int = 30,
        label: str = "Scanning",
    ) -> None:
        self.total = max(0, int(total))
        self.stream = stream or sys.stdout
        self.width = max(10, int(width))
        self.label = label
        self.done = 0
        self.counts = {GOOD: 0, MEDIUM: 0, BAD: 0}
        self._active = self.total > 0 and _stream_is_interactive(self.stream)

    def update(self, verdict: str | None = None) -> None:
        """Advance the bar by one unit, optionally recording a verdict."""
        self.done += 1
        if verdict in self.counts:
            self.counts[verdict] += 1
        if self._active:
            self._render()

    def _render(self) -> None:
        frac = self.done / self.total if self.total else 1.0
        frac = min(1.0, max(0.0, frac))
        filled = int(self.width * frac)
        bar = "#" * filled + "-" * (self.width - filled)
        tally = (
            f"G:{self.counts[GOOD]} "
            f"M:{self.counts[MEDIUM]} "
            f"B:{self.counts[BAD]}"
        )
        line = (
            f"\r{self.label} [{bar}] "
            f"{self.done}/{self.total} ({frac * 100:5.1f}%)  {tally}"
        )
        try:
            self.stream.write(line)
            self.stream.flush()
        except (OSError, ValueError):
            self._active = False

    def finish(self) -> None:
        """Terminate the progress line so subsequent output starts cleanly."""
        if self._active:
            try:
                self.stream.write("\n")
                self.stream.flush()
            except (OSError, ValueError):
                pass


def _stream_is_interactive(stream: TextIO) -> bool:
    return bool(getattr(stream, "isatty", lambda: False)())
