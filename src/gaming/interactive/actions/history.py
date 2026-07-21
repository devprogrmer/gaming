"""Scan-history browsing action (menu option 4)."""

from __future__ import annotations

from .. import report as report_mod
from .context import ActionContext


def history(ctx: ActionContext) -> None:
    """List saved scans and, on request, show one scan's per-host results."""
    scans = ctx.store.list_scans(limit=20)
    ctx.print_("")
    ctx.print_(report_mod.render_history(scans))
    if not scans:
        return
    answer = ctx.prompt("Enter a scan ID to view details (blank to return): ").strip()
    if not answer:
        return
    try:
        scan_id = int(answer)
    except ValueError:
        ctx.print_("Not a valid scan ID.")
        return
    rows = ctx.store.get_results(scan_id)
    if not rows:
        ctx.print_(f"No results found for scan #{scan_id}.")
        return
    ctx.print_("")
    ctx.print_(report_mod.render_results(rows, stream=ctx.stdout, limit=50))
