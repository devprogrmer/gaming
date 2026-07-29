"""Command-line interface for the gaming network-discovery tool.

Subcommands:
    menu      launch the interactive, menu-driven IP health scanner
    sources   list available discovery sources
    discover  discover + filter + normalize prefixes (no reachability)
    check     run reachability/ports/global checks on given prefixes
    run       full pipeline: discover -> process -> reachability -> report
    update    upgrade the installation in place to a new release
    watch     continuously re-discover a country and re-scan it, 24/7
    check-membership  reverse lookup: which stored range contains an IP?

Running ``gaming`` with no subcommand launches the interactive menu.
"""

from __future__ import annotations

import argparse
import os
import sys

from . import __version__
from .config import Config, apply_overrides, load_config
from .discovery import available_sources
from .logging_setup import setup_logging
from .models import Filters, IPRecord
from .pipeline import check_reachability, discover, process, run_pipeline
from .reporting import export


def _split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [v.strip() for v in value.split(",") if v.strip()]


def _add_common_output_args(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--format",
        "-f",
        choices=["console", "json", "csv", "ip-list"],
        default="console",
        help=(
            "output format (default: console). 'ip-list' prints bare IP "
            "addresses only, one per line — it DISCARDS ALL METADATA "
            "(no CIDR, ASN, organization or country) by design, for piping "
            "into other tools; use json/csv for auditing"
        ),
    )
    p.add_argument("--output", "-o", help="write output to this file")


