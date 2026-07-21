"""Shared context for interactive menu actions.

Part E refactor: the per-action logic that used to live inline on the ``Menu``
class now lives in the sibling modules of this package. Each action function
takes an :class:`ActionContext` instead of ``self`` so it depends only on a
small, explicit I/O surface (print / prompt / choose) plus the ``settings`` and
``store`` it operates on — never on a terminal directly. The terminal ``Menu``
builds a context backed by its stdin/stdout; a future web handler can build one
backed by request/response objects and call the exact same functions.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TextIO

from ..settings import Settings
from ..storage import HistoryStore


@dataclass(slots=True)
class ActionContext:
    """The I/O + state surface an action needs, decoupled from the terminal.

    ``print_`` writes a line; ``prompt`` asks for a line of input (raising
    ``EOFError`` at end of input, matching the terminal menu's behaviour);
    ``choose`` renders a titled numbered menu and returns the chosen key (or
    ``None``). ``stdout`` is exposed for the few helpers (progress bar, coloured
    report rendering) that need the raw stream.
    """

    settings: Settings
    store: HistoryStore
    stdout: TextIO
    print_: Callable[[str], None]
    prompt: Callable[[str], str]
    choose: Callable[[str, list[tuple[str, str]]], str | None]
