"""Loading, parsing, sampling, and editing of IP ranges.

Two *scopes* ship bundled ranges:

    "iran"     bundled Iranian ranges  + user custom ranges tagged iran
    "foreign"  bundled foreign ranges  + user custom ranges tagged foreign

On top of those, user/discovered ranges are organized into four **categories**
that separate datacenter from CDN/cloud, for both origins::

    iran_datacenter    iran_cdn    foreign_datacenter    foreign_cdn

Bundled ranges ship as data files inside the package (``data/*_ranges.txt``).
User-added and auto-discovered ranges are appended to ``custom_ranges.txt``
under the app home so they persist between runs and survive reinstalls of the
package. Each stored line is ``group,cidr,origin[,country,provider]`` where
``group`` is a scope or a category and ``origin`` is ``custom`` or
``discovered``. The trailing ``country``/``provider`` carry the metadata needed
for latency-by-country reporting. Legacy two-field ``group,cidr`` lines still
parse (origin defaults to ``custom``, metadata empty).

Large CIDRs are expanded into a bounded, evenly-spaced sample of host
addresses so scans stay responsive regardless of prefix size.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Iterable
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from . import paths

SCOPES = ("iran", "foreign")

#: The four category keys that separate datacenter from CDN/cloud per origin.
CATEGORIES = (
    "iran_datacenter",
    "iran_cdn",
    "foreign_datacenter",
    "foreign_cdn",
)

#: Which categories make up each legacy scope, so scanning ``iran`` /
#: ``foreign`` keeps working after the split into categories.
_SCOPE_CATEGORIES = {
    "iran": ("iran_datacenter", "iran_cdn"),
    "foreign": ("foreign_datacenter", "foreign_cdn"),
}

#: Every group name that may appear in the custom-ranges file.
_GROUPS = (*SCOPES, *CATEGORIES)

#: Valid origin markers for a stored custom/discovered entry.
_ORIGINS = ("custom", "discovered")

_BUNDLED = {
    "iran": "iran_ranges.txt",
    "foreign": "foreign_ranges.txt",
}


@dataclass(slots=True)
class RangeEntry:
    """A stored custom/discovered range with its origin and metadata."""

    cidr: str
    origin: str = "custom"
    country: str | None = None
    provider: str | None = None



def _parse_line(line: str) -> str | None:
    """Return the CIDR token from a data line, or None for blanks/comments."""
    text = line.strip()
    if not text or text.startswith("#"):
        return None
    # Allow an inline "# label" after the CIDR.
    token = text.split("#", 1)[0].strip()
    if not token:
        return None
    try:
        net = ipaddress.ip_network(token, strict=False)
    except ValueError:
        return None
    return str(net)


def _read_bundled(scope: str) -> list[str]:
    filename = _BUNDLED[scope]
    try:
        text = (
            resources.files("gaming.interactive.data")
            .joinpath(filename)
            .read_text(encoding="utf-8")
        )
    except (FileNotFoundError, ModuleNotFoundError, OSError):
        return []
    return [cidr for line in text.splitlines() if (cidr := _parse_line(line))]


def _custom_file() -> Path:
    return paths.custom_ranges_path()


def _read_custom() -> dict[str, list[RangeEntry]]:
    """Read the custom ranges file into ``{group: [RangeEntry, ...]}``.

    Each line is ``group,cidr[,origin[,country[,provider]]]``. ``group`` is a
    scope or category; ``origin`` defaults to ``custom`` when absent so legacy
    two-field files still load. Unknown groups and malformed lines are ignored.
    """
    out: dict[str, list[RangeEntry]] = {group: [] for group in _GROUPS}
    path = _custom_file()
    if not path.exists():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split(",")]
        group = parts[0].lower()
        cidr = _parse_line(parts[1]) if len(parts) > 1 else None
        origin = parts[2].lower() if len(parts) > 2 and parts[2] else "custom"
        if origin not in _ORIGINS:
            origin = "custom"
        country = parts[3].upper() if len(parts) > 3 and parts[3] else None
        provider = parts[4] if len(parts) > 4 and parts[4] else None
        if group in out and cidr:
            out[group].append(RangeEntry(cidr, origin, country, provider))
    return out


def _write_custom(data: dict[str, list[RangeEntry]]) -> None:
    lines = [
        "# User custom/discovered ranges — "
        "'group,cidr,origin,country,provider' per line."
    ]
    for group in _GROUPS:
        for e in data.get(group, []):
            lines.append(
                f"{group},{e.cidr},{e.origin},{e.country or ''},{e.provider or ''}"
            )
    _custom_file().write_text("\n".join(lines) + "\n", encoding="utf-8")


def _validate_group(group: str) -> str:
    group = group.lower()
    if group not in _GROUPS:
        raise ValueError(
            f"unknown group: {group!r} (expected a scope {SCOPES} or "
            f"category {CATEGORIES})"
        )
    return group


def load_ranges(scope: str) -> list[str]:
    """Return the merged, de-duplicated CIDR list for a scope.

    Combines bundled ranges with any custom/discovered entries stored under the
    scope *or* its member categories, so ``load_ranges("iran")`` includes
    ``iran_datacenter`` and ``iran_cdn`` too.
    """
    scope = scope.lower()
    if scope not in SCOPES:
        raise ValueError(f"unknown scope: {scope!r} (expected one of {SCOPES})")
    custom = _read_custom()
    stored: list[str] = [e.cidr for e in custom.get(scope, [])]
    for category in _SCOPE_CATEGORIES[scope]:
        stored.extend(e.cidr for e in custom.get(category, []))

    merged: list[str] = []
    seen: set[str] = set()
    for cidr in _read_bundled(scope) + stored:
        if cidr not in seen:
            seen.add(cidr)
            merged.append(cidr)
    return merged


def load_scope_group(scope: str) -> list[str]:
    """Alias for :func:`load_ranges` — the union of a scope's categories."""
    return load_ranges(scope)


