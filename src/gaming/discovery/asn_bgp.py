"""ASN / BGP based discovery using the RIPEstat public data API.

Given seed ASNs, this source fetches announced prefixes via RIPEstat's
``announced-prefixes`` endpoint. Offline or on failure it returns sample data.
"""

from __future__ import annotations

from ..models import IPRecord, normalize_asn
from ..utils.http import HTTPError, get_json
from .base import Source


class ASNBGPSource(Source):
    name = "asn_bgp"

    _PREFIXES_URL = (
        "https://stat.ripe.net/data/announced-prefixes/data.json?resource={asn}"
    )

    def _discover_online(self) -> list[IPRecord]:
        seeds = self.context.filters.asns
        if not seeds:
            return []
        records: list[IPRecord] = []
        for seed in seeds:
            asn_num = seed[2:] if seed.upper().startswith("AS") else seed
            url = self._PREFIXES_URL.format(asn=asn_num)
            try:
                data = get_json(url, timeout=self.context.timeout)
            except HTTPError as exc:
                self._report_request_error(f"RIPEstat query for {seed}", exc)
                continue
            prefixes = (data.get("data") or {}).get("prefixes") or []
            for entry in prefixes:
                prefix = entry.get("prefix")
                if not prefix:
                    continue
                try:
                    records.append(
                        IPRecord(
                            prefix=prefix,
                            source=self.name,
                            asn=normalize_asn(seed),
                            organization=None,
                            country=None,
                            notes="announced prefix via RIPEstat",
                        )
                    )
                except ValueError:
                    continue
        return records

    def _sample_data(self) -> list[IPRecord]:
        return [
            IPRecord(
                prefix="185.51.200.0/22",
                source=self.name,
                asn="AS58224",
                organization="Iran Telecommunication Company (sample)",
                country="IR",
                provider="tci",
                notes="BGP sample — Iranian datacenter prefix",
            ),
            IPRecord(
                prefix="188.114.96.0/20",
                source=self.name,
                asn="AS13335",
                organization="Cloudflare (sample)",
                country="US",
                provider="cloudflare",
                notes="BGP sample — foreign datacenter prefix",
            ),
            IPRecord(
                prefix="2a01:4f8::/29",
                source=self.name,
                asn="AS24940",
                organization="Hetzner Online (sample)",
                country="DE",
                provider="hetzner",
                notes="BGP sample — foreign datacenter IPv6 prefix",
            ),
        ]
