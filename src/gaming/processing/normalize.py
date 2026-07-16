"""Normalization of discovered records.

Responsibilities:
  * validate prefixes (already enforced by :class:`IPRecord`)
  * deduplicate identical prefixes, merging metadata
  * optionally collapse adjacent/contained prefixes per (asn, country)
"""

from __future__ import annotations

import ipaddress
from collections.abc import Iterable

from ..logging_setup import get_logger
from ..models import IPRecord

log = get_logger("gaming.normalize")


def _merge(into: IPRecord, other: IPRecord) -> None:
    """Fill missing fields on ``into`` using ``other`` and combine sources."""
    for attr in ("asn", "organization", "country", "provider"):
        if getattr(into, attr) in (None, "") and getattr(other, attr) not in (None, ""):
            setattr(into, attr, getattr(other, attr))
    sources = {s for s in into.source.split("+") if s}
    sources.update(s for s in other.source.split("+") if s)
    into.source = "+".join(sorted(sources))
    if other.notes and other.notes not in into.notes:
        into.notes = f"{into.notes}; {other.notes}".strip("; ")


def normalize_records(records: Iterable[IPRecord]) -> list[IPRecord]:
    """Deduplicate by prefix, merging metadata from duplicate entries."""
    by_prefix: dict[str, IPRecord] = {}
    for rec in records:
        key = rec.prefix
        if key in by_prefix:
            _merge(by_prefix[key], rec)
        else:
            by_prefix[key] = rec
    result = sorted(by_prefix.values(), key=_sort_key)
    log.debug("normalized %d records into %d unique prefixes", 0, len(result))
    return result


def _sort_key(rec: IPRecord):
    net = rec.network
    return (net.version, int(net.network_address), net.prefixlen)


def collapse_prefixes(records: Iterable[IPRecord]) -> list[IPRecord]:
    """Collapse contained/adjacent prefixes that share (asn, country).

    Metadata from the collapsed members is preserved on the surviving record.
    """
    groups: dict[tuple, list[IPRecord]] = {}
    for rec in records:
        groups.setdefault((rec.asn, rec.country, rec.is_ipv6), []).append(rec)

    out: list[IPRecord] = []
    for (asn, country, _v6), members in groups.items():
        nets = [m.network for m in members]
        try:
            collapsed = list(ipaddress.collapse_addresses(nets))
        except (TypeError, ValueError):
            out.extend(members)
            continue
        # Map each collapsed supernet back to a representative record.
        for supernet in collapsed:
            rep = next(
                (m for m in members if m.prefix == str(supernet)),
                None,
            )
            if rep is None:
                merged_source = "+".join(
                    sorted({s for m in members for s in m.source.split("+")})
                )
                rep = IPRecord(
                    prefix=str(supernet),
                    source=merged_source,
                    asn=asn,
                    country=country,
                    notes="collapsed prefix",
                )
                # Inherit org/provider from any contained member.
                for m in members:
                    if m.network.version == supernet.version and m.network.subnet_of(
                        supernet
                    ):
                        _merge(rep, m)
            out.append(rep)
    return sorted(out, key=_sort_key)
