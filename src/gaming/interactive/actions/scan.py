"""Scan-related interactive actions: scan saved ranges, provider discover+scan."""

from __future__ import annotations

from .. import ranges as ranges_mod
from .. import report as report_mod
from .. import scanner
from ..progress import ProgressBar
from .common import (
    ORIGIN_LABELS,
    categories_for,
    map_hosts_to_entries,
    render_scan_results,
    run_seeded_discovery,
)
from .context import ActionContext


def scan_saved(ctx: ActionContext) -> None:
    """Ask which class/origin to scan, then scan the saved category ranges.

    The user picks an origin (Iran / Foreign / Both) and a class (Datacenter /
    CDN-Cloud / Both); those resolve to storage categories, and the CIDRs
    previously saved under them (via discovery or manual add) are loaded from
    Manage IP Ranges and scanned. Nothing is re-entered by hand and nothing is
    re-discovered here.
    """
    origin = ctx.choose(
        "Which origin?",
        [("iran", "Iran"), ("foreign", "Foreign"), ("both", "Both")],
    )
    if origin is None:
        return
    cls = ctx.choose(
        "Which CIDR class?",
        [
            ("datacenter", "Datacenter CIDRs"),
            ("cdn", "CDN / Cloud CIDRs"),
            ("both", "Both"),
        ],
    )
    if cls is None:
        return

    categories = categories_for(origin, cls)
    entries: list[ranges_mod.RangeEntry] = []
    for category in categories:
        entries.extend(ranges_mod.category_entries(category))
    if not entries:
        ctx.print_(
            "\nNo saved CIDRs for that selection yet. Run "
            "'Discover & save provider ranges' first."
        )
        return

    hosts = ranges_mod.expand_hosts(
        [e.cidr for e in entries],
        sample_per_range=ctx.settings.sample_per_range,
        max_hosts=ctx.settings.max_hosts,
    )
    if not hosts:
        ctx.print_("Selected ranges expanded to no scannable hosts.")
        return

    cat_label = ", ".join(categories)
    ctx.print_(
        f"\nScanning {len(hosts)} host(s) from {len(entries)} saved "
        f"CIDR(s) [{cat_label}] ({ctx.settings.ping_count} probe(s) each)...\n"
    )
    host_to_record = map_hosts_to_entries(hosts, entries)

    bar = ProgressBar(len(hosts), stream=ctx.stdout, label="Scan")

    def _hook(_probe, verdict: str) -> None:
        bar.update(verdict)

    scope = categories[0] if len(categories) == 1 else origin
    report = scanner.run_scan(scope, ctx.settings, on_result=_hook, hosts=hosts)
    bar.finish()

    scan_id = scanner.persist(report, ctx.store)
    render_scan_results(ctx, report, host_to_record, scan_id, origin=origin)


def discover_save_scan_provider(ctx: ActionContext) -> None:
    """Targeted flow: pick an origin + a specific provider, then run the whole
    discover -> save -> scan pipeline as one continuous step.

    The chosen provider(s) are discovered live (seeded by their ASNs) and merged
    with their seed CIDRs, any newly discovered CIDRs are persisted into the
    correct Manage IP Ranges category automatically, and the expanded hosts are
    scanned immediately — no separate confirmation or manual save step.
    """
    from .. import providers as providers_mod

    origin = ctx.choose(
        "Which origin?",
        [("iran", "Iran"), ("foreign", "Foreign")],
    )
    if origin is None:
        return

    available = providers_mod.providers_for_origin(origin)
    if not available:
        ctx.print_("No known providers for that origin.")
        return

    options: list[tuple[str, str]] = [("__all__", "All providers")]
    for provider in available:
        label = f"{provider.name} ({provider.country or '?'})"
        options.append((provider.name, label))
    picked = ctx.choose(f"Which {ORIGIN_LABELS.get(origin, origin)} provider?", options)
    if picked is None:
        return

    if picked == "__all__":
        chosen = available
        scope_label = f"all {origin} providers"
    else:
        chosen = [p for p in available if p.name == picked]
        scope_label = picked

    ctx.print_(f"\nDiscovering ranges for {scope_label} (seed data + live)...")
    records = run_seeded_discovery(ctx, chosen)

    added = ranges_mod.persist_records(records)
    total = sum(added.values())
    if total:
        ctx.print_(f"Saved {total} new CIDR(s) into Manage IP Ranges.")
    else:
        ctx.print_("No new CIDRs to save (already stored).")

    located = [r for r in records if getattr(r, "prefix", None)]
    if not located:
        ctx.print_("Nothing discovered to scan.")
        return

    entries = [
        ranges_mod.RangeEntry(
            r.prefix, "discovered", r.country, r.provider or r.organization
        )
        for r in located
    ]
    hosts = ranges_mod.expand_hosts(
        [e.cidr for e in entries],
        sample_per_range=ctx.settings.sample_per_range,
        max_hosts=ctx.settings.max_hosts,
    )
    if not hosts:
        ctx.print_("Discovered ranges expanded to no scannable hosts.")
        return

    ctx.print_(
        f"\nScanning {len(hosts)} host(s) from {len(entries)} CIDR(s) "
        f"[{scope_label}] ({ctx.settings.ping_count} probe(s) each)...\n"
    )
    host_to_record = map_hosts_to_entries(hosts, entries)
    bar = ProgressBar(len(hosts), stream=ctx.stdout, label="Scan")

    def _hook(_probe, verdict: str) -> None:
        bar.update(verdict)

    report = scanner.run_scan(origin, ctx.settings, on_result=_hook, hosts=hosts)
    bar.finish()

    scan_id = scanner.persist(report, ctx.store)
    render_scan_results(ctx, report, host_to_record, scan_id, origin=origin)


def full_scan_hosts(ctx: ActionContext, scope: str, hosts: list[str]) -> None:
    """Run a full health scan on an explicit host list and print the report."""
    ctx.print_(f"\nRunning a full health scan on {len(hosts)} alive host(s)...\n")
    bar = ProgressBar(len(hosts), stream=ctx.stdout, label="Health scan")

    def _hook(_probe, verdict: str) -> None:
        bar.update(verdict)

    report = scanner.run_scan(scope, ctx.settings, on_result=_hook, hosts=hosts)
    bar.finish()
    scan_id = scanner.persist(report, ctx.store)
    ctx.print_("")
    ctx.print_(report_mod.render_report(report, stream=ctx.stdout))
    ctx.print_(report_mod.summary_line(report.counts, report.total, stream=ctx.stdout))
    ctx.print_(f"Saved as scan #{scan_id}.")
