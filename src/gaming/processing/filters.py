"""Filtering of discovered records by country / ASN / provider / org, plus
the Iranian- and foreign-datacenter focus toggles.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

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

# Major foreign CDN / cloud / edge / WAF / global-platform providers. These are
# deliberately kept separate from ordinary datacenter/hosting networks: the
# datacenter-only scan path excludes them, and the foreign-CDN path includes
# exactly these. Substring match against org/provider text (lower-cased).
_FOREIGN_CDN_HINTS = (
    "cloudflare",
    "fastly",
    "akamai",
    "facebook",
    "meta platforms",
    "meta ",
    "google",
    "amazon",
    "aws",
    "cloudfront",
    "microsoft",
    "azure",
    "limelight",
    "edgecast",
    "stackpath",
    "cdn77",
    "bunny",
    "keycdn",
    "incapsula",
    "imperva",
    "sucuri",
    "gcore",
    "g-core",
    "lumen",
    "level3",
    "level 3",
    "verizon digital",
    "oracle",
)

# Iranian CDN / delivery / edge providers. Best-effort: allocation data for
# Iranian networks is often sparse, so this is combined with a country + CDN
# keyword heuristic in :func:`is_iranian_cdn`.
_IR_CDN_HINTS = (
    "arvan",
    "arvancloud",
    "abrarvan",
    "derak",
    "parspack",
    "faraso",
    "sabavision",
    "iran cdn",
    "irancdn",
    "cdn.ir",
)

# Country-code groupings used by region selection. Best-effort static mapping
# over the ISO-3166 alpha-2 codes carried on records; unmapped countries have
# no region and only match the "all" selection.
_REGIONS: dict[str, frozenset[str]] = {
    "middle_east": frozenset(
        {"IR", "IQ", "SA", "AE", "TR", "IL", "QA", "KW", "BH", "OM",
         "YE", "JO", "LB", "SY", "PS", "EG"}
    ),
    "europe": frozenset(
        {"DE", "FR", "GB", "NL", "IT", "ES", "SE", "CH", "PL", "RU",
         "RO", "FI", "NO", "DK", "IE", "BE", "AT", "CZ", "PT", "UA"}
    ),
    "asia": frozenset(
        {"CN", "JP", "KR", "IN", "SG", "HK", "TW", "TH", "VN", "MY",
         "ID", "PH", "PK", "BD", "KZ"}
    ),
}
REGION_CHOICES = ("middle_east", "europe", "asia", "all")


def _text(rec: IPRecord) -> str:
    return " ".join(x for x in (rec.organization, rec.provider) if x).lower()


def _is_datacenter(rec: IPRecord) -> bool:
    text = _text(rec)
    return any(k in text for k in _DATACENTER_KEYWORDS)


def is_datacenter(rec: IPRecord) -> bool:
    """Public predicate: True if the record's org/provider text looks like a
    datacenter/hosting/cloud network.

    Uses the same heuristic keywords as the ``--iran-datacenter`` /
    ``--foreign-datacenter`` focus modes, but without any country scoping, so
    callers can ask "is this any kind of datacenter?" directly.

    Note this keys off org/provider *text*: a record with no organization and no
    provider (e.g. RIR delegated-stats allocations, which only carry a country)
    has nothing to match and returns ``False``. That is "cannot classify", not
    "definitely not a datacenter" — use :func:`has_provider_metadata` to tell the
    two apart so unclassifiable records aren't silently dropped.
    """
    return _is_datacenter(rec)


def has_provider_metadata(rec: IPRecord) -> bool:
    """True if the record carries any org/provider text to classify against.

    The datacenter/CDN heuristics all match keywords against org/provider text.
    When both are absent (common for RIR-sourced records, which only carry a
    country), those predicates can only ever return ``False`` — not because the
    record isn't a datacenter, but because there is nothing to key off. Callers
    use this to surface such records as "unclassified" rather than dropping them.
    """
    return bool(_text(rec))


def is_foreign_cdn(rec: IPRecord) -> bool:
    """True if the record belongs to a major foreign CDN/cloud/edge/WAF provider.

    Matches org/provider text against :data:`_FOREIGN_CDN_HINTS` (Cloudflare,
    Fastly, Akamai, Meta/Facebook, Google, AWS, Azure, …). These are the large
    global-platform networks that the datacenter-only scan path excludes and the
    foreign-CDN scan path targets.
    """
    text = _text(rec)
    return any(h in text for h in _FOREIGN_CDN_HINTS)


def is_iranian_cdn(rec: IPRecord) -> bool:
    """True if the record looks like an Iranian CDN/delivery/edge provider.

    Best-effort, metadata-driven: a record qualifies if its org/provider text
    names a known Iranian CDN (:data:`_IR_CDN_HINTS`), or if it is an Iranian
    network (country ``IR`` or an Iranian provider hint) whose text also carries
    a generic CDN/edge keyword. Iranian allocation data is often sparse, so the
    country+keyword fallback widens coverage without hardcoding every provider.
    """
    text = _text(rec)
    if any(h in text for h in _IR_CDN_HINTS):
        return True
    is_iran_network = rec.country == "IR" or any(
        h in text for h in _IR_PROVIDER_HINTS
    )
    return is_iran_network and ("cdn" in text or "edge" in text)


def is_datacenter_only(rec: IPRecord) -> bool:
    """True if the record is an ordinary datacenter/hosting network **and not**
    any CDN/cloud/edge/WAF provider (foreign or Iranian).

    This is the predicate that gates the datacenter-only scan path: it keeps
    real hosting/colo/datacenter ranges while excluding Cloudflare, Fastly,
    Meta, Akamai, Google edge, ArvanCloud, and similar delivery/edge platforms
    so they can never leak into datacenter results. A generic ``cdn``/``edge``
    keyword in the org text also disqualifies a record from this path.
    """
    if is_foreign_cdn(rec) or is_iranian_cdn(rec):
        return False
    text = _text(rec)
    if "cdn" in text or "edge" in text:
        return False
    return _is_datacenter(rec)


def region_of(rec: IPRecord) -> str | None:
    """Return the region key for a record's country, or ``None`` if unmapped."""
    country = (rec.country or "").upper()
    if not country:
        return None
    for region, codes in _REGIONS.items():
        if country in codes:
            return region
    return None


