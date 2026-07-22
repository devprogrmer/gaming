"""The interactive, menu-driven experience (thin I/O loop).

This is the human-facing front end. After the Part E refactor it holds only:

    * the ``Menu`` class's input loop and prompt/choice plumbing, and
    * dispatch to the per-action functions in :mod:`gaming.interactive.actions`.

The actual business logic for each action (scan, discover, manage ranges,
history, settings, first-octet filter, update) lives in the ``actions`` package,
written against an :class:`~gaming.interactive.actions.context.ActionContext` so
it is decoupled from the terminal and reusable by the web layer. Formatting
still lives in :mod:`gaming.interactive.report`.

It is deliberately simple: numbered menus and prompts, standard-library only,
and it never asks the user to run ``fping``, ``tail``, ``watch``, or ad-hoc
scripts. Everything happens inside the loop.
"""

from __future__ import annotations

import sys
from collections.abc import Iterable
from typing import TextIO

from .. import __version__
from . import actions
from .actions.context import ActionContext
from .filters_shared import format_bare_ips as _format_bare_ips
from .filters_shared import matches_first_octet as _matches_first_octet
from .filters_shared import parse_first_octets as _parse_first_octets
from .progress import _supports_color
from .settings import Settings, load_settings
from .storage import HistoryStore


def parse_first_octets(input_str: str) -> list[int]:
    """Parse a comma-separated string of first octets into a validated list.

    Re-exported from :mod:`gaming.interactive.filters_shared` so the terminal
    menu and the web dashboard share one implementation.
    """
    return _parse_first_octets(input_str)


def matches_first_octet(prefix: str, allowed_octets: Iterable[int]) -> bool:
    """Return True if the first octet of ``prefix`` is in ``allowed_octets``.

    Re-exported from :mod:`gaming.interactive.filters_shared`.
    """
    return _matches_first_octet(prefix, allowed_octets)


def format_bare_ips(hosts: Iterable[str]) -> str:
    """Render hosts as a copy-paste-ready block: one bare IP per line.

    Re-exported from :mod:`gaming.interactive.filters_shared`.
    """
    return _format_bare_ips(hosts)


_BANNER = r"""
      _
   __| | _____   ___ __  _ __ ___   __ _ _ __ ___  ___ _ __
  / _` |/ _ \ \ / / '_ \| '__/ _ \ / _` | '__/ _ \/ _ \ '__|
 | (_| |  __/\ V /| |_) | | | (_) | (_| | | |  __/  __/ |
  \__,_|\___| \_/ | .__/|_|  \___/ \__, |_|  \___|\___|_|
                  |_|              |___/
"""


def render_banner(stream: TextIO | None = None) -> str:
    """Return the ``devprogrmer`` banner, coloured on a capable TTY.

    Falls back to plain ASCII (no escape codes) when the stream is not an
    ANSI-capable TTY, so it stays clean over SSH, in pipes, and in CI.
    """
    stream = stream or sys.stdout
    art = _BANNER.rstrip("\n")
    if _supports_color(stream):
        return f"\033[36m{art}\033[0m"
    return art


def _hr(width: int = 52) -> str:
    return "-" * width


_MENU = """
{banner}
==================================================
   devprogrmer * IP Health Scanner   (v{version})
==================================================
  1) Scan saved ranges (datacenter / CDN / both)
  2) Discover & save provider ranges
  3) Manage IP ranges
  4) View scan history
  5) Settings
  6) Update installed version
  7) Filter CIDRs by first octet
  8) Discover, save & scan a provider
  9) Launch web panel
  0) Exit
{rule}"""


class Menu:
    """Interactive menu loop with injectable I/O streams (testable)."""

    def __init__(
        self,
        *,
        stdin: TextIO | None = None,
        stdout: TextIO | None = None,
        store: HistoryStore | None = None,
    ) -> None:
        self.stdin = stdin or sys.stdin
        self.stdout = stdout or sys.stdout
        self.store = store or HistoryStore()
        self.settings: Settings = load_settings()

    # ---- I/O helpers -----------------------------------------------------
    def _print(self, text: str = "") -> None:
        self.stdout.write(text + "\n")
        self.stdout.flush()

    def _prompt(self, message: str) -> str:
        self.stdout.write(message)
        self.stdout.flush()
        line = self.stdin.readline()
        if not line:  # EOF (e.g. piped input exhausted) — behave like "exit".
            raise EOFError
        return line.strip()

    def _choose(self, title: str, options: list[tuple[str, str]]) -> str | None:
        """Render a titled numbered menu; return the chosen key or None."""
        self._print(f"\n{title}")
        for i, (_key, label) in enumerate(options, start=1):
            self._print(f"  {i}) {label}")
        raw = self._prompt("Choice: ").strip()
        if not raw.isdigit() or not (1 <= int(raw) <= len(options)):
            self._print("Unknown option.")
            return None
        return options[int(raw) - 1][0]

    # ---- action context --------------------------------------------------
    def _context(self) -> ActionContext:
        """Build an ActionContext bound to this menu's I/O and current state."""
        return ActionContext(
            settings=self.settings,
            store=self.store,
            stdout=self.stdout,
            print_=self._print,
            prompt=self._prompt,
            choose=self._choose,
        )

    def _dispatch(self, fn) -> None:
        """Run an action with a fresh context, syncing back settings edits.

        Actions may replace ``ctx.settings`` (the Settings editor's "reset to
        defaults" does), so the possibly-new value is copied back onto the menu
        afterwards. All other state (store, streams) is shared by reference.
        """
        ctx = self._context()
        fn(ctx)
        self.settings = ctx.settings

    # ---- main loop -------------------------------------------------------
    def run(self) -> int:
        self.store.initialize()
        first = True
        while True:
            banner = render_banner(self.stdout) if first else ""
            first = False
            self._print(_MENU.format(banner=banner, version=__version__, rule=_hr()))
            try:
                choice = self._prompt("Select an option: ")
            except EOFError:
                self._print("\nGoodbye.")
                return 0

            try:
                if choice == "1":
                    self._dispatch(actions.scan_saved)
                elif choice == "2":
                    self._dispatch(actions.discover_and_save)
                elif choice == "3":
                    self._dispatch(actions.manage_ranges)
                elif choice == "4":
                    self._dispatch(actions.history)
                elif choice == "5":
                    self._dispatch(actions.edit_settings)
                elif choice == "6":
                    self._dispatch(actions.update_installed_version)
                elif choice == "7":
                    self._dispatch(actions.filter_by_first_octet)
                elif choice == "8":
                    self._dispatch(actions.discover_save_scan_provider)
                elif choice == "9":
                    self._dispatch(actions.launch_web_panel)
                elif choice in ("0", "q", "quit", "exit"):
                    self._print("Goodbye.")
                    return 0
                else:
                    self._print("Unknown option. Please choose from the menu.")
            except EOFError:
                self._print("\nGoodbye.")
                return 0
            except KeyboardInterrupt:
                self._print("\nCancelled.")


def run(argv: list[str] | None = None) -> int:
    """Entry point for the interactive menu."""
    return Menu().run()
