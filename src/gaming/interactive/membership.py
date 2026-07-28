"""Reverse lookup: which stored CIDR does an IP address belong to?

The rest of the tool asks "what ranges exist?"; this asks the inverse — given
one address, which of the ranges we already know about contains it, and who
operates that range?

Overlapping and nested prefixes are normal in real allocation data (a /16
allocation containing a more specific /24 announcement), so a single address can
legitimately match several stored ranges. Every match is reported, most specific
first, rather than stopping at the first hit.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass

from ..logging_setup import get_logger
from . import ranges

log = get_logger("gaming.interactive.membership")


@dataclass(slots=True)
class Match:
    """One stored range that contains the queried address."""

    cidr: str
    group: str
    origin: str = "custom"
    country: str | None = None
    provider: str | None = None

    @property
    def prefixlen(self) -> int:
        try:
            return ipaddress.ip_network(self.cidr, strict=False).prefixlen
        except ValueError:
            return -1

    def as_dict(self) -> dict[str, object]:
        return {
            "cidr": self.cidr,
            "group": self.group,
            "origin": self.origin,
            "country": self.country,
            "provider": self.provider,
        }


@dataclass(slots=True)
class LookupResult:
    """Outcome of an IP membership lookup."""

    ip: str
    matches: list[Match]
    #: Populated only when a live registry lookup was requested and succeeded.
    live: dict[str, object] | None = None

    @property
    def found(self) -> bool:
        return bool(self.matches)

    def as_dict(self) -> dict[str, object]:
        return {
            "ip": self.ip,
            "found": self.found,
            "matches": [m.as_dict() for m in self.matches],
            "live": self.live,
        }


def lookup_ip(ip: str, *, include_bundled: bool = True) -> LookupResult:
    """Find every stored CIDR containing ``ip``.

    Checks all four categories (``iran_datacenter``, ``iran_cdn``,
    ``foreign_datacenter``, ``foreign_cdn``) plus legacy scope groups and
    custom/discovered entries. With ``include_bundled`` the shipped range lists
    are searched too, so a match is reported even before any discovery has run.

    Raises :class:`ValueError` if ``ip`` is not a valid IP address.
    """
    addr = ipaddress.ip_address(ip.strip())

    matches: list[Match] = []
    seen: set[tuple[str, str]] = set()
    seen_cidrs: set[str] = set()

    def consider(cidr: str, group: str, origin: str, country, provider) -> None:
        try:
            net = ipaddress.ip_network(cidr, strict=False)
        except ValueError:
            return
        if addr.version != net.version or addr not in net:
            return
        key = (str(net), group)
        if key in seen:
            return
        # A bundled list entry duplicating a CIDR we already matched from stored
        # data carries no country/provider, so reporting it again would only add
        # a blank row next to the informative one.
        if origin == "bundled" and str(net) in seen_cidrs:
            return
        seen.add(key)
        seen_cidrs.add(str(net))
        matches.append(
            Match(
                cidr=str(net),
                group=group,
                origin=origin,
                country=country,
                provider=provider,
            )
        )

    # Stored custom/discovered entries carry the richest metadata, so they are
    # consulted first and their (country, provider) wins for a given CIDR.
    for group, entries in ranges._read_custom().items():
        for entry in entries:
            consider(entry.cidr, group, entry.origin, entry.country, entry.provider)

    if include_bundled:
        for scope in ranges.SCOPES:
            for cidr in ranges._read_bundled(scope):
                consider(cidr, scope, "bundled", None, None)

    # Most specific first: a /24 is a more useful answer than the /8 above it.
    matches.sort(key=lambda m: m.prefixlen, reverse=True)
    return LookupResult(ip=str(addr), matches=matches)


def live_lookup(ip: str, *, timeout: float = 5.0) -> dict[str, object] | None:
    """Resolve ``ip``'s real operator from public registry data, on demand.

    Used when an address matches nothing stored and the user wants to know who
    actually owns it. Reuses the existing RDAP source's request/parse logic
    rather than introducing another HTTP client. Returns ``None`` when nothing
    could be resolved (offline, rate-limited, or genuinely unregistered).
    """
    try:
        addr = ipaddress.ip_address(ip.strip())
    except ValueError:
        return None

    from ..discovery.base import DiscoveryContext
    from ..discovery.rdap import RDAPSource, _vcard_field
    from ..models import Filters
    from ..utils.http import HTTPError, get_json

    context = DiscoveryContext(filters=Filters(), timeout=timeout)
    source = RDAPSource(context)
    try:
        data = get_json(f"https://rdap.org/ip/{addr}", timeout=timeout)
    except HTTPError as exc:
        log.debug("live RDAP lookup failed for %s: %s", addr, exc)
        return None
    if not isinstance(data, dict):
        return None

    org, country = source._parse_autnum(data)
    handle = data.get("handle") if isinstance(data.get("handle"), str) else None
    start = (
        data.get("startAddress")
        if isinstance(data.get("startAddress"), str)
        else None
    )
    end = data.get("endAddress") if isinstance(data.get("endAddress"), str) else None
    cidr = _cidr_from_range(start, end) or handle

    result: dict[str, object] = {
        "ip": str(addr),
        "cidr": cidr,
        "organization": org,
        "country": country,
        "source": "rdap",
    }
    for entity in data.get("entities") or []:
        if isinstance(entity, dict) and not result.get("organization"):
            fn = _vcard_field(entity.get("vcardArray"), "fn")
            if fn:
                result["organization"] = fn
                break
    return result


def _cidr_from_range(start: str | None, end: str | None) -> str | None:
    """Collapse an RDAP ``startAddress``/``endAddress`` pair into a CIDR."""
    if not start or not end:
        return None
    try:
        nets = list(
            ipaddress.summarize_address_range(
                ipaddress.ip_address(start), ipaddress.ip_address(end)
            )
        )
    except (ValueError, TypeError):
        return None
    return str(nets[0]) if nets else None