def matches_region(rec: IPRecord, region: str) -> bool:
    """True if ``rec`` belongs to ``region`` (``"all"`` matches everything).

    Records with no known/mapped country never match a specific region, so a
    region selection acts as a positive location requirement.
    """
    if region == "all":
        return True
    return region_of(rec) == region


def classify_category(rec: IPRecord) -> str | None:
    """Classify a record into one of the four storage categories, or ``None``.

    Returns one of ``iran_cdn``, ``foreign_cdn``, ``iran_datacenter``,
    ``foreign_datacenter`` — or ``None`` when the record is neither a
    recognizable CDN nor a datacenter/hosting network.

    CDN classification wins over datacenter so a provider is never counted in
    both. Origin (Iran vs foreign) is decided by Iranian signals first
    (country ``IR`` or an Iranian provider hint), then by any non-IR country.
    """
    iranian = _is_iran(rec)
    if is_iranian_cdn(rec):
        return "iran_cdn"
    if is_foreign_cdn(rec):
        # A foreign-CDN hint on an Iranian network still means an edge/CDN
        # presence; classify by origin so it lands in the Iranian bucket.
        return "iran_cdn" if iranian else "foreign_cdn"
    if is_datacenter_only(rec):
        return "iran_datacenter" if iranian else "foreign_datacenter"
    return None


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
    return (
        _is_datacenter(rec)
        or rec.country == "IR"
        or any(h in _text(rec) for h in _IR_PROVIDER_HINTS)
    )


def _is_datacenter_or_known(rec: IPRecord) -> bool:
    return (
        _is_datacenter(rec)
        or any(h in _text(rec) for h in _FOREIGN_DC_HINTS)
        or bool(rec.country and rec.country != "IR")
    )


def apply_filters(records: Iterable[IPRecord], filters: Filters) -> list[IPRecord]:
    out = [r for r in records if matches(r, filters)]
    log.debug("filter kept %d records", len(out))
    return out


@dataclass(slots=True)
class LocationPartition:
    """Result of splitting records by verified country location.

    ``matched`` are records whose country field equals the requested code (the
    authoritative location signal). ``unverified`` are records with a missing or
    conflicting country — deliberately *not* included in a location-scoped
    result, but surfaced separately so uncertain data is never silently dropped
    nor silently misrepresented as a confident match.
    """

    matched: list[IPRecord] = field(default_factory=list)
    unverified: list[IPRecord] = field(default_factory=list)


def partition_by_country(
    records: Iterable[IPRecord], country: str
) -> LocationPartition:
    """Split ``records`` into those verified in ``country`` vs. everything else.

    The record's ISO country code is treated as the authoritative location
    signal: a record is only "matched" when its country equals ``country``. This
    is stricter than origin-by-ASN/org classification — an ASN registered as
    Iranian but whose actual prefix geolocates elsewhere (anycast edge, foreign
    PoP, misattributed WHOIS) carries a non-IR (or absent) country and lands in
    ``unverified`` rather than leaking into an Iran-only result.

    Records with no country are unverified too: for a location-scoped result the
    default is to *exclude* uncertain data, not to assume it belongs.
    """
    want = (country or "").strip().upper()
    matched: list[IPRecord] = []
    unverified: list[IPRecord] = []
    for rec in records:
        rc = (rec.country or "").upper()
        if rc and rc == want:
            matched.append(rec)
        else:
            unverified.append(rec)
    return LocationPartition(matched=matched, unverified=unverified)