def load_category(category: str) -> list[str]:
    """Return the de-duplicated stored CIDRs for a single category."""
    return [e.cidr for e in category_entries(category)]


def category_entries(category: str) -> list[RangeEntry]:
    """Return the stored :class:`RangeEntry` items for a category (deduped)."""
    category = category.lower()
    if category not in CATEGORIES:
        raise ValueError(f"unknown category: {category!r} (expected {CATEGORIES})")
    out: list[RangeEntry] = []
    seen: set[str] = set()
    for entry in _read_custom().get(category, []):
        if entry.cidr not in seen:
            seen.add(entry.cidr)
            out.append(entry)
    return out


def custom_ranges(group: str) -> list[str]:
    """Return only the user-added/discovered CIDRs for a scope or category."""
    group = group.lower()
    return [e.cidr for e in _read_custom().get(group, [])]


def save_discovered(
    category: str,
    cidrs: Iterable[str] | Iterable[RangeEntry],
    *,
    metadata: dict[str, tuple[str | None, str | None]] | None = None,
) -> int:
    """Persist auto-discovered CIDRs under a category. Returns count newly added.

    ``cidrs`` may be plain CIDR strings or :class:`RangeEntry` objects. Optional
    ``metadata`` maps a CIDR to ``(country, provider)`` used when a plain string
    is given. CIDRs already stored under the category (custom *or* discovered)
    are skipped, so repeated discovery never duplicates. Invalid CIDRs are
    ignored rather than raising.
    """
    category = _validate_group(category)
    if category not in CATEGORIES:
        raise ValueError(f"save_discovered expects a category, got {category!r}")
    metadata = metadata or {}
    data = _read_custom()
    existing = {e.cidr for e in data[category]}
    added = 0
    for raw in cidrs:
        if isinstance(raw, RangeEntry):
            normalized = _parse_line(raw.cidr)
            country, provider = raw.country, raw.provider
        else:
            normalized = _parse_line(str(raw))
            country, provider = metadata.get(str(raw), (None, None))
        if normalized is None or normalized in existing:
            continue
        if normalized in metadata:  # normalized form may differ from the key
            country, provider = metadata[normalized]
        existing.add(normalized)
        data[category].append(
            RangeEntry(normalized, "discovered", country, provider)
        )
        added += 1
    if added:
        _write_custom(data)
    return added


def persist_records(records: Iterable[object]) -> dict[str, int]:
    """Classify discovered records and auto-save their CIDRs by category.

    Each record is bucketed with
    :func:`gaming.processing.filters.classify_category`; unclassifiable records
    are skipped. Country/provider metadata is carried through so later scans can
    report latency by country. Returns a ``{category: newly_added_count}``
    mapping (only categories that gained entries appear). This is the
    persistence step that turns an in-memory discovery pass into durable,
    scan-later managed ranges.
    """
    # Imported here to avoid a package import cycle (processing -> interactive).
    from ..processing.filters import classify_category

    grouped: dict[str, list[RangeEntry]] = {}
    for rec in records:
        category = classify_category(rec)
        prefix = getattr(rec, "prefix", None)
        if category is None or not prefix:
            continue
        country = getattr(rec, "country", None)
        provider = getattr(rec, "provider", None) or getattr(
            rec, "organization", None
        )
        grouped.setdefault(category, []).append(
            RangeEntry(prefix, "discovered", country, provider)
        )

    added: dict[str, int] = {}
    for category, entries in grouped.items():
        count = save_discovered(category, entries)
        if count:
            added[category] = count
    return added


