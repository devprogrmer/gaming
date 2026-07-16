"""Loading, parsing, sampling, and editing of IP ranges.

Two scopes are supported:

    "iran"     bundled Iranian ranges  + user custom ranges tagged iran
    "foreign"  bundled foreign ranges  + user custom ranges tagged foreign

Bundled ranges ship as data files inside the package (``data/*_ranges.txt``).
User-added ranges are appended to ``custom_ranges.txt`` under the app home so
they persist between runs and survive reinstalls of the package.

Large CIDRs are expanded into a bounded, evenly-spaced sample of host
addresses so scans stay responsive regardless of prefix size.
"""

from __future__ import annotations

import ipaddress
from importlib import resources
from pathlib import Path

from . import paths

SCOPES = ("iran", "foreign")

_BUNDLED = {
    "iran": "iran_ranges.txt",
    "foreign": "foreign_ranges.txt",
}


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


def _read_custom() -> dict[str, list[str]]:
    """Read the custom ranges file into ``{scope: [cidr, ...]}``.

    The file stores one entry per line as ``scope,cidr``. Unknown scopes and
    malformed lines are ignored.
    """
    out: dict[str, list[str]] = {scope: [] for scope in SCOPES}
    path = _custom_file()
    if not path.exists():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        scope, _, rest = line.partition(",")
        scope = scope.strip().lower()
        cidr = _parse_line(rest)
        if scope in out and cidr:
            out[scope].append(cidr)
    return out


def _write_custom(data: dict[str, list[str]]) -> None:
    lines = ["# User custom ranges — one 'scope,cidr' entry per line."]
    for scope in SCOPES:
        for cidr in data.get(scope, []):
            lines.append(f"{scope},{cidr}")
    _custom_file().write_text("\n".join(lines) + "\n", encoding="utf-8")


def load_ranges(scope: str) -> list[str]:
    """Return the merged, de-duplicated CIDR list for a scope."""
    scope = scope.lower()
    if scope not in SCOPES:
        raise ValueError(f"unknown scope: {scope!r} (expected one of {SCOPES})")
    merged: list[str] = []
    seen: set[str] = set()
    for cidr in _read_bundled(scope) + _read_custom().get(scope, []):
        if cidr not in seen:
            seen.add(cidr)
            merged.append(cidr)
    return merged


def custom_ranges(scope: str) -> list[str]:
    """Return only the user-added ranges for a scope."""
    return list(_read_custom().get(scope.lower(), []))


def add_custom_range(scope: str, cidr: str) -> str:
    """Validate and persist a custom range. Returns the normalized CIDR.

    Raises ``ValueError`` on an invalid CIDR or unknown scope.
    """
    scope = scope.lower()
    if scope not in SCOPES:
        raise ValueError(f"unknown scope: {scope!r}")
    normalized = _parse_line(cidr)
    if normalized is None:
        raise ValueError(f"invalid CIDR: {cidr!r}")
    data = _read_custom()
    if normalized not in data[scope]:
        data[scope].append(normalized)
        _write_custom(data)
    return normalized


def remove_custom_range(scope: str, cidr: str) -> bool:
    """Remove a custom range. Returns True if something was removed."""
    scope = scope.lower()
    normalized = _parse_line(cidr) or cidr.strip()
    data = _read_custom()
    if scope in data and normalized in data[scope]:
        data[scope].remove(normalized)
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

