"""Bundled provider seed data for broad, deterministic discovery.

Live discovery (RDAP/WHOIS/BGP/PeeringDB/RIR) needs seed ASNs and network
access, and offline it yields only a handful of sample records. To make a
"discover everything" pass actually aggregate CIDRs across many datacenter,
hosting, cloud, and CDN providers — for both Iran and foreign origins — we ship
a curated seed file (``data/providers.toml``) that maps well-known providers to
representative CIDRs tagged with their storage category.

:func:`load_seed_records` turns that file into :class:`~gaming.models.IPRecord`
objects carrying enough org/provider/country metadata to classify with
:func:`gaming.processing.filters.classify_category`. The list is a maintainable
snapshot, not an exhaustive real-time BGP dump; live discovery augments it.
"""

from __future__ import annotations

import ipaddress
import tomllib
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

from ..logging_setup import get_logger
from ..models import IPRecord

log = get_logger("gaming.interactive.providers")

_SEED_FILE = "providers.toml"

#: Which seed categories belong to each origin, so the provider picker can list
#: "Iran" vs "Foreign" providers straight from the seed file's ``category``.
_ORIGIN_CATEGORIES = {
    "iran": ("iran_datacenter", "iran_cdn"),
    "foreign": ("foreign_datacenter", "foreign_cdn"),
}


@dataclass(slots=True)
class Provider:
    """A single seed provider: its identity plus the CIDRs bundled for it."""

    name: str
    category: str
    country: str | None = None
    asns: list[str] = field(default_factory=list)
    cidrs: list[str] = field(default_factory=list)

    @property
    def origin(self) -> str:
        """``"iran"`` or ``"foreign"``, derived from the storage category."""
        return "iran" if self.category.startswith("iran") else "foreign"

    def to_records(self) -> list[IPRecord]:
        """Turn this provider's CIDRs into classified :class:`IPRecord` objects."""
        provider_text = self.name.lower()
        asn = self.asns[0] if self.asns else None
        records: list[IPRecord] = []
        for cidr in self.cidrs:
            try:
                records.append(
                    IPRecord(
                        prefix=str(cidr),
                        source="seed",
                        asn=asn,
                        organization=self.name,
                        country=self.country,
                        provider=provider_text,
                        notes=f"seed:{self.category}",
                    )
                )
            except ValueError:
                # Skip anything that is not a valid prefix.
                continue
        return records


