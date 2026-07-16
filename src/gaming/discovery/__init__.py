"""Discovery sources.

Every source implements the :class:`Source` interface defined in ``base``.
Sources are registered in :data:`REGISTRY` and looked up by name.
"""

from __future__ import annotations

from .base import Source, DiscoveryContext
from .rdap import RDAPSource
from .whois import WhoisSource
from .asn_bgp import ASNBGPSource
from .peeringdb import PeeringDBSource
from .rir import RIRSource

REGISTRY: dict[str, type[Source]] = {
    "rdap": RDAPSource,
    "whois": WhoisSource,
    "asn_bgp": ASNBGPSource,
    "peeringdb": PeeringDBSource,
    "rir": RIRSource,
}


def available_sources() -> list[str]:
    return sorted(REGISTRY)


def build_source(name: str, context: DiscoveryContext) -> Source:
    try:
        cls = REGISTRY[name]
    except KeyError as exc:
        raise KeyError(f"unknown discovery source: {name!r}") from exc
    return cls(context)


__all__ = [
    "Source",
    "DiscoveryContext",
    "REGISTRY",
    "available_sources",
    "build_source",
]
