"""Scan orchestration for interactive mode.

Ties the pieces together:

    ranges  ->  host expansion  ->  (optional) alive sweep  ->  latency probe
            ->  GOOD/MEDIUM/BAD classification  ->  persistence

A live progress callback is invoked as each host completes so the caller can
drive a progress bar. The heavy lifting (concurrent probing) lives in
:mod:`.pinger`; this module adds the alive-discovery fast path and result
assembly.

**Iran-origin measurement.** Latency and reachability are measured by the OS
``ping``/TCP connect on the machine running this tool (see :mod:`.pinger`). When
deployed on an Iranian server, every RTT is therefore genuinely measured *from
Iran to the target* — foreign targets are probed Iran→abroad, never via an
Iran-to-Iran shortcut. :func:`summarize_by_group` uses those real measurements
to report which destination country/provider group answers fastest from here.
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

from ..logging_setup import get_logger
from ..reachability.global_check import build_providers, check_abroad
from ..reachability.ports import probe_ports
from . import ranges as ranges_mod
from .classify import (
    CombinedResult,
    ProbeResult,
    classify,
    classify_bidirectional,
    summarize,
    summarize_combined,
)
from .pinger import ping_host, scan_hosts
from .settings import Settings
from .storage import HistoryStore

log = get_logger("gaming.interactive.scanner")

# The local per-probe timeout (Settings.timeout, ~2s) is far too short for the
# check-host.net HTTP round-trips; use a floor so the abroad check isn't
# starved into spurious failures.
_GLOBAL_TIMEOUT_FLOOR = 5.0


def _normalize_abroad(result) -> tuple[bool | None, int, int, str]:
    """Normalise an abroad result into ``(reachable, ok, total, status)``.

    Accepts an :class:`~gaming.reachability.global_check.AbroadResult` or, for
    backward compatibility with older stubs/callers, a legacy
    ``(reachable, ok, total)`` tuple. A bare tuple has no explicit status, so it
    is mapped to "ok" when a node answered and "not_applicable" otherwise.
    """
    status = getattr(result, "status", None)
    if status is not None:
        return result.reachable, result.nodes_ok, result.nodes_total, status
    reachable, ok, total = result
    derived = "ok" if total > 0 else "not_applicable"
    return reachable, ok, total, derived


@dataclass(slots=True)
class ScanReport:
    """Full outcome of a scan, ready to display and persist."""

    scope: str
    results: list[tuple[ProbeResult, str]]  # (probe, health verdict) pairs
    # Index-aligned with ``results``: the bidirectional/port enrichment for each
    # host. Kept as a parallel list so the existing (ProbeResult, verdict) shape
    # that storage/report/summarize rely on is undisturbed. Empty when no scan
    # populated it (older callers / direct construction).
    combined: list[CombinedResult] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def counts(self) -> dict[str, int]:
        return summarize([verdict for _p, verdict in self.results])

    @property
    def combined_counts(self) -> dict[str, int]:
        """Tally of INTERNATIONAL / IRAN_ONLY / ABROAD_ONLY / UNREACHABLE."""
        return summarize_combined([self.combined_verdict(c) for c in self.combined])

    @staticmethod
    def combined_verdict(c: CombinedResult) -> str:
        return classify_bidirectional(c.iran_reachable, c.abroad_reachable)

    def alive_hosts(self) -> list[str]:
        """Hosts that responded to at least one probe."""
        return [p.host for p, _v in self.results if p.reachable]


@dataclass(slots=True)
class GroupLatency:
    """Aggregated latency for one destination grouping (country or provider)."""

    key: str
    live: int = 0
    total: int = 0
    _samples: list[float] = field(default_factory=list)

    def add(self, avg_ms: float | None, *, reachable: bool) -> None:
        self.total += 1
        if reachable:
            self.live += 1
            if avg_ms is not None:
                self._samples.append(avg_ms)

    @property
    def avg_ms(self) -> float | None:
        if not self._samples:
            return None
        return sum(self._samples) / len(self._samples)


def summarize_by_group(
    results: list[tuple[ProbeResult, str]],
    host_to_record: dict[str, object],
    *,
    by: str = "country",
) -> list[GroupLatency]:
    """Bucket probe results by destination country (or provider) and average RTT.

    Only reachable probes contribute to the latency average, so the returned
    groups answer "which destination grouping is fastest **from the Iranian
    server running this scan**." Groups are sorted best-first: live groups with
    a known average latency ascending, then groups with no measurable latency.

    Args:
        results: ``(ProbeResult, verdict)`` pairs from a completed scan.
        host_to_record: maps a probe host back to its discovered record (used to
            read ``country``/``provider``/``organization``).
        by: ``"country"`` (default) or ``"provider"`` — the grouping attribute.

    Returns:
        A list of :class:`GroupLatency`, best (lowest) average latency first.
    """
    groups: dict[str, GroupLatency] = {}
    for probe, _verdict in results:
        rec = host_to_record.get(probe.host)
        key = _group_key(rec, by)
        group = groups.get(key)
        if group is None:
            group = groups[key] = GroupLatency(key=key)
        group.add(probe.avg_ms, reachable=probe.reachable)

    def _sort_key(g: GroupLatency) -> tuple[int, float]:
        # Measurable groups first (0), sorted by ascending latency; the rest last.
        if g.avg_ms is None:
            return (1, float("inf"))
        return (0, g.avg_ms)

    return sorted(groups.values(), key=_sort_key)


def _group_key(rec: object, by: str) -> str:
    if rec is None:
        return "unknown"
    if by == "provider":
        value = getattr(rec, "provider", None) or getattr(rec, "organization", None)
    else:
        value = getattr(rec, "country", None)
    text = (value or "").strip()
    return text or "unknown"


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

    After the local latency pass, an independent abroad-reachability pass
    (check-host.net) runs for up to ``settings.max_global_targets`` hosts,
    prioritising hosts that answered locally. The two passes are fully
    decoupled: any failure in the abroad pass is logged and leaves the local
    results untouched.
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

    combined = _run_abroad_pass([p for p, _v in results], s)
    _run_port_scan(combined, s)
    return ScanReport(scope=scope, results=results, combined=combined)


def _run_abroad_pass(
    probes: list[ProbeResult], settings: Settings
) -> list[CombinedResult]:
    """Enrich local probes with abroad reachability (concurrent, fail-soft).

    Returns a :class:`CombinedResult` per probe, index-aligned with ``probes``.
    When the abroad check is disabled, or a host is beyond the per-scan cap, or
    it is non-public, ``abroad_reachable`` stays ``None`` ("not checked").
    Alive hosts are prioritised for the limited abroad budget.
    """
    combined = [CombinedResult(probe=p) for p in probes]
    if not settings.check_global or settings.max_global_targets <= 0:
        return combined

    by_host = {c.host: c for c in combined}
    # Alive-first ordering so the limited abroad budget is spent on hosts that
    # actually answered locally; de-duplicate by host.
    ordered: list[str] = []
    seen: set[str] = set()
    for want_alive in (True, False):
        for c in combined:
            if c.host in seen:
                continue
            if c.iran_reachable is want_alive:
                seen.add(c.host)
                ordered.append(c.host)
    chosen = ordered[: settings.max_global_targets]

    timeout = max(_GLOBAL_TIMEOUT_FLOOR, float(settings.timeout))
    providers = build_providers(getattr(settings, "abroad_provider", "check-host"))

    def _worker(host: str) -> None:
        try:
            result = check_abroad(
                host,
                providers=providers,
                timeout=timeout,
                min_ok_fraction=settings.global_min_ok_fraction,
            )
        except Exception as exc:  # noqa: BLE001 - abroad check must never break scan
            log.warning(
                "abroad check failed for %s: %s: %s",
                host,
                type(exc).__name__,
                exc,
            )
            return
        c = by_host.get(host)
        if c is not None:
            reachable, ok, total, status = _normalize_abroad(result)
            c.abroad_reachable = reachable
            c.abroad_nodes_ok = ok
            c.abroad_nodes_total = total
            c.abroad_status = status

    workers = max(1, min(settings.concurrency, 8, len(chosen)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_worker, h) for h in chosen]
        for _ in as_completed(futures):
            pass

    return combined


def _run_port_scan(combined: list[CombinedResult], settings: Settings) -> None:
    """Populate ``open_ports`` on each result via a plain TCP-connect scan.

    Fully fail-soft and independent of the latency/abroad passes: only hosts
    that answered locally are probed (a dead host has nothing to serve), and a
    connect error against one host or port never aborts the scan. Does nothing
    when the port scan is disabled or no valid ports are configured.
    """
    if not settings.scan_ports:
        return
    ports = settings.port_list()
    if not ports:
        return

    live = [c for c in combined if c.iran_reachable]
    if not live:
        return

    timeout = max(0.5, float(settings.timeout))

    def _worker(c: CombinedResult) -> None:
        try:
            c.open_ports = probe_ports(
                c.host,
                ports,
                timeout=timeout,
                concurrency=max(1, min(len(ports), 16)),
            )
        except Exception as exc:  # noqa: BLE001 - port scan must never break a scan
            log.warning(
                "port scan failed for %s: %s: %s",
                c.host,
                type(exc).__name__,
                exc,
            )

    workers = max(1, min(settings.concurrency, 16, len(live)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_worker, c) for c in live]
        for _ in as_completed(futures):
            pass


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
    return store.save_scan(report.scope, report.results, combined=report.combined)


__all__ = [
    "ScanReport",
    "GroupLatency",
    "CombinedResult",
    "run_scan",
    "discover_alive",
    "summarize_by_group",
    "persist",
    "ping_host",
]
