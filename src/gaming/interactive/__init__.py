"""Interactive-mode helpers for the gaming tool.

This subpackage adds a menu-driven experience on top of the existing
discovery / reachability engine:

  * :mod:`gaming.interactive.paths`     — per-user data directory resolution
  * :mod:`gaming.interactive.storage`   — SQLite persistence for scan history
  * :mod:`gaming.interactive.ranges`    — bundled/editable Iran & foreign ranges
  * :mod:`gaming.interactive.classify`  — GOOD / MEDIUM / BAD health scoring
  * :mod:`gaming.interactive.scanner`   — alive discovery + latency scanning
  * :mod:`gaming.interactive.progress`  — dependency-free live progress bar
  * :mod:`gaming.interactive.menu`      — the top-level interactive menu loop

Everything here is standard-library only, matching the rest of the project.
"""

from __future__ import annotations

__all__ = [
    "paths",
    "storage",
    "ranges",
    "classify",
    "scanner",
    "progress",
    "menu",
]