def _add_filter_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--country", help="comma-separated country codes, e.g. IR,DE")
    p.add_argument("--asn", help="comma-separated ASNs, e.g. AS13335,AS24940")
    p.add_argument("--provider", help="comma-separated provider substrings")
    p.add_argument("--org", help="comma-separated organization substrings")
    p.add_argument(
        "--iran-datacenter",
        action="store_true",
        help="focus on Iranian datacenter-related ranges",
    )
    p.add_argument(
        "--foreign-datacenter",
        action="store_true",
        help="focus on foreign datacenter-related ranges",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gaming",
        description=(
            "Network discovery and reachability analysis CLI "
            "(discovers IP ranges, checks reachability, exports reports). "
            "Note: not a video game."
        ),
    )
    parser.add_argument("--version", action="version", version=f"gaming {__version__}")
    parser.add_argument("--config", "-c", help="path to a TOML configuration file")
    parser.add_argument("--log-level", help="DEBUG|INFO|WARNING|ERROR")
    parser.add_argument("--concurrency", type=int, help="max concurrent workers")
    parser.add_argument("--timeout", type=float, help="per-operation timeout (seconds)")
    parser.add_argument(
        "--offline",
        action="store_true",
        help="use bundled sample data instead of live network calls",
    )
    parser.add_argument("--quiet", "-q", action="store_true", help="only log errors")

    sub = parser.add_subparsers(dest="command", required=False)

    # menu (interactive) — also the default when no subcommand is given.
    sub.add_parser(
        "menu",
        help="launch the interactive, menu-driven IP health scanner",
    )

    # sources
    sub.add_parser("sources", help="list available discovery sources")

    # discover
    p_disc = sub.add_parser("discover", help="discover + filter + normalize prefixes")
    _add_filter_args(p_disc)
    _add_common_output_args(p_disc)
    p_disc.add_argument("--sources", help="comma-separated subset of sources to use")
    p_disc.add_argument(
        "--collapse", action="store_true", help="collapse adjacent/contained prefixes"
    )
    p_disc.add_argument(
        "--exhaustive",
        action="store_true",
        help=(
            "full country sweep: enumerate EVERY delegated IPv4/IPv6 prefix for "
            "--country from RIR delegated statistics and resolve each one's ASN "
            "and registered organization, instead of only the curated seed "
            "providers. Finds small/obscure hosting companies the seed list has "
            "never heard of. Slow (thousands of prefixes) but resumable"
        ),
    )
    p_disc.add_argument(
        "--no-ipv6",
        action="store_true",
        help="exhaustive mode: skip IPv6 allocations",
    )
    p_disc.add_argument(
        "--no-resume",
        action="store_true",
        help="exhaustive mode: ignore any saved progress and start over",
    )
    p_disc.add_argument(
        "--save",
        action="store_true",
        help="persist discovered ranges for later scans",
    )

    # check
    p_check = sub.add_parser("check", help="run reachability checks on given prefixes")
    p_check.add_argument("prefixes", nargs="+", help="one or more IPs/CIDRs to check")
    p_check.add_argument("--ports", help="comma-separated ports to probe, e.g. 80,443")
    p_check.add_argument(
        "--global",
        dest="global_check",
        action="store_true",
        help="perform global reachability checks (check-host.net)",
    )
    p_check.add_argument(
        "--method", choices=["auto", "ping", "tcp"], help="local alive method"
    )
    _add_common_output_args(p_check)

    # run (full pipeline)
    p_run = sub.add_parser("run", help="full pipeline: discover -> check -> report")
    _add_filter_args(p_run)
    _add_common_output_args(p_run)
    p_run.add_argument("--sources", help="comma-separated subset of sources to use")
    p_run.add_argument("--ports", help="comma-separated ports to probe")
    p_run.add_argument(
        "--global",
        dest="global_check",
        action="store_true",
        help="perform global reachability checks (check-host.net)",
    )
    p_run.add_argument(
        "--no-reachability", action="store_true", help="skip local reachability"
    )
    p_run.add_argument(
        "--collapse", action="store_true", help="collapse adjacent/contained prefixes"
    )

    # update (in-place upgrade / version switch)
    p_update = sub.add_parser(
        "update",
        help="upgrade or switch the installation in place to a specific release",
    )
    p_update.add_argument(
        "--source",
        help="path to the gaming checkout to install from (default: auto-detect)",
    )
    p_update.add_argument(
        "--version",
        dest="ref",
        metavar="RELEASE",
        help="switch to a specific release tag/ref (e.g. v0.1.0), including "
        "older versions; omit to update to the latest",
    )
    p_update.add_argument(
        "--list",
        dest="list_releases",
        action="store_true",
        help="list the available release versions and exit",
    )
    p_update.add_argument(
        "--no-pull",
        dest="pull",
        action="store_false",
        default=None,
        help="install the source as-is without running 'git pull' first",
    )

    # web (local dashboard)
    p_web = sub.add_parser(
        "web",
        help="launch the local web dashboard (search, scan, history, settings)",
    )
    p_web.add_argument(
        "--bind",
        default="0.0.0.0",
        help="address to bind (default: 0.0.0.0; use 127.0.0.1 to stay local)",
    )
    p_web.add_argument(
        "--port",
        type=int,
        default=None,
        help="port to listen on (default: auto-pick a free port in 20000-65000)",
    )
    p_web.add_argument(
        "--tls",
        action="store_true",
        help="serve over HTTPS with a cached self-signed certificate",
    )
    p_web.add_argument(
        "--reset-credentials",
        action="store_true",
        help="regenerate the dashboard username/password and invalidate sessions",
    )
    p_web.add_argument(
        "-d",
        "--daemon",
        action="store_true",
        help="run in the background, detached from the terminal/SSH session "
        "(survives disconnect); writes a PID file for --stop/--status",
    )
    p_web.add_argument(
        "--stop",
        action="store_true",
        help="stop a running background dashboard (via its PID file) and exit",
    )
    p_web.add_argument(
        "--status",
        action="store_true",
        help="report whether a background dashboard is running and exit",
    )
    p_web.add_argument(
        "--schedule",
        default=None,
        metavar="SCOPE",
        help="also run recurring scans of SCOPE while the panel is up, "
        "appending each run to history; stopped as part of Ctrl+C shutdown",
    )
    p_web.add_argument(
        "--schedule-interval",
        type=float,
        default=900.0,
        help="seconds between --schedule runs (default: 900)",
    )

    # watch (continuous discover -> scan loop, 24/7)
    p_watch = sub.add_parser(
        "watch",
        help="continuously re-discover a country's ranges and re-scan them",
    )
    p_watch.add_argument(
        "--country",
        default="IR",
        help="country code to watch (default: IR)",
    )
    p_watch.add_argument(
        "--interval",
        default="1h",
        help="time between iterations: 30m, 1h, 2d, or bare seconds "
        "(default: 1h; floored at 5m)",
    )
    p_watch.add_argument(
        "--scope",
        default="iran",
        choices=["iran", "foreign"],
        help="which stored scope to re-scan each iteration (default: iran)",
    )
    p_watch.add_argument(
        "--no-ipv6",
        action="store_true",
        help="skip IPv6 allocations during the discovery step",
    )
    p_watch.add_argument(
        "--count",
        type=int,
        default=0,
        help="stop after N iterations (default: 0 = run until stopped)",
    )
    p_watch.add_argument(
        "-d",
        "--daemon",
        action="store_true",
        help="run in the background, detached from the terminal/SSH session "
        "(survives disconnect); writes a PID file for --stop/--status",
    )
    p_watch.add_argument(
        "--stop",
        action="store_true",
        help="stop a running background watch (via its PID file) and exit",
    )
    p_watch.add_argument(
        "--status",
        action="store_true",
        help="report whether a background watch is running and exit",
    )

    # check-membership (reverse lookup: which stored range holds this IP?)
    p_member = sub.add_parser(
        "check-membership",
        help="find which stored CIDR(s) contain a given IP address",
    )
    p_member.add_argument("ip", help="the IPv4/IPv6 address to look up")
    p_member.add_argument(
        "--live",
        action="store_true",
        help="if the address matches nothing stored, query RDAP for its real "
        "operator instead of only reporting 'not found'",
    )
    p_member.add_argument(
        "--no-bundled",
        action="store_true",
        help="search only custom/discovered ranges, skipping the shipped lists",
    )
    p_member.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        help="emit the result as JSON instead of a table",
    )

    # refresh-seeds (re-validate bundled provider CIDRs against live BGP)
    p_refresh = sub.add_parser(
        "refresh-seeds",
        help="re-validate bundled provider seed CIDRs against announced prefixes",
    )
    p_refresh.add_argument(
        "--timeout",
        type=float,
        default=15.0,
        help="per-provider lookup timeout in seconds (default: 15)",
    )

    # validate-seed (validate + stamp the seed file's last_validated marker)
    p_validate = sub.add_parser(
        "validate-seed",
        help="validate bundled seed CIDRs and update the last_validated marker",
    )
    p_validate.add_argument(
        "--timeout",
        type=float,
        default=15.0,
        help="per-provider lookup timeout in seconds (default: 15)",
    )
    p_validate.add_argument(
        "--no-marker",
        dest="update_marker",
        action="store_false",
        default=True,
        help="report only; do not update the seed file's last_validated marker",
    )

    # schedule (recurring background re-scans of a saved scope)
    p_sched = sub.add_parser(
        "schedule",
        help="re-run a saved scope scan on an interval, updating history",
    )
    p_sched.add_argument(
        "scope",
        nargs="?",
        default="iran",
        help="scope to re-scan: iran or foreign (default: iran)",
    )
    p_sched.add_argument(
        "--interval",
        type=float,
        default=900.0,
        help="seconds between scans (default: 900; floored at 30)",
    )
    p_sched.add_argument(
        "--count",
        type=int,
        default=0,
        help="stop after N scans (default: 0 = run until interrupted)",
    )

    return parser


