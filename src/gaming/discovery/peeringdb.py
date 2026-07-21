"""PeeringDB-based discovery.

PeeringDB exposes network and IX-participation metadata. PeeringDB does not
publish routed prefixes, but for a seed ASN it authoritatively provides the
network's organization name and its per-exchange peering IP addresses
(``netixlan``). This source resolves the org via ``/api/net`` and emits one
record per peering IP (as a host prefix). Offline (``--offline``) or on any
failure it returns representative sample records.
"""

from __future__ import annotations

from typing import Any

from ..models import IPRecord, normalize_asn
from ..utils.http import HTTPError, get_json
from .base import Source


class PeeringDBSource(Source):
    name = "peeringdb"

    _NET_URL = "https://www.peeringdb.com/api/net?asn={asn}"
    _NETIXLAN_URL = "https://www.peeringdb.com/api/netixlan?asn={asn}"

    def _discover_online(self) -> list[IPRecord]:
        seeds = self.context.filters.asns
        if not seeds:
            return []

        records: list[IPRecord] = []
        for seed in seeds:
            asn_num = seed[2:] if seed.upper().startswith("AS") else seed
            org = self._lookup_org(asn_num)
            for prefix in self._peering_ips(asn_num):
                try:
                    records.append(
                        IPRecord(
                            prefix=prefix,
                            source=self.name,
                            asn=normalize_asn(seed),
                            organization=org,
                            notes="PeeringDB netixlan peering address",
                        )
                    )
                except ValueError:
                    continue
        return records

    # ---- helpers ---------------------------------------------------------
    def _lookup_org(self, asn_num: str) -> str | None:
        try:
            data = get_json(
                self._NET_URL.format(asn=asn_num), timeout=self.context.timeout
            )
        except HTTPError as exc:
            self._report_request_error(f"PeeringDB net lookup for AS{asn_num}", exc)
            return None
        rows = _rows(data)
        if not rows:
            return None
        first = rows[0]
        name = first.get("name") if isinstance(first, dict) else None
        return name if isinstance(name, str) and name.strip() else None

    def _peering_ips(self, asn_num: str) -> list[str]:
        try:
            data = get_json(
                self._NETIXLAN_URL.format(asn=asn_num), timeout=self.context.timeout
            )
        except HTTPError as exc:
            self._report_request_error(f"PeeringDB netixlan for AS{asn_num}", exc)
            return []
        prefixes: list[str] = []
        for row in _rows(data):
            if not isinstance(row, dict):
                continue
            for key in ("ipaddr4", "ipaddr6"):
                addr = row.get(key)
                if isinstance(addr, str) and addr.strip():
                    prefixes.append(addr.strip())
        return prefixes

    def _sample_data(self) -> list[IPRecord]:
        return [
            IPRecord(
                prefix="194.5.175.0/24",
                source=self.name,
                asn="AS205585",
                organization="ArvanCloud (sample)",
                country="IR",
                provider="arvancloud",
                notes="PeeringDB sample — Iranian CDN/datacenter",
            ),
            IPRecord(
                prefix="45.142.212.0/24",
                source=self.name,
                asn="AS49453",
                organization="Global Layer B.V. (sample)",
                country="NL",
                provider="global layer",
                notes="PeeringDB sample — foreign datacenter",
            ),
        ]


def _rows(data: Any) -> list[Any]:
    """PeeringDB wraps result rows in a top-level ``{"data": [...]}`` object."""
    if isinstance(data, dict):
        rows = data.get("data")
        if isinstance(rows, list):
            return rows
    return []
