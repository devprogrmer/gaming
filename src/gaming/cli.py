"""Command-line interface for the gaming network-discovery tool.

Subcommands:
    sources   list available discovery sources
    discover  discover + filter + normalize prefixes (no reachability)
    check     run reachability/ports/global checks on given prefixes
    run       full pipeline: discover -> process -> reachability -> report
"""

from __future__ import annotations

import argparse
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
        choices=["console", "json", "csv"],
        default="console",
        help="output format (default: console)",
    )
    p.add_argument("--output", "-o", help="write output to this file (json/csv)")


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

    sub = parser.add_subparsers(dest="command", required=True)

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
    else:
        # For json/csv, always print to stdout; also written to file if -o given.
        sys.stdout.write(text)
        if not text.endswith("\n"):
            sys.stdout.write("\n")
        if getattr(args, "output", None):
            sys.stderr.write(f"written: {args.output}\n")


# ---- command handlers ----------------------------------------------------
def cmd_sources(args: argparse.Namespace, config: Config) -> int:
    for name in available_sources():
        sys.stdout.write(f"{name}\n")
    return 0


def cmd_discover(args: argparse.Namespace, config: Config) -> int:
    filters = _filters_from_args(args, config)
    sources = _split_csv(args.sources) or None
    raw = discover(config, filters, sources=sources)
    records = process(raw, filters, collapse=args.collapse)
    _emit(records, args)
    return 0


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


_HANDLERS = {
    "sources": cmd_sources,
    "discover": cmd_discover,
    "check": cmd_check,
    "run": cmd_run,
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