def _config_from_args(args: argparse.Namespace) -> Config:
    config = load_config(args.config)
    overrides = {
        "general.log_level": args.log_level,
        "general.concurrency": args.concurrency,
        "general.timeout": args.timeout,
        "discovery.offline": True if args.offline else None,
    }
    return apply_overrides(config, {k: v for k, v in overrides.items() if v is not None})


def _filters_from_args(args: argparse.Namespace, config: Config) -> Filters:
    base = config.to_filters()
    return Filters(
        countries=_split_csv(getattr(args, "country", None)) or base.countries,
        asns=_split_csv(getattr(args, "asn", None)) or base.asns,
        providers=_split_csv(getattr(args, "provider", None)) or base.providers,
        organizations=_split_csv(getattr(args, "org", None)) or base.organizations,
        iran_datacenter=getattr(args, "iran_datacenter", False) or base.iran_datacenter,
        foreign_datacenter=getattr(args, "foreign_datacenter", False)
        or base.foreign_datacenter,
    )


def _emit(records: list[IPRecord], args: argparse.Namespace) -> None:
    text = export(records, args.format, getattr(args, "output", None))
    if args.format == "console":
        sys.stdout.write(text)
        return
    # json/csv/ip-list: the payload is the only thing on stdout, so it can be
    # redirected or piped verbatim. Anything advisory goes to stderr.
    sys.stdout.write(text)
    if text and not text.endswith("\n"):
        sys.stdout.write("\n")
    if getattr(args, "output", None):
        sys.stderr.write(f"written: {args.output}\n")


