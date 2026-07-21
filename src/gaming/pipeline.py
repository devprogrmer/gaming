"""High-level pipeline orchestration tying discovery -> processing ->
reachability -> reporting together with concurrency and error handling.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

from .config import Config
from .discovery import DiscoveryContext, build_source
from .logging_setup import get_logger
from .models import Filters, IPRecord
from .processing import apply_filters, normalize_records
from .processing.normalize import collapse_prefixes
from .reachability.global_check import build_providers, check_abroad
from .reachability.local import check_alive_bulk
from .reachability.ports import probe_records

log = get_logger("gaming.pipeline")


def discover(
    config: Config,
    filters: Filters | None = None,
    *,
    sources: list[str] | None = None,
    timeout: float | None = None,
    verbose_errors: bool = False,
) -> list[IPRecord]:
    """Run all configured discovery sources concurrently and merge results.

    ``timeout`` overrides the per-request timeout for this pass only (e.g. the
    interactive bulk "discover & save" flow uses a longer timeout than quick
    ad-hoc CLI calls). ``verbose_errors`` surfaces each source's per-request
    failure at WARNING with its real exception type instead of a terse DEBUG
    line, so a live run shows *why* it fell back to sample data.
    """
    filters = filters or config.to_filters()
    source_names = sources or config.sources
    ctx = DiscoveryContext(
        filters=filters,
        timeout=config.timeout if timeout is None else timeout,
        offline=config.offline,
        verbose_errors=verbose_errors,
    )

    collected: list[IPRecord] = []

    def _run(name: str) -> list[IPRecord]:
        try:
            src = build_source(name, ctx)
            return src.discover()
        except KeyError as exc:
            log.warning("skipping unknown source: %s", exc)
            return []
        except Exception as exc:  # noqa: BLE001
            log.warning("source %s crashed: %s", name, exc)
            return []

    with ThreadPoolExecutor(max_workers=max(1, config.concurrency)) as pool:
        futures = {pool.submit(_run, n): n for n in source_names}
        for fut in as_completed(futures):
            collected.extend(fut.result())

    log.info(
        "discovered %d raw records from %d source(s)",
        len(collected),
        len(source_names),
    )
    return collected


def process(
    records: list[IPRecord],
    filters: Filters,
    *,
    collapse: bool = False,
) -> list[IPRecord]:
    """Filter then normalize (and optionally collapse) discovered records."""
    filtered = apply_filters(records, filters)
    normalized = normalize_records(filtered)
    if collapse:
        normalized = collapse_prefixes(normalized)
    log.info("processed down to %d records", len(normalized))
    return normalized


def check_reachability(records: list[IPRecord], config: Config) -> list[IPRecord]:
    """Populate alive status, optional port scan, and optional global check."""
    reach = config.reachability
    if reach.get("enabled", True):
        check_alive_bulk(
            records,
            method=reach.get("method", "auto"),
            timeout=config.timeout,
            tcp_probe_port=int(reach.get("tcp_probe_port", 80)),
            concurrency=config.concurrency,
        )

    ports = list(reach.get("ports", []) or [])
    if ports:
        probe_records(
            records,
            ports,
            timeout=config.timeout,
            concurrency=config.concurrency,
        )

    gc = config.global_check
    if gc.get("enabled", False):
        _run_global_checks(records, config)

    return records


def _run_global_checks(records: list[IPRecord], config: Config) -> None:
    gc = config.global_check
    max_targets = int(gc.get("max_targets", 10))
    port = int(config.reachability.get("tcp_probe_port", 80))
    providers = build_providers(str(gc.get("provider", "check-host")))

    # Only probe alive (or unknown-but-public) records, capped for politeness.
    candidates = [r for r in records if r.alive is not False][:max_targets]

    def _worker(rec: IPRecord) -> None:
        try:
            result = check_abroad(
                rec.sample_host(),
                providers=providers,
                timeout=config.timeout,
                port=port,
            )
            rec.global_reachable = result.reachable
        except Exception as exc:  # noqa: BLE001
            rec.notes = f"{rec.notes}; global-check error: {exc}".strip("; ")

    with ThreadPoolExecutor(max_workers=max(1, min(config.concurrency, 8))) as pool:
        futures = [pool.submit(_worker, r) for r in candidates]
        for _ in as_completed(futures):
            pass


def run_pipeline(
    config: Config,
    *,
    sources: list[str] | None = None,
    collapse: bool = False,
) -> list[IPRecord]:
    """Full pipeline: discover -> process -> reachability."""
    filters = config.to_filters()
    raw = discover(config, filters, sources=sources)
    processed = process(raw, filters, collapse=collapse)
    return check_reachability(processed, config)