def add_custom_range(
    group: str,
    cidr: str,
    *,
    country: str | None = None,
    provider: str | None = None,
) -> str:
    """Validate and persist a custom range. Returns the normalized CIDR.

    ``group`` may be a legacy scope (``iran``/``foreign``) or one of the four
    categories. Raises ``ValueError`` on an invalid CIDR or unknown group.
    """
    group = _validate_group(group)
    normalized = _parse_line(cidr)
    if normalized is None:
        raise ValueError(f"invalid CIDR: {cidr!r}")
    data = _read_custom()
    if normalized not in {e.cidr for e in data[group]}:
        data[group].append(
            RangeEntry(normalized, "custom", country, provider)
        )
        _write_custom(data)
    return normalized


def remove_custom_range(group: str, cidr: str) -> bool:
    """Remove a custom/discovered range. Returns True if something was removed."""
    group = group.lower()
    normalized = _parse_line(cidr) or cidr.strip()
    data = _read_custom()
    entries = data.get(group)
    if not entries:
        return False
    kept = [e for e in entries if e.cidr != normalized]
    if len(kept) != len(entries):
        data[group] = kept
        _write_custom(data)
        return True
    return False


def expand_hosts(
    cidrs: list[str],
    *,
    sample_per_range: int = 16,
    max_hosts: int = 512,
) -> list[str]:
    """Expand CIDRs into a bounded, evenly-spaced list of host addresses.

    * ``sample_per_range`` caps how many hosts are taken from each CIDR
      (0 means "all", still subject to ``max_hosts``). Hosts are sampled at a
      uniform stride so the sample spans the whole range.
    * ``max_hosts`` is a global safety cap across all ranges combined.

    Addresses are computed by index arithmetic rather than materializing the
    range, so even very large IPv4/IPv6 prefixes are handled cheaply.
    """
    hosts: list[str] = []
    seen: set[str] = set()

    for cidr in cidrs:
        try:
            net = ipaddress.ip_network(cidr, strict=False)
        except ValueError:
            continue

        for addr in _sample_addresses(net, sample_per_range):
            key = str(addr)
            if key not in seen:
                seen.add(key)
                hosts.append(key)
            if len(hosts) >= max_hosts:
                return hosts

    return hosts


def _usable_count(net: ipaddress._BaseNetwork) -> int:
    """Number of usable host addresses in a network (never negative)."""
    total = net.num_addresses
    if total <= 1:
        return total  # a /32 or /128 host route
    # IPv4 blocks larger than /31 reserve network + broadcast; net.hosts()
    # excludes both. IPv6 has no broadcast, so only exclude the base address
    # for the same-shaped iteration ipaddress uses.
    if net.version == 4 and net.prefixlen < 31:
        return total - 2
    return total - 1


def _nth_host(net: ipaddress._BaseNetwork, index: int) -> ipaddress._BaseAddress:
    """Return the ``index``-th usable host address without materializing hosts."""
    if net.num_addresses == 1:
        return net.network_address
    base = int(net.network_address)
    # net.hosts() starts at network_address + 1 for blocks that reserve the
    # base address; align our offset accordingly.
    if net.version == 4 and net.prefixlen < 31:
        offset = 1
    elif net.version == 6 and net.prefixlen < 127:
        offset = 1
    else:
        offset = 0
    addr_int = base + offset + index
    return ipaddress.ip_address(addr_int)


def _sample_addresses(net: ipaddress._BaseNetwork, count: int) -> list:
    """Evenly-spaced usable host addresses from a network (0 => all, capped)."""
    usable = _usable_count(net)
    if usable <= 0:
        return [net.network_address]
    if count <= 0 or count >= usable:
        # Take all usable hosts, but guard against pathologically huge ranges.
        take = min(usable, 65536)
        return [_nth_host(net, i) for i in range(take)]
    step = usable / count
    return [_nth_host(net, int(i * step)) for i in range(count)]

