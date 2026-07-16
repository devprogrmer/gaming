"""RIR / public allocation based discovery.

RIRs publish delegated-statistics files listing allocations per country.
This source can parse such data; offline or on failure it returns samples
covering Iranian and foreign datacenter allocations.
"""

from __future__ import annotations

import ipaddress

from ..models import IPRecord
from ..utils.http import HTTPError, get_text
from .base import Source


class RIRSource(Source):
    name = "rir"

    # RIPE NCC delegated statistics (large file; parsed line-by-line).
    _DELEGATED = "https://ftp.ripe.net/pub/stats/ripencc/delegated-ripencc-latest"

    def _discover_online(self) -> list[IPRecord]:
        countries = set(self.context.filters.countries)
        if not countries:
            # Avoid downloading and parsing the entire delegated file with no
            # country filter; fall back to sample data instead.
            return []
        try:
            body = get_text(self._DELEGATED, timeout=self.context.timeout)
        except HTTPError as exc:
            self.log.debug("RIR delegated fetch failed: %s", exc)
            return []
        return list(self._parse_delegated(body, countries))

    def _parse_delegated(self, body: str, countries: set[str]):
        # Format: registry|cc|type|start|value|date|status[|...]
        for line in body.splitlines():
            if not line or line.startswith("#") or line.endswith("summary"):
                continue
            parts = line.split("|")
            if len(parts) < 7 or parts[2] != "ipv4":
                continue
            cc = parts[1].upper()
            if cc not in countries:
                continue
            start, value = parts[3], parts[4]
            try:
                count = int(value)
                # value is a host count; derive the CIDR prefix length.
                prefixlen = 32 - (count.bit_length() - 1)
                net = ipaddress.ip_network(f"{start}/{prefixlen}", strict=False)
            except (ValueError, TypeError):
                continue
            yield IPRecord(
                prefix=str(net),
                source=self.name,
                asn=None,
                organization=None,
                country=cc,
                notes="RIR delegated allocation",
            )

    def _sample_data(self) -> list[IPRecord]:
        return [
            IPRecord(
                prefix="2.144.0.0/13",
                source=self.name,
                asn=None,
                organization="Iran allocation (sample)",
                country="IR",
                provider=None,
                notes="RIR sample — Iranian allocation",
            ),
            IPRecord(
                prefix="146.75.0.0/16",
                source=self.name,
                asn="AS54113",
                organization="Fastly (sample)",
                country="US",
                provider="fastly",
                notes="RIR sample — foreign datacenter allocation",
            ),
        ]
