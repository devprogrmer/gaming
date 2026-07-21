"""First-octet filter + auto-scan action (menu option 7)."""

from __future__ import annotations

from .. import report as report_mod
from .. import scanner
from ..classify import GOOD
from ..filters_shared import (
    format_bare_ips,
    matches_first_octet,
    parse_first_octets,
)
from ..progress import ProgressBar
from .context import ActionContext


def filter_by_first_octet(ctx: ActionContext) -> None:
    """Discover ranges, then filter the records by first octet + datacenter.

    Layers dynamic filters on top of the existing discovery filters (e.g.
    ``--country``): discovery + the standard ``Filters`` run first, then the
    first-octet predicate and the datacenter choice are applied to the resulting
    ``records`` list.
    """
    raw_input = ctx.prompt("Enter first octet(s) (0-255, comma-separated): ")
    try:
        allowed = parse_first_octets(raw_input)
    except ValueError as exc:
        ctx.print_(f"Invalid input: {exc}")
        return
    if not allowed:
        ctx.print_("No octets provided. Nothing to filter.")
        return

    # Step 2: datacenter filtering.
    dc_choice = ctx.prompt(
        "\nFilter by datacenter?\n"
        "  1) All datacenters\n"
        "  2) Specific datacenter/provider/org\n"
        "Choice: "
    ).strip()
    provider_term = ""
    if dc_choice == "1":
        dc_mode = "all"
    elif dc_choice == "2":
        dc_mode = "specific"
        provider_term = ctx.prompt("Enter datacenter/provider/org name: ").strip()
        if not provider_term:
            ctx.print_("No name provided. Nothing to filter.")
            return
    else:
        ctx.print_("Unknown option. Please choose 1 or 2.")
        return

    # Optional country filter so the dynamic filters demonstrably work alongside
    # the existing discovery filters.
    country = ctx.prompt("Optional country filter (e.g. IR,DE; blank for none): ").strip()

    # Imported lazily so launching the menu doesn't pull in the whole
    # discovery/reachability stack unless this feature is actually used.
    from ... import pipeline
    from ...config import load_config
    from ...models import Filters
    from ...processing.filters import has_provider_metadata, is_datacenter

    countries = [c.strip() for c in country.split(",") if c.strip()]
    # A "specific" datacenter/provider/org term reuses the existing provider
    # filter, so it is applied natively during process() alongside country.
    providers = [provider_term] if dc_mode == "specific" else []
    config = load_config()
    filters = Filters(countries=countries, providers=providers)

    ctx.print_("\nDiscovering IP ranges...")
    raw = pipeline.discover(config, filters)
    records = pipeline.process(raw, filters)

    # Step 1: octet match, reported on its own. This count is independent of the
    # datacenter classification below, so the user always sees how many CIDRs
    # match the octet even when none carry classifiable provider metadata (the
    # real-world RIR case where the combined filter used to collapse to zero).
    octet_matched = [r for r in records if matches_first_octet(r.prefix, allowed)]
    octet_label = ", ".join(str(o) for o in allowed)
    ctx.print_(
        f"\n{len(octet_matched)} of {len(records)} discovered record(s) "
        f"match first octet [{octet_label}]."
    )
    if not octet_matched:
        ctx.print_("  (none)")
        return

    # Step 2: datacenter classification as a *secondary, labelled* narrowing.
    if dc_mode == "all":
        datacenters = [r for r in octet_matched if is_datacenter(r)]
        # Records with no org/provider text can't be classified either way. They
        # are a third bucket — surfaced, never silently dropped — so RIR-sourced
        # CIDRs (country only, no org) remain actionable.
        unclassified = [r for r in octet_matched if not has_provider_metadata(r)]
        # "Not a datacenter" = has metadata to judge, but the heuristic said no.
        not_datacenter = [
            r
            for r in octet_matched
            if has_provider_metadata(r) and not is_datacenter(r)
        ]
        ctx.print_(
            f"Of those: {len(datacenters)} classified as datacenters, "
            f"{len(unclassified)} unclassified (no org/provider metadata to "
            f"classify — shown separately, not dropped), "
            f"{len(not_datacenter)} not datacenters."
        )
        # The actionable set keeps both confirmed datacenters and unclassifiable
        # records; only records we can positively rule out are excluded.
        matched = datacenters + unclassified
        _print_octet_records(ctx, "Datacenters", datacenters)
        _print_octet_records(
            ctx, "Unclassified (no provider metadata available)", unclassified
        )
    else:
        # "specific" provider/org term was already applied natively in process().
        matched = octet_matched
        _print_octet_records(
            ctx, f"Matching provider/org ~ {provider_term!r}", matched
        )

    if not matched:
        ctx.print_("\nNo records left to scan.")
        return

    # Step 3: auto-scan the filtered records with strict reachability +
    # location requirements, then persist the qualifying hosts.
    auto_scan_matched(ctx, matched)


