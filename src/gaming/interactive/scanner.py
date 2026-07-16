"""Scan orchestration for interactive mode.

Ties the pieces together:

    ranges  ->  host expansion  ->  (optional) alive sweep  ->  latency probe
            ->  GOOD/MEDIUM/BAD classification  ->  persistence

A live progress callback is invoked as each host completes so the caller can
drive a progress bar. The heavy lifting (concurrent probing) lives in
:mod:`.pinger`; this module adds the alive-discovery fast path and result
assembly.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from ..logging_setup import get_logger
from . import ranges as ranges_mod
from .classify import ProbeResult, classify, summarize
from .pinger import ping_host, scan_hosts
from .settings import Settings
from .storage import HistoryStore

log = get_logger("gaming.interactive.scanner")


@dataclass(slots=True)
class ScanReport:
    """Full outcome of a scan, ready to display and persist."""

    scope: str
    results: list[tuple[ProbeResult, str]]  # (probe, verdict) pairs

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def counts(self) -> dict[str, int]:
        return summarize([verdict for _p, verdict in self.results])

    def alive_hosts(self) -> list[str]:
        """Hosts that responded to at least one probe."""
        return [p.host for p, _v in self.results if p.reachable]


ProgressHook = Callable[[ProbeResult, str], None]


def _prepare_hosts(scope: str, settings: Settings) -> list[str]:
    cidrs = ranges_mod.load_ranges(scope)
    return ranges_mod.expand_hosts(
        cidrs,
        sample_per_range=settings.sample_per_range,
        max_hosts=settings.max_hosts,
    )


def run_scan(
    scope: str,
    settings: Settings,
    *,
    on_result: ProgressHook | None = None,
    hosts: list[str] | None = None,
) -> ScanReport:
    """Run a full latency scan for a scope and classify every host.

    ``on_result`` is called with ``(ProbeResult, verdict)`` for each completed
    host, enabling a live progress bar. ``hosts`` overrides range expansion
    (used when scanning a pre-discovered alive set).
    """
    s = settings.clamped()
    targets = hosts if hosts is not None else _prepare_hosts(scope, s)

    results: list[tuple[ProbeResult, str]] = []

    def _handle(probe: ProbeResult) -> None:
        verdict = classify(probe, s)
        results.append((probe, verdict))
        if on_result is not None:
            on_result(probe, verdict)

    scan_hosts(
        targets,
        count=s.ping_count,
        timeout=s.timeout,
        concurrency=s.concurrency,
        on_result=_handle,
    )

    return ScanReport(scope=scope, results=results)


def discover_alive(
    scope: str,
    settings: Settings,
    *,
    on_result: Callable[[ProbeResult], None] | None = None,
    hosts: list[str] | None = None,
) -> list[str]:
    """Fast single-probe sweep to find which hosts are alive.

    Uses one probe per host (regardless of ``ping_count``) for speed. Returns
    the list of hosts that answered, in input order.
    """
    s = settings.clamped()
    targets = hosts if hosts is not None else _prepare_hosts(scope, s)

    alive: list[str] = []

    def _handle(probe: ProbeResult) -> None:
        if probe.reachable:
            alive.append(probe.host)
        if on_result is not None:
            on_result(probe)

    scan_hosts(
        targets,
        count=1,
        timeout=s.timeout,
        concurrency=s.concurrency,
        on_result=_handle,
    )

    # Preserve input order for a stable report.
    alive_set = set(alive)
    return [h for h in targets if h in alive_set]


def persist(report: ScanReport, store: HistoryStore | None = None) -> int:
    """Save a scan report to history. Returns the new scan id."""
    store = store or HistoryStore()
    return store.save_scan(report.scope, report.results)


__all__ = [
    "ScanReport",
    "run_scan",
    "discover_alive",
    "persist",
    "ping_host",
]
