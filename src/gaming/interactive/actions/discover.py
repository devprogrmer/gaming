"""Discover & save interactive action (menu option 2)."""

from __future__ import annotations

from .. import ranges as ranges_mod
from .common import hr, run_seeded_discovery
from .context import ActionContext


def discover_and_save(ctx: ActionContext) -> None:
    """Discover CIDRs across many providers and auto-save them by category.

    Aggregates the bundled provider seed data (broad, deterministic coverage
    across datacenter/hosting/cloud/CDN for Iran and foreign) together with live
    pipeline discovery seeded by those providers' ASNs, classifies every record,
    and persists the CIDRs into Manage IP Ranges. Seeding the live pipeline is
    what makes real discovery run at all: without seed ASNs every source returns
    nothing and the pipeline silently swaps in the sample set.
    """
    from .. import providers as providers_mod

    ctx.print_("\nDiscovering provider ranges (seed data + live sources)...")
    records = run_seeded_discovery(ctx, providers_mod.load_providers())

    added = ranges_mod.persist_records(records)
    total = sum(added.values())
    ctx.print_(f"\n{hr()}")
    if not total:
        ctx.print_("No new CIDRs to save — everything discovered is already stored.")
    else:
        ctx.print_(f"Saved {total} new CIDR(s) into Manage IP Ranges:")
        for category in ranges_mod.CATEGORIES:
            if category in added:
                ctx.print_(f"  {category:20s} +{added[category]}")
    ctx.print_("\nStored totals by category:")
    for category in ranges_mod.CATEGORIES:
        ctx.print_(f"  {category:20s} {len(ranges_mod.load_category(category))}")
    ctx.print_(
        "\nThese persist across restarts — scan them any time from 'Scan saved ranges'."
    )
