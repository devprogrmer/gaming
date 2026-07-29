"""Interactive menu actions, split out of ``menu.py`` (Part E refactor).

Each action takes an :class:`~gaming.interactive.actions.context.ActionContext`
rather than the ``Menu`` instance, so its business logic is decoupled from the
terminal and can be driven by a web handler too. ``menu.py`` keeps only the I/O
loop, prompt/choice plumbing, and dispatch to these functions.
"""

from __future__ import annotations

from .context import ActionContext
from .discover import discover_and_save
from .filter_octet import auto_scan_matched, filter_by_first_octet
from .history import history
from .provider_lookup_action import lookup_provider
from .ranges_action import manage_ranges
from .scan import discover_save_scan_provider, full_scan_hosts, scan_saved
from .settings_action import edit_settings
from .update_action import update_installed_version
from .web_action import launch_web_panel

__all__ = [
    "ActionContext",
    "scan_saved",
    "discover_save_scan_provider",
    "full_scan_hosts",
    "discover_and_save",
    "manage_ranges",
    "history",
    "edit_settings",
    "update_installed_version",
    "filter_by_first_octet",
    "auto_scan_matched",
    "launch_web_panel",
    "lookup_provider",
]
