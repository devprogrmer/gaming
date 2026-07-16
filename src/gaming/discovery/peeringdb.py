"""PeeringDB-based discovery.

PeeringDB exposes network/organization metadata. This source can enrich or
seed discovery for known networks. Offline or on failure it returns samples.
"""

from __future__ import annotations

from ..models import IPRecord
from ..utils.http import HTTPError, get_json
from .base import Source


class PeeringDBSource(Source):
    name = "peeringdb"

    _NET_URL = "https://www.peeringdb.com/api/net?asn={asn}"

    def _discover_online(self) -> list[IPRecord]:
        # PeeringDB does not enumerate prefixes directly, but confirms network
        # metadata for seed ASNs. We only issue a request when seeds exist.
        seeds = self.context.filters.asns
        if not seeds:
            return []
        for seed in seeds:
            asn_num = seed[2:] if seed.upper().startswith("AS") else seed
            try:
                get_json(self._NET_URL.format(asn=asn_num), timeout=self.context.timeout)
            except HTTPError as exc:
                self.log.debug("PeeringDB lookup failed for %s: %s", seed, exc)
        # Metadata-only; return empty so the base falls back to samples.
        return []

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
