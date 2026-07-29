"""On-demand provider lookup by organization name.

The rest of :mod:`gaming.discovery` is keyed by ASN or country: every source
needs a seed to query, and a provider *name* was only ever used afterwards as a
lowercase substring filter over whatever those seeds happened to return. That is
why asking for a real company absent from the bundled seed file produced nothing
at all rather than an answer — there was no path from a name to a registry.

This module is that path. It queries RDAP directly by organization name and
returns the networks the registries actually hold, whether or not the provider
has ever been heard of locally.

Two query shapes are needed because no single one works everywhere:

* **ARIN** supports searching networks by name directly
  (``/registry/ips?name=Zenlayer*``), which returns the network objects —
  including their CIDRs — in one call.
* **RIPE** rejects that endpoint (HTTP 500) but does support entity search by
  formatted name (``/entities?fn=Zenlayer``). Each hit is an organization
  handle; following it to ``/entity/<handle>`` yields that org's networks.
  RIPE also rejects mid-string wildcards, so the term is sent unadorned.

APNIC, LACNIC, and AFRINIC expose no usable name search (empty results or 404),
so they are not queried; a provider registered solely in those regions will not
be found this way, and the result says so rather than implying the org does not
exist.

Results deliberately bypass the provider-substring filter and the datacenter
keyword classifier in :mod:`gaming.processing.filters`. The caller named the
organization explicitly, so re-filtering by keyword would discard legitimate
matches whose names lack a word like "hosting" — which is exactly the failure
this module exists to fix.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..logging_setup import get_logger
from ..models import IPRecord, normalize_asn
from ..utils.http import HTTPError, get_json

log = get_logger("gaming.discovery.provider_lookup")

SOURCE_NAME = "rdap-name"

# Registries with a working name search, in query order.
_ARIN_IPS = "https://rdap.arin.net/registry/ips?name={term}*"
_RIPE_ENTITIES = "https://rdap.db.ripe.net/entities?fn={term}"
_RIPE_ENTITY = "https://rdap.db.ripe.net/entity/{handle}"

_RDAP_HEADERS = {"Accept": "application/rdap+json"}

# An entity search can match many loosely-related handles; following every one
# would mean dozens of extra requests for a single lookup. Bounded so a vague
# term stays responsive.
_MAX_ENTITY_FOLLOWS = 12
DEFAULT_LIMIT = 200


@dataclass(slots=True)
class ProviderLookupResult:
    """Outcome of a name lookup, keeping "none found" apart from "lookup failed".

    Reporting a registry outage as "no such provider" is the specific way this
    feature failed before, so the distinction is part of the return type rather
    than left to the caller to infer from an empty list.
    """

    name: str
    records: list[IPRecord] = field(default_factory=list)
    #: Registries that answered, e.g. ``["arin", "ripe"]``.
    sources_queried: list[str] = field(default_factory=list)
    #: Human-readable failures, one per registry that could not be reached.
    errors: list[str] = field(default_factory=list)

    @property
    def found(self) -> bool:
        return bool(self.records)

    @property
    def all_sources_failed(self) -> bool:
        """True when nothing answered — an empty result here means nothing."""
        return bool(self.errors) and not self.sources_queried

    @property
    def organizations(self) -> list[str]:
        """Distinct organization names among the matches, in first-seen order."""
        seen: list[str] = []
        for rec in self.records:
            org = rec.organization
            if org and org not in seen:
                seen.append(org)
        return seen

    def summary(self) -> str:
        """One line describing the outcome, shared by every surface."""
        if self.found:
            orgs = len(self.organizations)
            return (
                f"{len(self.records)} range(s) across {orgs} organization(s) "
                f"for '{self.name}'."
            )
        if self.all_sources_failed:
            return (
                f"Could not reach any registry to look up '{self.name}': "
                f"{'; '.join(self.errors)}"
            )
        queried = ", ".join(self.sources_queried) or "no registries"
        return (
            f"No organization matching '{self.name}' is registered in {queried}. "
            "Note that only ARIN and RIPE support search by name."
        )


def lookup_provider_by_name(
    name: str,
    *,
    timeout: float = 15.0,
    limit: int = DEFAULT_LIMIT,
) -> ProviderLookupResult:
    """Find the networks registered to an organization by name.

    Queries ARIN and RIPE and merges the results, deduplicated by prefix and
    capped at ``limit``. Never raises for a network failure: a registry that
    cannot be reached is recorded in ``errors`` so the caller can distinguish
    "this provider does not exist" from "the lookup did not happen".
    """
    term = (name or "").strip()
    result = ProviderLookupResult(name=term)
    if not term:
        result.errors.append("no provider name given")
        return result

    seen: set[str] = set()
    for label, fetcher in (("arin", _query_arin), ("ripe", _query_ripe)):
        try:
            records = fetcher(term, timeout)
        except HTTPError as exc:
            if exc.not_found:
                # An authoritative "no such name" is an answer, not a failure.
                result.sources_queried.append(label)
                continue
            log.debug("%s lookup for %r failed: %s", label, term, exc)
            result.errors.append(f"{label}: {exc}")
            continue
        except Exception as exc:  # noqa: BLE001 - a bad registry must not raise
            log.debug("%s lookup for %r failed: %s", label, term, exc)
            result.errors.append(f"{label}: {type(exc).__name__}: {exc}")
            continue

        result.sources_queried.append(label)
        for rec in records:
            if rec.prefix in seen:
                continue
            seen.add(rec.prefix)
            result.records.append(rec)
            if len(result.records) >= limit:
                log.debug("provider lookup hit the %d-record cap", limit)
                return result
    return result


# ---- ARIN ----------------------------------------------------------------
def _query_arin(term: str, timeout: float) -> list[IPRecord]:
    """Search ARIN networks by name; one call yields the networks themselves."""
    data = get_json(
        _ARIN_IPS.format(term=_quote(term)), timeout=timeout, headers=_RDAP_HEADERS
    )
    out: list[IPRecord] = []
    for net in _as_list(data, "ipSearchResults"):
        out.extend(_networks_to_records(net, fallback_org=None))
    return out


# ---- RIPE ----------------------------------------------------------------
def _query_ripe(term: str, timeout: float) -> list[IPRecord]:
    """Search RIPE organizations by name, then follow each to its networks.

    RIPE's network-name endpoint returns HTTP 500, so the entity search is the
    only route; the extra hop per handle is why the follow count is bounded.
    """
    data = get_json(
        _RIPE_ENTITIES.format(term=_quote(term)), timeout=timeout,
        headers=_RDAP_HEADERS,
    )
    entities = _as_list(data, "entitySearchResults")

    out: list[IPRecord] = []
    for entity in entities[:_MAX_ENTITY_FOLLOWS]:
        handle = entity.get("handle") if isinstance(entity, dict) else None
        if not handle:
            continue
        try:
            detail = get_json(
                _RIPE_ENTITY.format(handle=_quote(str(handle))),
                timeout=timeout,
                headers=_RDAP_HEADERS,
            )
        except HTTPError as exc:
            log.debug("RIPE entity %s could not be followed: %s", handle, exc)
            continue
        if not isinstance(detail, dict):
            continue
        org = _vcard_fn(detail.get("vcardArray")) or str(handle)
        for net in detail.get("networks") or []:
            out.extend(_networks_to_records(net, fallback_org=org))
    return out


# ---- shared parsing ------------------------------------------------------
def _networks_to_records(net: Any, *, fallback_org: str | None) -> list[IPRecord]:
    """Turn one RDAP network object into IPRecords, one per CIDR it declares.

    A single RDAP network can cover several CIDRs (``cidr0_cidrs`` is a list),
    and the CIDR form is preferred over start/end addresses because it is what
    the rest of the pipeline consumes.
    """
    if not isinstance(net, dict):
        return []

    org = fallback_org or _network_org(net)
    country = net.get("country")
    country = country.strip().upper() if isinstance(country, str) else None
    asn = _network_asn(net)

    # ARIN networks carry no country field, so record the registrant's location
    # as free text rather than guessing an ISO code the registry never gave.
    note = "RDAP lookup by organization name"
    if country is None:
        where = _registrant_country_label(net)
        if where:
            note = f"{note}; registrant address: {where}"

    out: list[IPRecord] = []
    for prefix in _cidrs_of(net):
        try:
            out.append(
                IPRecord(
                    prefix=prefix,
                    source=SOURCE_NAME,
                    asn=asn,
                    organization=org,
                    country=country,
                    provider=org,
                    notes=note,
                )
            )
        except ValueError:
            continue
    return out


def _registrant_country_label(net: dict[str, Any]) -> str | None:
    """Last line of the registrant's postal label — usually the country name."""
    for entity in _walk_entities(net):
        if "registrant" not in (entity.get("roles") or []):
            continue
        label = _vcard_param(entity.get("vcardArray"), "adr", "label")
        if label:
            lines = [ln.strip() for ln in label.splitlines() if ln.strip()]
            if lines:
                return lines[-1]
    return None


