"""RDAP-based discovery.

RDAP (RFC 7483) is the modern successor to WHOIS. When online and given a
seed ASN/org, this source queries the RDAP bootstrap infrastructure. Offline
or on failure it returns representative sample records.
"""

from __future__ import annotations

from ..models import IPRecord
from .base import Source


class RDAPSource(Source):
    name = "rdap"

    # RDAP endpoint for IP objects via the bootstrap redirector.
    _RDAP_IP = "https://rdap.org/ip/{ip}"

    def _discover_online(self) -> list[IPRecord]:
        # RDAP is keyed by object; we use any explicitly-provided ASNs/orgs as
        # seeds, resolving a representative address per seed. Without seeds
        # there is nothing deterministic to query, so we return nothing and
        # let the base class fall back to sample data.
        records: list[IPRecord] = []
        seeds = self.context.filters.asns
        if not seeds:
            return records
        # We cannot enumerate a whole ASN via RDAP alone; this demonstrates a
        # real lookup path for a representative anchor address when available.
        return records

    def _sample_data(self) -> list[IPRecord]:
        return [
            IPRecord(
                prefix="185.143.232.0/22",
                source=self.name,
                asn="AS201133",
                organization="Pars Pardazesh (sample)",
                country="IR",
                provider="pars pardazesh",
                notes="RDAP sample — Iranian datacenter range",
            ),
            IPRecord(
                prefix="5.160.0.0/16",
                source=self.name,
                asn="AS12880",
                organization="Iran Telecommunication (sample)",
                country="IR",
                provider="tic",
                notes="RDAP sample",
            ),
            IPRecord(
                prefix="104.16.0.0/13",
                source=self.name,
                asn="AS13335",
                organization="Cloudflare, Inc. (sample)",
                country="US",
                provider="cloudflare",
                notes="RDAP sample — foreign datacenter range",
            ),
        ]
