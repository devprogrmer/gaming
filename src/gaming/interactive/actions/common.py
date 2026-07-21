"""Helpers shared across interactive action modules.

Pure/near-pure logic lifted out of the old ``Menu`` methods so it can be reused
by both the terminal menu and (later) the web layer. Formatting still lives in
:mod:`gaming.interactive.report`; the functions here orchestrate scans and
render their already-formatted output through the :class:`ActionContext`.
"""

from __future__ import annotations

from .. import ranges as ranges_mod
from .. import report as report_mod
from .. import scanner
from ..classify import INTERNATIONAL
from ..filters_shared import format_bare_ips
from .context import ActionContext

# Origin (Iran vs foreign) and class (datacenter vs cdn) map onto the four
# storage categories in gaming.interactive.ranges.
ORIGIN_LABELS = {"iran": "Iran", "foreign": "Foreign"}
CLASS_LABELS = {"datacenter": "Datacenter", "cdn": "CDN / Cloud"}

# Longer per-request timeout for the bulk "discover & save" passes: RIPEstat and
# WHOIS are slow enough that the quick 5s ad-hoc CLI default reliably times out
# for a multi-ASN sweep, silently degrading to sample data.
DISCOVERY_TIMEOUT = 15.0


def hr(width: int = 52) -> str:
    return "-" * width


def categories_for(origin: str, cls: str) -> list[str]:
    """Resolve an (origin, class) selection to storage category keys."""
    classes = ("datacenter", "cdn") if cls == "both" else (cls,)
    origins = ("iran", "foreign") if origin == "both" else (origin,)
    return [f"{o}_{c}" for o in origins for c in classes]


def map_hosts_to_entries(
    hosts: list[str], entries: list[ranges_mod.RangeEntry]
) -> dict[str, object]:
    """Map each probe host back to the entry (metadata) of its CIDR."""
    import ipaddress as _ip

    nets = []
    for e in entries:
        try:
            nets.append((_ip.ip_network(e.cidr, strict=False), e))
        except ValueError:
            continue
    mapping: dict[str, object] = {}
    for host in hosts:
        try:
            addr = _ip.ip_address(host)
        except ValueError:
            continue
        for net, entry in nets:
            if addr in net:
                mapping[host] = entry
                break
    return mapping


def run_seeded_discovery(ctx: ActionContext, providers: list) -> list:
    """Discover live CIDRs for a set of seed providers + their seed CIDRs.

    Seeds the live pipeline with the providers' ASNs and countries (without
    seeds every source early-returns nothing and the pipeline silently falls
    back to sample data), runs it with a longer timeout and verbose per-request
    error logging, and merges the result with the bundled seed CIDRs so a
    provider always yields something even when the network is unavailable.
    """
    from ... import pipeline
    from ...config import load_config
    from ...models import Filters

    records: list = []
    for provider in providers:
        records.extend(provider.to_records())

    asns = [a for p in providers for a in p.asns]
    countries = [p.country for p in providers if p.country]
    if asns or countries:
        try:
            config = load_config()
            filters = Filters(asns=asns, countries=countries)
            raw = pipeline.discover(
                config,
                filters,
                timeout=DISCOVERY_TIMEOUT,
                verbose_errors=True,
            )
            records.extend(pipeline.process(raw, filters))
        except Exception as exc:  # noqa: BLE001 - discovery must never crash the menu
            ctx.print_(f"  (live discovery unavailable: {exc})")
    return records


def render_scan_results(
    ctx: ActionContext,
    report,
    host_to_record: dict[str, object],
    scan_id: int,
    *,
    origin: str,
) -> None:
    """Print the summary, lowest-latency grouping, and clean bare-IP block."""
    ctx.print_("")
    ctx.print_(report_mod.summary_line(report.counts, report.total, stream=ctx.stdout))

    # Bidirectional (Iran + abroad) tally, when the scan ran an abroad pass.
    combined = getattr(report, "combined", None) or []
    international_hosts: set[str] = set()
    if combined:
        counts = report.combined_counts
        ctx.print_(
            "Reachability  " + report_mod.combined_summary_line(counts, stream=ctx.stdout)
        )
        international_hosts = {
            c.host for c in combined if report.combined_verdict(c) == INTERNATIONAL
        }

    live = [(p, v) for p, v in report.results if p.reachable]
    if live:
        groups = scanner.summarize_by_group(report.results, host_to_record)
        ctx.print_("\n" + hr())
        ctx.print_("Latency by destination country (from this server):")
        ctx.print_(report_mod.render_group_latency(groups))

    # A per-host results table now carries the abroad/whitelist columns.
    ctx.print_(hr())
    ctx.print_(report_mod.render_report(report, stream=ctx.stdout))

    # Bare-IP export: optionally restrict to whitelisted (INTERNATIONAL) IPs.
    whitelist_only = ctx.settings.export_international_only and bool(combined)
    if whitelist_only:
        export_hosts = [p.host for p, _v in live if p.host in international_hosts]
        export_label = "Whitelisted (INTERNATIONAL) IPs (copy-paste ready):"
    else:
        export_hosts = [p.host for p, _v in live]
        export_label = "Alive IPs (copy-paste ready):"

    ctx.print_(hr())
    ctx.print_(export_label)
    if export_hosts:
        ctx.print_(format_bare_ips(export_hosts))
    else:
        ctx.print_("(none)")
    ctx.print_(f"\nSaved as scan #{scan_id}. Use 'View scan history' to revisit it.")