# ---- command handlers ----------------------------------------------------
def cmd_menu(args: argparse.Namespace, config: Config) -> int:
    # Imported lazily so the interactive subpackage (and its sqlite/data
    # dependencies) is only loaded when actually launching the menu.
    from .interactive.menu import run as run_menu

    return run_menu()


def cmd_sources(args: argparse.Namespace, config: Config) -> int:
    import sys as _sys

    from .discovery import REGISTRY
    from .interactive import theme

    columns = [theme.Column("SOURCE"), theme.Column("DESCRIPTION")]
    rows = []
    for name in available_sources():
        cls = REGISTRY.get(name)
        # The one-line summary lives in the source *module*'s docstring; the
        # classes themselves are undocumented.
        module = _sys.modules.get(getattr(cls, "__module__", ""), None)
        doc = ((getattr(module, "__doc__", "") or "").strip().splitlines() or [""])[0]
        # Docstrings are reStructuredText; ``literal`` markup is noise here.
        rows.append([name, doc.replace("``", "").rstrip(".") or "-"])

    sys.stdout.write(theme.heading("Discovery sources", sys.stdout) + "\n")
    sys.stdout.write(theme.render_table(columns, rows, stream=sys.stdout))

    # Surface how stale the bundled provider seed data is (Part F marker).
    try:
        from .interactive.providers import seed_last_validated

        stamp = seed_last_validated()
    except Exception:  # noqa: BLE001 - informational only, never fail `sources`
        stamp = ""
    sys.stdout.write(
        "\n"
        + theme.key_value(
            "seed data last validated:",
            f"{stamp or 'never'} (run 'gaming validate-seed')",
            stream=sys.stdout,
        )
        + "\n"
    )
    return 0


def cmd_discover(args: argparse.Namespace, config: Config) -> int:
    filters = _filters_from_args(args, config)
    if getattr(args, "exhaustive", False):
        records = _discover_exhaustive(args, config, filters)
    else:
        sources = _split_csv(args.sources) or None
        raw = discover(config, filters, sources=sources)
        records = process(raw, filters, collapse=args.collapse)
    if getattr(args, "save", False):
        _save_records(records, exhaustive=getattr(args, "exhaustive", False))
    _emit(records, args)
    return 0


def _discover_exhaustive(
    args: argparse.Namespace, config: Config, filters: Filters
) -> list[IPRecord]:
    """Full-country sweep: every delegated prefix, not just seeded providers."""
    from .discovery.base import DiscoveryContext
    from .discovery.exhaustive import ExhaustiveSweep

    countries = filters.countries or ["IR"]
    context = DiscoveryContext(filters=filters, timeout=config.timeout)

    records: list[IPRecord] = []
    for country in countries:
        sweep = ExhaustiveSweep(
            country=country,
            context=context,
            include_ipv6=not getattr(args, "no_ipv6", False),
            resume=not getattr(args, "no_resume", False),
            progress_callback=None if args.quiet else _sweep_progress,
        )
        records.extend(sweep.run())
    if not args.quiet:
        sys.stderr.write("\n")
    return records


def _sweep_progress(progress) -> None:
    """Render sweep advancement on stderr so stdout stays pipeable."""
    sys.stderr.write(
        f"\r{progress.country}: {progress.done}/{progress.total} prefixes "
        f"({progress.named} named, {progress.unnamed} unnamed, "
        f"{progress.errors} error(s))   "
    )
    sys.stderr.flush()


