"""Filtering/formatting helpers shared by the terminal menu and web dashboard.

These small, pure functions were originally defined on
:mod:`gaming.interactive.menu`. They are hoisted here so the web layer can reuse
the exact same partial-match / bare-IP-export logic instead of duplicating it;
``menu.py`` re-exports them so its existing callers and tests are unaffected.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Iterable


def parse_first_octets(input_str: str) -> list[int]:
    """Parse a comma-separated string of first octets into a validated list.

    Each token must be an integer in the range 0-255. Blank tokens are
    ignored, duplicates are removed (order preserved). Raises ``ValueError``
    if any token is non-numeric or out of range, so the caller can show the
    offending value to the user.
    """
    octets: list[int] = []
    seen: set[int] = set()
    for token in input_str.split(","):
        text = token.strip()
        if not text:
            continue
        try:
            value = int(text)
        except ValueError:
            raise ValueError(f"{text!r} is not a number") from None
        if not 0 <= value <= 255:
            raise ValueError(f"{value} is out of range (0-255)")
        if value not in seen:
            seen.add(value)
            octets.append(value)
    return octets


def matches_first_octet(prefix: str, allowed_octets: Iterable[int]) -> bool:
    """Return True if the first octet of ``prefix`` is in ``allowed_octets``.

    ``prefix`` may be a CIDR (``185.51.200.0/22``) or a bare IP. IPv6 prefixes
    have no dotted first octet and never match. Malformed prefixes are treated
    as non-matching rather than raising, so a single bad record can't abort a
    whole filtering pass.
    """
    allowed = set(allowed_octets)
    if not allowed:
        return True
    try:
        net = ipaddress.ip_network(prefix, strict=False)
    except ValueError:
        return False
    if net.version != 4:
        return False
    first_octet = int(net.network_address) >> 24 & 0xFF
    return first_octet in allowed


def matches_cidr_query(prefix: str, query: str) -> bool:
    """Partial-match a CIDR/IP against a free-text ``query``.

    Empty query matches everything. A numeric query like ``85`` matches any
    prefix whose dotted form starts with that token (``85.x.y.z`` — the shared
    "type 85 to see every 85.* range" search used by both front ends). Any
    other query is a case-insensitive substring test over the prefix text.
    Malformed input never raises.
    """
    q = query.strip()
    if not q:
        return True
    text = str(prefix).strip()
    if not text:
        return False
    # Anchor a pure-number query to the leading octet(s) so "85" doesn't also
    # match "185.85.0.0". Split on the CIDR boundary first.
    addr = text.split("/", 1)[0]
    if q.isdigit():
        octets = addr.split(".")
        return bool(octets) and octets[0].startswith(q)
    return q.lower() in text.lower()


def record_matches(
    rec: object,
    *,
    query: str = "",
    provider: str = "",
    country: str = "",
    asn: str = "",
) -> bool:
    """True when an ``IPRecord``-like object passes all supplied web filters.

    Every filter is optional (blank = ignore) and combined with AND. ``query``
    is the partial CIDR match above; ``provider`` matches provider/organization
    substrings; ``country`` is an exact (case-insensitive) code; ``asn`` is a
    substring of the record's ASN. Missing attributes are treated as empty.
    """
    prefix = getattr(rec, "prefix", "") or ""
    if not matches_cidr_query(prefix, query):
        return False

    if provider:
        p = (getattr(rec, "provider", "") or "").lower()
        o = (getattr(rec, "organization", "") or "").lower()
        if provider.lower() not in p and provider.lower() not in o:
            return False

    if country:
        rc = (getattr(rec, "country", "") or "").upper()
        if rc != country.strip().upper():
            return False

    if asn:
        ra = (getattr(rec, "asn", "") or "").upper()
        if asn.strip().upper() not in ra:
            return False

    return True


def format_bare_ips(hosts: Iterable[str]) -> str:
    """Render hosts as a copy-paste-ready block: one bare IP per line.

    De-duplicates while preserving order and strips whitespace. Produces no
    prefixes, symbols, colours, or headers — just the addresses — so the output
    can be piped or pasted straight into another tool. Returns an empty string
    when there are no hosts.
    """
    seen: set[str] = set()
    out: list[str] = []
    for host in hosts:
        ip = host.strip()
        if ip and ip not in seen:
            seen.add(ip)
            out.append(ip)
    return "\n".join(out)
