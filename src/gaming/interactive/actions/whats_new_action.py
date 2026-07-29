"""Show what the 24/7 watcher discovered since this surface last looked.

Both halves live here: :func:`whats_new_notice`, the one-line banner the menu
prints on startup, and :func:`whats_new`, menu option 11, which shows the detail
and only then marks the entries as seen. Acknowledging on the detail view rather
than on the banner means a notice cannot be cleared by someone who never read
what it was about.
"""

from __future__ import annotations

from .. import whats_new as whats_new_mod
from .context import ActionContext


def whats_new_notice(store) -> str:
    """A one-line banner for the menu header, or "" when there is nothing new.

    Fail-soft: a ledger that cannot be read must not stop the menu from opening,
    so any error yields no banner rather than a traceback.
    """
    try:
        result = whats_new_mod.whats_new(store, whats_new_mod.MENU, limit=1000)
    except Exception:  # noqa: BLE001 - a banner is never worth a crash
        return ""
    if not result.has_new:
        return ""
    return f"{result.summary()} Choose 11 to see them."


def whats_new(ctx: ActionContext) -> None:
    result = whats_new_mod.whats_new(ctx.store, whats_new_mod.MENU)
    ctx.print_(whats_new_mod.render(result, ctx.stdout))
    if not result.has_new:
        return

    # Only now are they genuinely seen, and only up to what was shown, so a
    # watcher tick during the read stays unread.
    whats_new_mod.acknowledge(ctx.store, whats_new_mod.MENU, up_to_id=result.up_to_id)
    ctx.print_("Marked as seen. The web dashboard keeps its own notice.")
