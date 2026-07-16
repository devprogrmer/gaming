"""Canonical data models for the gaming tool."""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field, asdict
from typing import Any, Iterable, Optional


@dataclass(slots=True)
class IPRecord:
    """A single discovered/analyzed network record.

    This is the canonical result row that flows through the whole pipeline
    and is emitted by every reporter.
    """

    prefix: str
    source: str = "unknown"
    asn: Optional[str] = None
    organization: Optional[str] = None
    country: Optional[str] = None
    provider: Optional[str] = None
    # Reachability results (populated later in the pipeline).
    alive: Optional[bool] = None
    global_reachable: Optional[bool] = None
    open_ports: list[int] = field(default_factory=list)
    notes: str = ""

    def __post_init__(self) -> None:
        # Validate/normalize the prefix as early as possible.
        self.prefix = normalize_prefix(self.prefix)
        if self.asn is not None:
            self.asn = normalize_asn(self.asn)
        if self.country is not None:
            self.country = self.country.strip().upper() or None

    @property
    def network(self) -> ipaddress._BaseNetwork:
        return ipaddress.ip_network(self.prefix, strict=False)

    @property
    def is_ipv6(self) -> bool:
        return self.network.version == 6

    def sample_host(self) -> str:
        """Return a representative host address for reachability probing."""
        net = self.network
        if net.num_addresses == 1:
            return str(net.network_address)
        # For a usable range, the first usable host is a reasonable probe.
        try:
            return str(next(net.hosts()))
        except StopIteration:
            return str(net.network_address)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["open_ports"] = list(self.open_ports)
        return d

    # Ordered field list used by CSV export and console tables.
    FIELDS = (
        "source",
        "asn",
        "organization",
        "country",
        "provider",
        "prefix",
        "alive",
        "global_reachable",
        "open_ports",
        "notes",
    )


@dataclass(slots=True)
class Filters:
    """Filtering criteria applied to discovered records."""

    countries: list[str] = field(default_factory=list)
    asns: list[str] = field(default_factory=list)
    providers: list[str] = field(default_factory=list)
    organizations: list[str] = field(default_factory=list)
    # Focus toggles.
    iran_datacenter: bool = False
    foreign_datacenter: bool = False

    def __post_init__(self) -> None:
        self.countries = [c.strip().upper() for c in self.countries if c and c.strip()]
        self.asns = [normalize_asn(a) for a in self.asns if str(a).strip()]
        self.providers = [p.strip().lower() for p in self.providers if p and p.strip()]
        self.organizations = [
            o.strip().lower() for o in self.organizations if o and o.strip()
        ]

    def is_empty(self) -> bool:
        return not any(
            [
                self.countries,
                self.asns,
                self.providers,
                self.organizations,
                self.iran_datacenter,
                self.foreign_datacenter,
            ]
        )


def normalize_asn(value: Any) -> str:
    """Normalize an ASN to the canonical ``AS<number>`` form."""
    s = str(value).strip().upper()
    if not s:
        return ""
    if s.startswith("AS"):
        s = s[2:]
    s = s.strip()
    if not s.isdigit():
        # Leave non-numeric values untouched but tagged so filters can match.
        return f"AS{s}" if not s.startswith("AS") else s
    return f"AS{int(s)}"


def normalize_prefix(value: str) -> str:
    """Validate and normalize a CIDR prefix or single IP into CIDR form."""
    s = str(value).strip()
    if not s:
        raise ValueError("empty prefix")
    # Accept a bare IP by turning it into a host prefix.
    net = ipaddress.ip_network(s, strict=False)
    return str(net)


def records_to_dicts(records: Iterable[IPRecord]) -> list[dict[str, Any]]:
    return [r.to_dict() for r in records]