def _cidrs_of(net: dict[str, Any]) -> list[str]:
    """Extract CIDR strings from an RDAP network's ``cidr0_cidrs`` block."""
    out: list[str] = []
    for entry in net.get("cidr0_cidrs") or []:
        if not isinstance(entry, dict):
            continue
        length = entry.get("length")
        base = entry.get("v4prefix") or entry.get("v6prefix")
        if base and length is not None:
            out.append(f"{base}/{length}")
    return out


def _network_org(net: dict[str, Any]) -> str | None:
    """Best available organization name for a network object.

    ARIN nests contact entities several levels deep and the first ``fn`` on a
    network is frequently an individual admin/abuse contact ("qu ming") rather
    than the company. So an entity is only trusted when it identifies itself as
    an organization (``kind`` of ``org``) or holds the registrant role; failing
    that the network's own ``name`` (e.g. "ZENLAYER") is far more useful to an
    operator than a stranger's personal name.
    """
    best_registrant: str | None = None
    best_org_kind: str | None = None

    for entity in _walk_entities(net):
        vcard = entity.get("vcardArray")
        fn = _vcard_fn(vcard)
        if not fn:
            continue
        roles = entity.get("roles") or []
        if "registrant" in roles and best_registrant is None:
            best_registrant = fn
        if _vcard_field(vcard, "kind") == "org" and best_org_kind is None:
            best_org_kind = fn

    name = net.get("name")
    name = name.strip() if isinstance(name, str) and name.strip() else None
    return best_registrant or best_org_kind or name


