"""RDAP-based discovery.

RDAP (RFC 7483) is the modern successor to WHOIS. Given seed ASNs, this source
performs two authoritative lookups per ASN via the RDAP bootstrap redirector:

  * ``autnum`` — the registration record for the AS itself, which yields the
    owning organization and country.
  * the ASN's announced prefixes (via RIPEstat), each enriched with the org and
    country resolved from the autnum record.

Offline (``--offline``) or on any failure it returns representative sample
records so the pipeline always produces output.
"""

from __future__ import annotations

from typing import Any

from ..models import IPRecord, normalize_asn
from ..utils.http import HTTPError, get_json
from .base import Source


class RDAPSource(Source):
    name = "rdap"

    # RDAP endpoints via the bootstrap redirector (follows to the authoritative RIR).
    _RDAP_AUTNUM = "https://rdap.org/autnum/{asn}"
    # Announced prefixes for an ASN (RIPEstat), used as the prefix producer.
    _PREFIXES_URL = (
        "https://stat.ripe.net/data/announced-prefixes/data.json?resource={asn}"
    )

    def _discover_online(self) -> list[IPRecord]:
        # RDAP is keyed by object, not enumerable, so we need seed ASNs to have
        # something authoritative to resolve. Without seeds there is nothing
        # deterministic to query; fall back to sample data.
        seeds = self.context.filters.asns
        if not seeds:
            return []

        records: list[IPRecord] = []
        for seed in seeds:
            asn_num = self._asn_number(seed)
            org, country = self._lookup_autnum(asn_num)
            for prefix in self._announced_prefixes(asn_num):
                try:
                    records.append(
                        IPRecord(
                            prefix=prefix,
                            source=self.name,
                            asn=normalize_asn(seed),
                            organization=org,
                            country=country,
                            notes="RDAP autnum + announced prefix",
                        )
                    )
                except ValueError:
                    # Skip anything that is not a valid prefix.
                    continue
        return records

    # ---- helpers ---------------------------------------------------------
    @staticmethod
    def _asn_number(seed: str) -> str:
        return seed[2:] if seed.upper().startswith("AS") else seed

    def _lookup_autnum(self, asn_num: str) -> tuple[str | None, str | None]:
        """Resolve (organization, country) for an ASN from its RDAP autnum."""
        try:
            data = get_json(
                self._RDAP_AUTNUM.format(asn=asn_num), timeout=self.context.timeout
            )
        except HTTPError as exc:
            self._report_request_error(f"RDAP autnum lookup for AS{asn_num}", exc)
            return None, None
        return self._parse_autnum(data)

    @staticmethod
    def _parse_autnum(data: Any) -> tuple[str | None, str | None]:
        """Extract organization name and country from an RDAP autnum object."""
        if not isinstance(data, dict):
            return None, None

        country = data.get("country")
        if isinstance(country, str):
            country = country.strip().upper() or None
        else:
            country = None

        # Prefer the object name, then a registrant/administrative vCard "fn".
        org = data.get("name") if isinstance(data.get("name"), str) else None
        for entity in data.get("entities") or []:
            if not isinstance(entity, dict):
                continue
            fn = _vcard_field(entity.get("vcardArray"), "fn")
            if fn:
                roles = entity.get("roles") or []
                if not org or "registrant" in roles:
                    org = fn
                if "registrant" in roles:
                    break
        return (org or None), country

    def _announced_prefixes(self, asn_num: str) -> list[str]:
        try:
            data = get_json(
                self._PREFIXES_URL.format(asn=asn_num), timeout=self.context.timeout
            )
        except HTTPError as exc:
            self._report_request_error(f"RIPEstat prefixes for AS{asn_num}", exc)
            return []
        prefixes = (data.get("data") or {}).get("prefixes") or []
        return [p["prefix"] for p in prefixes if isinstance(p, dict) and p.get("prefix")]

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


def _vcard_field(vcard_array: Any, field: str) -> str | None:
    """Pull a named property (e.g. ``fn``) out of a jCard/vcardArray structure.

    A vcardArray looks like ``["vcard", [ ["fn", {}, "text", "ACME"], ... ]]``.
    """
    if not isinstance(vcard_array, list) or len(vcard_array) < 2:
        return None
    for entry in vcard_array[1]:
        if (
            isinstance(entry, list)
            and len(entry) >= 4
            and entry[0] == field
            and isinstance(entry[3], str)
            and entry[3].strip()
        ):
            return entry[3].strip()
    return None