def _save_records(records: list[IPRecord], *, exhaustive: bool) -> None:
    """Persist discovered records to the stored range lists."""
    from .interactive import ranges as ranges_mod

    if exhaustive:
        added = ranges_mod.persist_exhaustive_records(records)
    else:
        added = ranges_mod.persist_records(records)
    total = sum(added.values())
    detail = ", ".join(f"{k}: {v}" for k, v in sorted(added.items())) or "none"
    sys.stderr.write(f"saved {total} new range(s) ({detail})\n")


def cmd_check(args: argparse.Namespace, config: Config) -> int:
    records: list[IPRecord] = []
    for prefix in args.prefixes:
        try:
            records.append(IPRecord(prefix=prefix, source="cli"))
        except ValueError as exc:
            sys.stderr.write(f"skipping invalid prefix {prefix!r}: {exc}\n")

    # Apply CLI overrides to the reachability/global config.
    overrides: dict = {}
    if args.method:
        overrides["reachability.method"] = args.method
    if args.ports:
        overrides["reachability.ports"] = [int(p) for p in _split_csv(args.ports)]
    if args.global_check:
        overrides["global_check.enabled"] = True
    config = apply_overrides(config, overrides)

    check_reachability(records, config)
    _emit(records, args)
    return 0


def cmd_run(args: argparse.Namespace, config: Config) -> int:
    filters = _filters_from_args(args, config)
    # Fold filters + reachability CLI flags into config.
    overrides: dict = {
        "filters.countries": filters.countries,
        "filters.asns": filters.asns,
        "filters.providers": filters.providers,
        "filters.organizations": filters.organizations,
        "filters.iran_datacenter": filters.iran_datacenter,
        "filters.foreign_datacenter": filters.foreign_datacenter,
    }
    if args.no_reachability:
        overrides["reachability.enabled"] = False
    if args.ports:
        overrides["reachability.ports"] = [int(p) for p in _split_csv(args.ports)]
    if args.global_check:
        overrides["global_check.enabled"] = True
    config = apply_overrides(config, overrides)

    sources = _split_csv(args.sources) or None
    records = run_pipeline(config, sources=sources, collapse=args.collapse)
    _emit(records, args)
    return 0


def cmd_update(args: argparse.Namespace, config: Config) -> int:
    # Imported lazily so the normal CLI paths don't pull in update machinery.
    from .updater import UpdateError, list_releases, run_update

    source = getattr(args, "source", None)

    if getattr(args, "list_releases", False):
        try:
            releases = list_releases(source)
        except UpdateError as exc:
            sys.stderr.write(f"update failed: {exc}\n")
            return 1
        if not releases:
            sys.stdout.write(
                "No release versions found "
                "(the source is not a git checkout or has no tags).\n"
            )
            return 0
        sys.stdout.write("Available releases (newest first):\n")
        for tag in releases:
            marker = "  * " if tag.lstrip("v") == __version__ else "    "
            sys.stdout.write(f"{marker}{tag}\n")
        return 0

    try:
        result = run_update(
            source=source,
            pull=getattr(args, "pull", None),
            ref=getattr(args, "ref", None),
            log=lambda m: sys.stdout.write(m + "\n"),
        )
    except UpdateError as exc:
        sys.stderr.write(f"update failed: {exc}\n")
        return 1

    if result.ref is not None:
        sys.stdout.write(
            f"Switched gaming {result.previous_version} -> {result.new_version} "
            f"(release {result.ref}).\n"
        )
    elif result.changed:
        sys.stdout.write(
            f"Updated gaming {result.previous_version} -> {result.new_version}.\n"
        )
    else:
        sys.stdout.write(
            f"gaming is already up to date (version {result.new_version}).\n"
        )
    return 0


