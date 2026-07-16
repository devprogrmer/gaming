"""Per-user data directory resolution for persistent interactive state.

Everything the interactive mode needs to persist (scan history database,
custom IP ranges, and settings) lives under a single application data
directory. The location follows platform conventions and can be overridden
with the ``GAMING_HOME`` environment variable (useful for tests and CI).
"""

from __future__ import annotations

import os
from pathlib import Path

_ENV_OVERRIDE = "GAMING_HOME"
_APP_DIRNAME = "gaming"


def app_home() -> Path:
    """Return the application data directory, creating it if necessary.

    Resolution order:
      1. ``$GAMING_HOME`` if set (used verbatim).
      2. ``%LOCALAPPDATA%\\gaming`` on Windows.
      3. ``$XDG_DATA_HOME/gaming`` if ``XDG_DATA_HOME`` is set.
      4. ``~/.local/share/gaming`` on Linux/macOS as a sensible default.
    """
    override = os.environ.get(_ENV_OVERRIDE)
    if override:
        home = Path(override).expanduser()
    elif os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")
        home = Path(base) / _APP_DIRNAME
    else:
        xdg = os.environ.get("XDG_DATA_HOME")
        base = Path(xdg).expanduser() if xdg else (Path.home() / ".local" / "share")
        home = base / _APP_DIRNAME

    home.mkdir(parents=True, exist_ok=True)
    return home


def database_path() -> Path:
    """Path to the SQLite scan-history database."""
    return app_home() / "history.db"


def settings_path() -> Path:
    """Path to the interactive settings JSON file."""
    return app_home() / "settings.json"


def custom_ranges_path() -> Path:
    """Path to the user's custom IP ranges file (one CIDR per line)."""
    return app_home() / "custom_ranges.txt"
