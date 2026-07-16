"""Filtering of discovered records by country / ASN / provider / org, plus
the Iranian- and foreign-datacenter focus toggles.
"""

from __future__ import annotations

from typing import Iterable

from ..logging_setup import get_logger
from ..models import Filters, IPRecord

log = get_logger("gaming.filters")

# Heuristic keywords used to classify datacenter/hosting organizations.
_DATACENTER_KEYWORDS = (
    "datacenter",
    "data center",
    "hosting",
    "host",
    "server",
    "cloud",
    "colocation",
    "colo",
    "vps",
    "cdn",
    "idc",
    "telecom",
    "network",
)

# A small, extensible set of well-known providers used by the focus toggles.
_IR_PROVIDER_HINTS = (
    "arvan",
    "arvancloud",
    "pars",
    "asiatech",
    "irancell",
    "tci",
    "tic",
    "respina",
    "shatel",
    "afranet",
    "datak",
    "iran",
)
_FOREIGN_DC_HINTS = (
    "cloudflare",
    "hetzner",
    "ovh",
    "digitalocean",
    "linode",
    "akamai",
    "fastly",
    "amazon",
    "aws",
    "google",
    "microsoft",
    "azure",
    "leaseweb",
    "contabo",
    "vultr",
    "alexhost",
    "global layer",
)


def _text(rec: IPRecord) -> str:
    return " ".join(
        x for x in (rec.organization, rec.provider) if x
    ).lower()


def _is_datacenter(rec: IPRecord) -> bool:
    text = _text(rec)
    return any(k in text for k in _DATACENTER_KEYWORDS)


def _is_iran(rec: IPRecord) -> bool:
    if rec.country == "IR":
        return True
    text = _text(rec)
    return any(h in text for h in _IR_PROVIDER_HINTS)


def _is_foreign(rec: IPRecord) -> bool:
    if rec.country and rec.country != "IR":
        return True
    text = _text(rec)
    return any(h in text for h in _FOREIGN_DC_HINTS)


def matches(rec: IPRecord, filters: Filters) -> bool:
    """Return True if ``rec`` passes all active filter criteria (AND logic)."""
    if filters.is_empty():
        return True

    if filters.countries and (rec.country or "") not in filters.countries:
        return False

    if filters.asns and (rec.asn or "") not in filters.asns:
        return False

    if filters.providers:
        prov = (rec.provider or "").lower()
        org = (rec.organization or "").lower()
        if not any(p in prov or p in org for p in filters.providers):
            return False

    if filters.organizations:
        org = (rec.organization or "").lower()
        if not any(o in org for o in filters.organizations):
            return False

    if filters.iran_datacenter and not (_is_iran(rec) and _is_datacenter_or_ir(rec)):
        return False

    if filters.foreign_datacenter and not (
        _is_foreign(rec) and _is_datacenter_or_known(rec)
    ):
        return False

    return True


def _is_datacenter_or_ir(rec: IPRecord) -> bool:
    # For the Iran focus, treat any IR-country record or datacenter-ish org as
    # a datacenter candidate (allocation data often lacks org text).
    return _is_datacenter(rec) or rec.country == "IR" or any(
        h in _text(rec) for h in _IR_PROVIDER_HINTS
    )


def _is_datacenter_or_known(rec: IPRecord) -> bool:
    return _is_datacenter(rec) or any(h in _text(rec) for h in _FOREIGN_DC_HINTS) or bool(
        rec.country and rec.country != "IR"
    )


def apply_filters(records: Iterable[IPRecord], filters: Filters) -> list[IPRecord]:
    out = [r for r in records if matches(r, filters)]
    log.debug("filter kept %d records", len(out))
    return out