def cmd_web(args: argparse.Namespace, config: Config) -> int:
    # Imported lazily so normal CLI paths don't pull in the http server stack.
    from .web import daemon as daemon_mod
    from .web.server import serve

    # Lifecycle subcommands short-circuit before starting a server.
    if getattr(args, "stop", False):
        stopped = daemon_mod.stop()
        if stopped:
            sys.stdout.write("Stopped the background dashboard.\n")
        else:
            sys.stdout.write("No running background dashboard found.\n")
        return 0

    if getattr(args, "status", False):
        st = daemon_mod.status()
        if st.running:
            import datetime as _dt

            when = (
                _dt.datetime.fromtimestamp(st.since).isoformat(timespec="seconds")
                if st.since
                else "unknown"
            )
            sys.stdout.write(
                f"Dashboard is running (PID {st.pid}, since {when}).\n"
            )
        else:
            sys.stdout.write("Dashboard is not running.\n")
        return 0

    daemon = getattr(args, "daemon", False)
    if daemon:
        # Refuse to start a second daemon on top of a live one.
        existing = daemon_mod.status()
        if existing.running:
            sys.stderr.write(
                f"A background dashboard is already running (PID {existing.pid}). "
                "Use 'gaming web --stop' first, or 'gaming web --status'.\n"
            )
            return 1

    scheduler = None
    schedule_scope = getattr(args, "schedule", None)
    if schedule_scope:
        from .interactive.scheduler import ScanScheduler

        scheduler = ScanScheduler(
            schedule_scope,
            getattr(args, "schedule_interval", 900.0),
            run_immediately=False,
        )
        scheduler.start()
        sys.stdout.write(
            f"Recurring '{schedule_scope}' scans every "
            f"{scheduler.interval:.0f}s; history updates while the panel runs.\n"
        )

    return serve(
        bind=getattr(args, "bind", "0.0.0.0"),
        port=getattr(args, "port", None),
        use_tls=getattr(args, "tls", False),
        reset_credentials=getattr(args, "reset_credentials", False),
        daemon=daemon,
        scheduler=scheduler,
    )


def cmd_refresh_seeds(args: argparse.Namespace, config: Config) -> int:
    """Re-validate bundled provider seed CIDRs against currently-announced BGP.

    Read-only: flags stale-looking CIDRs (and providers that couldn't be
    checked) but never edits the seed file.
    """
    from .interactive.providers import refresh_seed_data

    checks = refresh_seed_data(timeout=getattr(args, "timeout", 15.0))
    _print_seed_checks(checks)
    return 0


def cmd_validate_seed(args: argparse.Namespace, config: Config) -> int:
    """Validate bundled seed CIDRs and stamp the ``last_validated`` marker.

    Reports the same stale-CIDR findings as ``refresh-seeds`` but, unless
    ``--no-marker`` is given, also updates the seed file's ``[meta].last_validated``
    date. It never adds, edits, or deletes any provider entry.
    """
    from .interactive.providers import validate_seed_data

    report = validate_seed_data(
        timeout=getattr(args, "timeout", 15.0),
        update_marker=getattr(args, "update_marker", True),
    )
    _print_seed_checks(report.checks)
    if report.marker_updated:
        print(f"Updated seed last_validated marker to {report.last_validated}.")
    elif getattr(args, "update_marker", True):
        print(
            "Seed last_validated marker not updated "
            "(no provider was reachable, or the seed file is read-only)."
        )
    return 0


def _print_seed_checks(checks: list) -> None:
    """Shared reporting for refresh-seeds / validate-seed."""
    from .interactive import theme

    out = sys.stdout
    checked = [c for c in checks if c.checked]
    stale = [c for c in checked if c.stale]

    print(theme.heading("Seed validation", out))
    print(
        theme.key_value(
            "Validated:", f"{len(checked)}/{len(checks)} providers with a live lookup",
            stream=out,
        )
    )

    if not stale:
        print(theme.style("No stale seed CIDRs detected.", "ok", out))
    else:
        total_stale = sum(len(c.stale) for c in stale)
        print(
            theme.style(f"{total_stale} seed CIDR(s) look stale:", "warn", out)
        )
        rows = [
            [c.name, cidr, "not in any announced prefix"]
            for c in stale
            for cidr in c.stale
        ]
        print(
            theme.render_table(
                [theme.Column("PROVIDER"), theme.Column("CIDR"), theme.Column("REASON")],
                rows,
                stream=out,
                indent="  ",
            ),
            end="",
        )

    unchecked = [c for c in checks if not c.checked]
    if unchecked:
        print(
            theme.style(
                f"{len(unchecked)} provider(s) could not be checked "
                "(no ASNs or lookup failed); left untouched.",
                "muted",
                out,
            )
        )