def _print_octet_records(ctx: ActionContext, label: str, records: list) -> None:
    """Print a labelled bucket of records (or a friendly empty note)."""
    ctx.print_(f"\n{label} ({len(records)}):")
    if not records:
        ctx.print_("  (none)")
        return
    for rec in records:
        country_tag = f" [{rec.country}]" if rec.country else ""
        org_tag = f" — {rec.organization}" if rec.organization else ""
        ctx.print_(f"  {rec.prefix}{country_tag}{org_tag}")


def auto_scan_matched(ctx: ActionContext, records: list, scope: str = "filtered") -> None:
    """Health-scan the filtered records and keep only qualifying hosts.

    Two strict requirements gate the final list:

    * **Strict reachability** — a host must classify as ``GOOD`` (a real,
      low-latency, low-loss reply). ``MEDIUM`` and ``BAD`` are rejected.
    * **Location requirement** — the record must carry a verified country code,
      so results without a known location are excluded.

    Qualifying hosts are saved to scan history for later review, and their
    addresses are printed as a clean, copy-paste-ready bare-IP block.
    """
    # Only scan records that already satisfy the location requirement; a record
    # with no country can never qualify, so probing it is wasted work.
    located = [r for r in records if r.country]
    skipped_no_location = len(records) - len(located)
    if skipped_no_location:
        ctx.print_(
            f"\nExcluding {skipped_no_location} record(s) with no known "
            f"location (location requirement)."
        )
    if not located:
        ctx.print_("No records meet the location requirement. Nothing to scan.")
        return

    # Map each probe host back to its record so we can report location.
    host_to_record: dict[str, object] = {}
    for rec in located:
        host_to_record[rec.sample_host()] = rec
    hosts = list(host_to_record)

    ctx.print_(
        f"\nAuto-scanning {len(hosts)} located host(s) "
        f"({ctx.settings.ping_count} probe(s) each, strict GOOD only)...\n"
    )
    bar = ProgressBar(len(hosts), stream=ctx.stdout, label="Auto-scan")

    def _hook(_probe, verdict: str) -> None:
        bar.update(verdict)

    report = scanner.run_scan(scope, ctx.settings, on_result=_hook, hosts=hosts)
    bar.finish()

    # Strict reachability: keep only hosts classified GOOD.
    qualifying = [
        (probe, host_to_record[probe.host])
        for probe, verdict in report.results
        if verdict == GOOD and probe.host in host_to_record
    ]

    scan_id = scanner.persist(report, ctx.store)
    ctx.print_("")
    ctx.print_(report_mod.summary_line(report.counts, report.total, stream=ctx.stdout))
    ctx.print_(
        f"\n{len(qualifying)} host(s) meet both strict reachability (GOOD) "
        f"and the location requirement:"
    )
    if not qualifying:
        ctx.print_("  (none)")
    else:
        for probe, rec in qualifying:
            latency = f"{probe.avg_ms:.0f} ms" if probe.avg_ms is not None else "?"
            ctx.print_(f"  {probe.host}  [{rec.country}]  {latency}")

    # Clean, copy-paste-ready output: bare alive IPs, one per line, no
    # colours/symbols/prefixes. Empty section stays friendly.
    ctx.print_("\n--- Alive IPs (copy-paste ready) ---")
    if qualifying:
        ctx.print_(format_bare_ips(probe.host for probe, _rec in qualifying))
    else:
        ctx.print_("(no live IPs found)")
    ctx.print_(f"\nSaved as scan #{scan_id}. Use 'View scan history' to revisit it.")
