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

import tomllib
from importlib import resources

from ..logging_setup import get_logger
from ..models import IPRecord

log = get_logger("gaming.interactive.providers")

_SEED_FILE = "providers.toml"


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


def load_seed_records() -> list[IPRecord]:
    """Return one :class:`IPRecord` per seed CIDR across all bundled providers.

    Each record carries the provider name as ``organization``/``provider`` and
    the provider's country, so downstream classification lands it in the right
    category. Malformed entries are skipped rather than raising.
    """
    data = _read_seed_toml()
    providers = data.get("provider") or []
    records: list[IPRecord] = []
    for entry in providers:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        cidrs = entry.get("cidrs") or []
        if not name or not isinstance(cidrs, list):
            continue
        country = entry.get("country") or None
        asns = entry.get("asns") or []
        asn = asns[0] if asns else None
        category = entry.get("category") or ""
        provider_text = name.lower()
        for cidr in cidrs:
            try:
                records.append(
                    IPRecord(
                        prefix=str(cidr),
                        source="seed",
                        asn=asn,
                        organization=name,
                        country=country,
                        provider=provider_text,
                        notes=f"seed:{category}",
                    )
                )
            except ValueError:
                # Skip anything that is not a valid prefix.
                continue
    return records