def cmd_schedule(args: argparse.Namespace, config: Config) -> int:
    """Run recurring background re-scans of a saved scope.

    With ``--count N`` it runs exactly N scans and returns; otherwise it runs
    until interrupted (Ctrl-C). Each run is appended to scan history and, when
    enabled in Settings, triggers verdict-change alerts.
    """
    import time

    from .interactive.scheduler import ScanScheduler

    scope = getattr(args, "scope", "iran")
    interval = getattr(args, "interval", 900.0)
    count = getattr(args, "count", 0)

    sched = ScanScheduler(scope, interval, run_immediately=True)

    if count and count > 0:
        for _ in range(count):
            state = sched.run_once()
            _print_schedule_run(state)
        return 0

    print(
        f"Scheduling '{scope}' scans every {sched.interval:.0f}s. "
        "Press Ctrl-C to stop."
    )
    sched.start()
    try:
        while sched.running:
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("\nStopping scheduler...")
        sched.stop()
    return 0


def _print_schedule_run(state) -> None:
    if state.last_error:
        print(f"[{state.scope}] scan failed: {state.last_error}")
    else:
        extra = f", {state.last_changes} verdict change(s)" if state.last_changes else ""
        print(
            f"[{state.scope}] scan #{state.runs} saved (id={state.last_scan_id}){extra}"
        )


def cmd_watch(args: argparse.Namespace, config: Config) -> int:
    """Run the continuous discover -> persist -> scan watch loop.

    Lifecycle flags (``--stop``/``--status``/``--daemon``) reuse the same
    PID-file machinery as ``gaming web``, just pointed at the watch PID file, so
    a backgrounded watcher outlives the SSH session that started it.
    """
    from .interactive import paths
    from .interactive.watch import WatchLoop, parse_interval
    from .web import daemon as daemon_mod

    pid_path = paths.watch_pid_path()

    if getattr(args, "stop", False):
        stopped = daemon_mod.stop(pid_path)
        sys.stdout.write(
            "Stopped the background watch.\n"
            if stopped
            else "No running background watch found.\n"
        )
        return 0

    if getattr(args, "status", False):
        st = daemon_mod.status(pid_path)
        if st.running:
            import datetime as _dt

            when = (
                _dt.datetime.fromtimestamp(st.since).isoformat(timespec="seconds")
                if st.since
                else "unknown"
            )
            sys.stdout.write(f"Watch is running (PID {st.pid}, since {when}).\n")
        else:
            sys.stdout.write("Watch is not running.\n")
        return 0

    country = getattr(args, "country", "IR")
    interval = parse_interval(getattr(args, "interval", "1h"))
    count = getattr(args, "count", 0)

    if getattr(args, "daemon", False):
        existing = daemon_mod.status(pid_path)
        if existing.running:
            sys.stderr.write(
                f"A background watch is already running (PID {existing.pid}). "
                "Use 'gaming watch --stop' first, or 'gaming watch --status'.\n"
            )
            return 1
        try:
            daemon_mod.daemonize(paths.watch_log_path())
        except daemon_mod.DaemonError as exc:
            sys.stderr.write(f"{exc}\n")
            return 1
        daemon_mod.write_pid(os.getpid(), pid_path)

    loop = WatchLoop(
        country=country,
        interval_seconds=interval,
        scope=getattr(args, "scope", "iran"),
        include_ipv6=not getattr(args, "no_ipv6", False),
        on_iteration=None if args.quiet else _print_watch_iteration,
    )

    if not args.quiet:
        sys.stdout.write(
            f"Watching {loop.country} every {loop.interval:.0f}s "
            f"(scope: {loop.scope}). Press Ctrl-C to stop.\n"
        )
        sys.stdout.flush()

    try:
        loop.run_forever(max_iterations=max(0, count))
    except KeyboardInterrupt:
        sys.stdout.write("\nStopping watch...\n")
        loop.stop()
    finally:
        if getattr(args, "daemon", False):
            daemon_mod.remove_pid(pid_path)
    return 0