def _read_seed_toml() -> dict:
    try:
        raw = (
            resources.files("gaming.interactive.data")
            .joinpath(_SEED_FILE)
            .read_bytes()
        )
    except (FileNotFoundError, ModuleNotFoundError, OSError) as exc:
        log.debug("provider seed file unavailable: %s", exc)
        return {}
    try:
        return tomllib.loads(raw.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        log.warning("provider seed file is malformed: %s", exc)
        return {}


def _seed_file_path() -> Path | None:
    """Filesystem path of the bundled seed file, or None if not on disk.

    ``importlib.resources`` may serve the file from a zip; for the in-place
    ``[meta]`` update we need a real, writable path, so this returns ``None``
    when the resource isn't backed by a plain file (in which case the update is
    skipped rather than failing).
    """
    try:
        res = resources.files("gaming.interactive.data").joinpath(_SEED_FILE)
        path = Path(str(res))
        return path if path.is_file() else None
    except (FileNotFoundError, ModuleNotFoundError, OSError, TypeError):
        return None


def seed_last_validated() -> str:
    """Return the seed file's ``[meta].last_validated`` marker ("" if unset)."""
    meta = _read_seed_toml().get("meta") or {}
    value = meta.get("last_validated") if isinstance(meta, dict) else None
    return str(value) if value else ""


def update_last_validated(date_str: str) -> bool:
    """Rewrite only the ``[meta].last_validated`` line in the seed file.

    Never touches any ``[[provider]]`` entry: it does a line-oriented rewrite of
    the single ``last_validated = "..."`` assignment (or inserts a ``[meta]``
    block if somehow absent). Returns True on success, False when the file isn't
    a writable on-disk file. Fail-soft — an I/O error is logged and returns
    False rather than raising.
    """
    path = _seed_file_path()
    if path is None:
        log.debug("seed file not on a writable path; skipping meta update")
        return False
    try:
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        new_assignment = f'last_validated = "{date_str}"\n'
        in_meta = False
        replaced = False
        out: list[str] = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("[") and stripped.endswith("]"):
                in_meta = stripped == "[meta]"
            if (
                in_meta
                and not replaced
                and stripped.replace(" ", "").startswith("last_validated=")
            ):
                out.append(new_assignment)
                replaced = True
                continue
            out.append(line)
        if not replaced:
            # No existing marker — prepend a [meta] block without disturbing the
            # rest of the file.
            out = [f"[meta]\n{new_assignment}\n", *out]
        path.write_text("".join(out), encoding="utf-8")
        return True
    except OSError as exc:
        log.warning("could not update seed last_validated: %s", exc)
        return False


def load_providers() -> list[Provider]:
    """Return every bundled seed provider as a :class:`Provider`.

    Malformed entries (missing name or a non-list ``cidrs``) are skipped rather
    than raising, so one bad seed row never breaks the picker.
    """
    data = _read_seed_toml()
    entries = data.get("provider") or []
    providers: list[Provider] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        cidrs = entry.get("cidrs") or []
        if not name or not isinstance(cidrs, list):
            continue
        asns = entry.get("asns") or []
        providers.append(
            Provider(
                name=str(name),
                category=str(entry.get("category") or ""),
                country=entry.get("country") or None,
                asns=[str(a) for a in asns if str(a).strip()],
                cidrs=[str(c) for c in cidrs],
            )
        )
    return providers


def providers_for_origin(origin: str) -> list[Provider]:
    """Return the seed providers for an origin (``"iran"`` / ``"foreign"``).

    Ordering is stable (seed-file order), so the numbered picker the menu builds
    on top of this stays predictable between runs.
    """
    wanted = _ORIGIN_CATEGORIES.get(origin.lower())
    if wanted is None:
        raise ValueError(f"unknown origin: {origin!r} (expected iran/foreign)")
    return [p for p in load_providers() if p.category in wanted]


def load_seed_records() -> list[IPRecord]:
    """Return one :class:`IPRecord` per seed CIDR across all bundled providers.

    Each record carries the provider name as ``organization``/``provider`` and
    the provider's country, so downstream classification lands it in the right
    category. Malformed entries are skipped rather than raising.
    """
    records: list[IPRecord] = []
    for provider in load_providers():
        records.extend(provider.to_records())
    return records


@dataclass(slots=True)
class SeedCheck:
    """One provider's seed-freshness result from :func:`refresh_seed_data`.

    ``stale`` lists the bundled CIDRs that are no longer covered by any
    currently-announced prefix for the provider's ASNs. ``checked`` is False
    when the provider had no ASNs or the live lookup returned nothing (so
    ``stale`` is not meaningful and nothing should be acted on).
    """

    name: str
    category: str
    checked: bool = False
    announced: int = 0
    stale: list[str] = field(default_factory=list)


def refresh_seed_data(*, timeout: float = 15.0) -> list[SeedCheck]:
    """Re-validate bundled seed CIDRs against currently-announced prefixes.

    For each provider with seed ASNs, fetch its announced prefixes live (reusing
    the existing :class:`~gaming.discovery.asn_bgp.ASNBGPSource`) and flag any
    bundled CIDR not contained in an announced prefix as *stale*. This never
    deletes or rewrites the seed file — it only reports, so a transient lookup
    failure or a legitimately-repartitioned block can be reviewed by hand.

    Fully fail-soft: a lookup error for one provider is logged and skipped
    (``checked=False``) without affecting the others. Returns one
    :class:`SeedCheck` per provider.
    """
    from ..discovery.asn_bgp import ASNBGPSource
    from ..discovery.base import DiscoveryContext
    from ..models import Filters, normalize_asn

    results: list[SeedCheck] = []
    for provider in load_providers():
        check = SeedCheck(name=provider.name, category=provider.category)
        if not provider.asns:
            results.append(check)
            continue
        try:
            asns = [normalize_asn(a) for a in provider.asns]
            source = ASNBGPSource(
                DiscoveryContext(filters=Filters(asns=asns), timeout=timeout)
            )
            announced_recs = source.discover()
            networks = _to_networks(r.prefix for r in announced_recs)
        except Exception as exc:  # noqa: BLE001 - refresh must never crash
            log.warning(
                "seed refresh for %s failed: %s: %s",
                provider.name,
                type(exc).__name__,
                exc,
            )
            results.append(check)
            continue

        if not networks:
            log.debug(
                "seed refresh for %s: no announced prefixes returned", provider.name
            )
            results.append(check)
            continue

        check.checked = True
        check.announced = len(networks)
        for cidr in provider.cidrs:
            if not _covered(cidr, networks):
                check.stale.append(cidr)
                log.warning(
                    "seed CIDR %s for %s looks stale (not in any announced prefix)",
                    cidr,
                    provider.name,
                )
        results.append(check)
    return results


def _to_networks(prefixes) -> list[ipaddress._BaseNetwork]:
    nets: list[ipaddress._BaseNetwork] = []
    for prefix in prefixes:
        try:
            nets.append(ipaddress.ip_network(str(prefix), strict=False))
        except ValueError:
            continue
    return nets


def _covered(cidr: str, networks: list[ipaddress._BaseNetwork]) -> bool:
    """True if ``cidr`` is a subnet of (or equal to) any announced network."""
    try:
        seed = ipaddress.ip_network(cidr, strict=False)
    except ValueError:
        return True  # unparseable seed CIDR is a separate problem; don't flag here
    for net in networks:
        if seed.version != net.version:
            continue
        if seed.subnet_of(net) or seed == net:
            return True
    return False


@dataclass(slots=True)
class ValidationReport:
    """Outcome of :func:`validate_seed_data`: the per-provider checks + marker."""

    checks: list[SeedCheck]
    last_validated: str  # the date written to (or attempted for) the [meta] marker
    marker_updated: bool  # whether the seed file's [meta] marker was rewritten


def validate_seed_data(
    *, timeout: float = 15.0, update_marker: bool = True
) -> ValidationReport:
    """Validate every seed provider and (optionally) stamp ``last_validated``.

    Runs :func:`refresh_seed_data` (which reports, never deletes) and then, when
    ``update_marker`` is set and at least one provider was actually checked,
    writes today's date into the seed file's ``[meta].last_validated`` marker via
    :func:`update_last_validated`. Provider entries are never modified. The date
    is computed here (normal runtime code) as an ISO ``YYYY-MM-DD`` string.
    """
    from datetime import UTC, datetime

    checks = refresh_seed_data(timeout=timeout)
    today = datetime.now(UTC).date().isoformat()
    marker_updated = False
    # Only stamp the marker if we genuinely reached the network for at least one
    # provider; an all-offline run shouldn't claim a fresh validation date.
    if update_marker and any(c.checked for c in checks):
        marker_updated = update_last_validated(today)
    return ValidationReport(
        checks=checks, last_validated=today, marker_updated=marker_updated
    )