def _walk_entities(node: Any, depth: int = 0):
    """Yield every entity dict nested under an RDAP object.

    ARIN embeds entities inside entities; a flat scan of the top level misses
    the organization record entirely. Depth-bounded so a cyclic or pathological
    payload cannot spin.
    """
    if depth > 4 or not isinstance(node, dict):
        return
    for entity in node.get("entities") or []:
        if not isinstance(entity, dict):
            continue
        yield entity
        yield from _walk_entities(entity, depth + 1)


def _network_asn(net: dict[str, Any]) -> str | None:
    """ASN from an RDAP network, when the registry provides one."""
    for key in ("originAutnums", "arin_originas0_originautnums"):
        values = net.get(key)
        if isinstance(values, list) and values:
            try:
                return normalize_asn(values[0])
            except (TypeError, ValueError):
                continue
    return None


def _vcard_fn(vcard_array: Any) -> str | None:
    """Pull the ``fn`` (formatted name) property out of a jCard structure."""
    return _vcard_field(vcard_array, "fn")


def _vcard_field(vcard_array: Any, field_name: str) -> str | None:
    """Pull a named text property out of a jCard structure.

    A vcardArray looks like ``["vcard", [["fn", {}, "text", "ACME"], ...]]``.
    """
    if not isinstance(vcard_array, list) or len(vcard_array) < 2:
        return None
    for entry in vcard_array[1]:
        if (
            isinstance(entry, list)
            and len(entry) >= 4
            and entry[0] == field_name
            and isinstance(entry[3], str)
            and entry[3].strip()
        ):
            return entry[3].strip()
    return None


def _vcard_param(vcard_array: Any, field_name: str, param: str) -> str | None:
    """Pull a parameter (e.g. ``adr``'s ``label``) off a jCard property.

    Postal details live in the property's parameter dict rather than its value,
    which for ``adr`` is usually a list of empty strings.
    """
    if not isinstance(vcard_array, list) or len(vcard_array) < 2:
        return None
    for entry in vcard_array[1]:
        if isinstance(entry, list) and len(entry) >= 2 and entry[0] == field_name:
            params = entry[1]
            if isinstance(params, dict):
                value = params.get(param)
                if isinstance(value, str) and value.strip():
                    return value.strip()
    return None


def _as_list(data: Any, key: str) -> list[Any]:
    if not isinstance(data, dict):
        return []
    value = data.get(key)
    return value if isinstance(value, list) else []


def _quote(value: str) -> str:
    from urllib.parse import quote

    return quote(value, safe="")