def _print_watch_iteration(state) -> None:
    persisted = sum(state.last_persisted.values())
    line = (
        f"[{state.country}] iteration #{state.iterations + 1}: "
        f"{state.last_discovered} prefix(es) discovered, {persisted} newly stored, "
        f"{state.last_scanned} host(s) scanned"
    )
    if state.last_scan_id is not None:
        line += f" (scan id={state.last_scan_id})"
    if state.last_changes:
        line += f", {state.last_changes} verdict change(s)"
    sys.stdout.write(line + "\n")
    for err in state.errors:
        sys.stdout.write(f"  ! {err}\n")
    sys.stdout.flush()


def cmd_check_membership(args: argparse.Namespace, config: Config) -> int:
    """Report which stored CIDR(s) contain a given IP address."""
    import json as _json

    from .interactive import membership, theme

    try:
        result = membership.lookup_ip(
            args.ip, include_bundled=not getattr(args, "no_bundled", False)
        )
    except ValueError as exc:
        sys.stderr.write(f"invalid IP address: {exc}\n")
        return 2

    if not result.found and getattr(args, "live", False):
        result.live = membership.live_lookup(args.ip, timeout=config.timeout)

    if getattr(args, "as_json", False):
        sys.stdout.write(_json.dumps(result.as_dict(), indent=2) + "\n")
        return 0 if result.found else 1

    out = sys.stdout
    if result.found:
        out.write(
            theme.heading(
                f"{result.ip} matches {len(result.matches)} stored range(s)", out
            )
            + "\n"
        )
        columns = [
            theme.Column("CIDR"),
            theme.Column("CATEGORY"),
            theme.Column("ORIGIN"),
            theme.Column("COUNTRY"),
            theme.Column("ORGANIZATION"),
        ]
        rows = [
            [m.cidr, m.group, m.origin, m.country or "-", m.provider or "-"]
            for m in result.matches
        ]
        out.write(theme.render_table(columns, rows, stream=out))
        return 0

    out.write(f"{result.ip} is not inside any stored range.\n")
    if result.live:
        out.write("\n")
        out.write(theme.heading("Live registry lookup", out) + "\n")
        for key in ("cidr", "organization", "country", "source"):
            value = result.live.get(key)
            if value:
                out.write(theme.key_value(f"{key}:", str(value), stream=out) + "\n")
    elif getattr(args, "live", False):
        out.write("Live registry lookup returned nothing (offline or unregistered).\n")
    else:
        out.write("Re-run with --live to ask the registries who owns it.\n")
    return 1


_HANDLERS = {
    "menu": cmd_menu,
    "sources": cmd_sources,
    "discover": cmd_discover,
    "check": cmd_check,
    "run": cmd_run,
    "update": cmd_update,
    "web": cmd_web,
    "refresh-seeds": cmd_refresh_seeds,
    "validate-seed": cmd_validate_seed,
    "schedule": cmd_schedule,
    "watch": cmd_watch,
    "check-membership": cmd_check_membership,
}


def _ensure_utf8_stdio() -> None:
    """Best-effort: force UTF-8 on stdout/stderr so reports render correctly
    even under legacy terminal code pages (e.g. Windows cp1252)."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


def main(argv: list[str] | None = None) -> int:
    _ensure_utf8_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)

    # No subcommand -> launch the interactive menu (the default experience).
    if args.command is None:
        args.command = "menu"

    try:
        config = _config_from_args(args)
    except (FileNotFoundError, ValueError) as exc:
        sys.stderr.write(f"config error: {exc}\n")
        return 2

    setup_logging(config.log_level, quiet=args.quiet)

    handler = _HANDLERS.get(args.command)
    if handler is None:  # pragma: no cover - argparse enforces this
        parser.error(f"unknown command: {args.command}")
        return 2

    try:
        return handler(args, config)
    except KeyboardInterrupt:  # pragma: no cover
        sys.stderr.write("interrupted\n")
        return 130
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"error: {exc}\n")
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
