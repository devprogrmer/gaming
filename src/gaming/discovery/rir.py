"""RIR / public allocation based discovery.

RIRs publish delegated-statistics files listing allocations per country.
This source can parse such data; offline or on failure it returns samples
covering Iranian and foreign datacenter allocations.

:func:`parse_delegated_networks` is the shared parser used both here and by the
exhaustive country sweep in :mod:`gaming.discovery.exhaustive`.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Iterator

from ..models import IPRecord
from ..utils.http import HTTPError, get_text
from .base import Source

#: RIPE NCC delegated statistics (large file; parsed line-by-line).
DELEGATED_URL = "https://ftp.ripe.net/pub/stats/ripencc/delegated-ripencc-latest"

#: Delegation statuses that represent a real allocation to an end party.
#: ``reserved``/``available`` blocks are not allocated to anyone.
_ALLOCATED_STATUSES = frozenset({"allocated", "assigned"})


def parse_delegated_networks(
    body: str,
    countries: set[str],
    *,
    include_ipv6: bool = True,
) -> Iterator[tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, str, str]]:
    """Yield ``(network, country, status)`` for each allocation in ``body``.

    The delegated-stats line format is::

        registry|cc|type|start|value|date|status[|opaque-id|...]

    For ``ipv4`` the ``value`` field is a *host count* (not a prefix length),
    and it is not always a power of two — a single line can describe several
    adjacent CIDRs. Such runs are decomposed with
    :func:`ipaddress.summarize_address_range` so no allocated space is lost.
    For ``ipv6`` the ``value`` field already is the prefix length.
    """
    for line in body.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.endswith("summary"):
            continue
        parts = line.split("|")
        if len(parts) < 7:
            continue
        kind = parts[2]
        if kind == "ipv6" and not include_ipv6:
            continue
        if kind not in ("ipv4", "ipv6"):
            continue
        cc = parts[1].upper()
        if countries and cc not in countries:
            continue
        status = parts[6].strip().lower()
        if status not in _ALLOCATED_STATUSES:
            continue
        start, value = parts[3], parts[4]
        try:
            if kind == "ipv6":
                yield ipaddress.IPv6Network(f"{start}/{int(value)}", strict=False), cc, status
                continue
            count = int(value)
            if count <= 0:
                continue
            first = ipaddress.IPv4Address(start)
            last = ipaddress.IPv4Address(int(first) + count - 1)
            for net in ipaddress.summarize_address_range(first, last):
                yield net, cc, status
        except (ValueError, TypeError, ipaddress.AddressValueError):
            continue


class RIRSource(Source):
    name = "rir"

    # Kept as a class attribute for backward compatibility with existing tests
    # and any caller that patched it.
    _DELEGATED = DELEGATED_URL

    def _discover_online(self) -> list[IPRecord]:
        countries = set(self.context.filters.countries)
        if not countries:
            # Avoid downloading and parsing the entire delegated file with no
            # country filter; fall back to sample data instead.
            return []
        try:
            body = get_text(self._DELEGATED, timeout=self.context.timeout)
        except HTTPError as exc:
            self._report_request_error("RIR delegated fetch", exc)
            return []
        return list(self._parse_delegated(body, countries))

    def _parse_delegated(self, body: str, countries: set[str]):
        for net, cc, _status in parse_delegated_networks(body, countries):
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
