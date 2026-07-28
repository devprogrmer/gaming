"""Bare IP-address list output.

A deliberately metadata-free format: every matched prefix is expanded to host
addresses and printed one per line, with no CIDR notation, ASN, organization,
country, header, or separator. It exists so the tool's output can be piped
straight into another program::

    gaming discover --country IR --exhaustive --format ip-list > ips.txt

Because it discards everything except the addresses, it is unsuitable for
auditing or debugging — use ``json``/``csv`` for that.
"""

from __future__ import annotations

from collections.abc import Iterable

from ..models import IPRecord

#: Default host-expansion bounds. Generous enough to be useful as a target list,
#: bounded so a ``/8`` cannot try to materialize 16M addresses.
DEFAULT_SAMPLE_PER_RANGE = 0  # 0 => every usable host in the range
DEFAULT_MAX_HOSTS = 65536


def to_ip_list(
    records: Iterable[IPRecord],
    *,
    sample_per_range: int = DEFAULT_SAMPLE_PER_RANGE,
    max_hosts: int = DEFAULT_MAX_HOSTS,
) -> str:
    """Expand ``records`` to bare host addresses, one per line.

    Reuses :func:`gaming.interactive.ranges.expand_hosts` for expansion and
    :func:`gaming.interactive.filters_shared.format_bare_ips` for rendering, so
    this format cannot drift from the interactive menu's bare-IP output.
    """
    # Imported lazily: reporting is imported by the CLI at startup, and the
    # interactive package pulls in settings/storage we do not need until asked.
    from ..interactive.filters_shared import format_bare_ips
    from ..interactive.ranges import expand_hosts

    prefixes = [r.prefix for r in records if getattr(r, "prefix", None)]
    hosts = expand_hosts(
        prefixes, sample_per_range=sample_per_range, max_hosts=max_hosts
    )
    return format_bare_ips(hosts)


def write_ip_list(records: Iterable[IPRecord], path, **kwargs) -> None:
    """Write the bare IP list to ``path`` (trailing newline included)."""
    text = to_ip_list(records, **kwargs)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
        if text:
            fh.write("\n")
