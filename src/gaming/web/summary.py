"""Provider/country connectivity summary for the dashboard home page (C5).

Answers the tool's core question at a glance: *which providers currently have
working international connectivity from Iran?* For each seed provider it takes
the most recent scan in history, matches that scan's probed hosts to the
provider's CIDRs, and reports the fraction that came back ``INTERNATIONAL``.

Read-only and best-effort: with no scans yet, or a provider with no probed
hosts, it simply reports zero coverage rather than failing.
"""

from __future__ import annotations

import ipaddress
from typing import Any

from ..logging_setup import get_logger

log = get_logger("gaming.web.summary")


def provider_connectivity(store: Any) -> list[dict[str, Any]]:
    """Return per-provider international-connectivity stats from the latest scan.

    Each entry: ``{name, country, category, hosts, international, iran_only,
    fraction}`` where ``fraction`` is ``international / hosts`` (0 when no hosts
    of that provider were in the most recent scan).
    """
    from ..interactive import providers as providers_mod

    latest_rows = _latest_results(store)
    # Pre-parse probed hosts once.
    probed: list[tuple[ipaddress._BaseAddress, str]] = []
    for row in latest_rows:
        try:
            addr = ipaddress.ip_address(row.host)
        except ValueError:
            continue
        probed.append((addr, row.combined_verdict or ""))

    out: list[dict[str, Any]] = []
    for provider in providers_mod.load_providers():
        nets = _provider_nets(provider)
        hosts = 0
        international = 0
        iran_only = 0
        for addr, verdict in probed:
            if any(addr in net for net in nets):
                hosts += 1
                if verdict == "INTERNATIONAL":
                    international += 1
                elif verdict == "IRAN_ONLY":
                    iran_only += 1
        out.append(
            {
                "name": provider.name,
                "country": provider.country,
                "category": provider.category,
                "hosts": hosts,
                "international": international,
                "iran_only": iran_only,
                "fraction": round(international / hosts, 3) if hosts else 0.0,
            }
        )
    # Best international coverage first; providers with no probed hosts sink.
    out.sort(key=lambda e: (e["hosts"] == 0, -e["fraction"], -e["hosts"]))
    return out


def _latest_results(store: Any) -> list:
    scans = store.list_scans(limit=1)
    if not scans:
        return []
    try:
        return store.get_results(scans[0].id)
    except Exception as exc:  # noqa: BLE001 - summary is best-effort
        log.warning("could not load latest scan results: %s", exc)
        return []


def _provider_nets(provider) -> list[ipaddress._BaseNetwork]:
    nets: list[ipaddress._BaseNetwork] = []
    for cidr in provider.cidrs:
        try:
            nets.append(ipaddress.ip_network(str(cidr), strict=False))
        except ValueError:
            continue
    return nets
