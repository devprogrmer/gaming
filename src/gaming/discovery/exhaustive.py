"""Exhaustive, country-wide allocation discovery.

The ordinary discovery sources are *seed-driven*: they answer "what does this
known provider announce?". That structurally cannot find a hosting company
nobody hand-added to ``providers.toml``, no matter how well documented its own
registry records are.

This module answers the opposite question — "what is allocated to this country,
by anyone?" — by walking the authoritative RIR delegated-statistics table and
resolving each prefix's operator from public registry data:

1. **Enumerate** every delegated IPv4 *and* IPv6 prefix for the country
   (:func:`gaming.discovery.rir.parse_delegated_networks`).
2. **Resolve the announcing ASN** for each prefix via RIPEstat.
3. **Resolve that ASN's registered organization** via RDAP, falling back to
   WHOIS, and cache it per-ASN so each AS is looked up once no matter how many
   prefixes it announces.

A small, obscure hosting company therefore surfaces with exactly the same
completeness — CIDR, ASN, organization, country — as a famous one. Fame is not
an input anywhere in this pipeline.

Two deliberate departures from the seeded sources:

* **No sample-data fallback.** :meth:`gaming.discovery.base.Source.discover`
  substitutes bundled fake records when a live lookup comes up empty. That is
  reasonable for a demo pass but actively harmful here: a rate-limited sweep
  would silently emit fiction like "Cloudflare (sample)" that is
  indistinguishable from a real finding. This module only ever returns what the
  registries actually said.
* **Unresolved organizations are kept, not dropped.** Some allocations really
  have no public org name (RIR delegated data with no matching RDAP/WHOIS
  object). Those records are labelled :data:`UNNAMED_ORG` and retained, because
  "an allocation exists here and nobody will say whose it is" is a finding.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field

from ..logging_setup import get_logger
from ..models import IPRecord, normalize_asn
from ..utils.http import HTTPError, get_json, get_text
from .base import DiscoveryContext
from .rdap import RDAPSource
from .resume import ResumeJournal
from .rir import DELEGATED_URL, parse_delegated_networks
from .whois import WhoisSource

log = get_logger("gaming.discovery.exhaustive")

#: Label for an allocation whose operator no public registry will name.
UNNAMED_ORG = "(unnamed / no public org name)"

#: RIPEstat: which ASN currently announces a given prefix.
_NETWORK_INFO_URL = "https://stat.ripe.net/data/network-info/data.json?resource={prefix}"


@dataclass(slots=True)
class SweepProgress:
    """Live counters for an in-flight sweep (for progress display)."""

    country: str
    total: int = 0
    done: int = 0
    resumed: int = 0
    named: int = 0
    unnamed: int = 0
    errors: int = 0

    @property
    def remaining(self) -> int:
        return max(0, self.total - self.done)


@dataclass(slots=True)
class ExhaustiveSweep:
    """Resolve every delegated prefix for one country to its operator.

    ``progress_callback`` is invoked after each prefix with the live
    :class:`SweepProgress`, so a CLI spinner or web job can report advancement
    on an operation that legitimately takes minutes.
    """

    country: str
    context: DiscoveryContext
    include_ipv6: bool = True
    resume: bool = True
    journal: ResumeJournal | None = None
    progress_callback: Callable[[SweepProgress], None] | None = None
    #: Per-ASN organization cache: asn -> (organization, country).
    _org_cache: dict[str, tuple[str | None, str | None]] = field(default_factory=dict)

    # ---- top level -------------------------------------------------------
    def run(self) -> list[IPRecord]:
        """Run the sweep to completion and return every resolved record."""
        return list(self.iter_records())

    def iter_records(self) -> Iterator[IPRecord]:
        """Yield resolved records as they are produced.

        Streaming keeps memory flat on a country with thousands of prefixes and
        lets callers persist incrementally.
        """
        country = self.country.upper()
        body = self._fetch_delegated()
        if body is None:
            return

        networks = list(
            parse_delegated_networks(body, {country}, include_ipv6=self.include_ipv6)
        )
        dataset = _dataset_marker(body)
        journal = self.journal
        if journal is None and self.resume:
            journal = ResumeJournal.load(country, dataset=dataset)
        elif journal is not None and not journal.dataset:
            journal.dataset = dataset

        progress = SweepProgress(country=country, total=len(networks))
        log.info(
            "exhaustive sweep for %s: %d delegated prefix(es)", country, progress.total
        )

        try:
            for net, cc, _status in networks:
                prefix = str(net)
                cached = journal.entries.get(prefix) if journal else None
                if cached is not None:
                    progress.done += 1
                    progress.resumed += 1
                    record = _record_from_payload(cached)
                    if record is not None:
                        self._tally(progress, record)
                        self._emit_progress(progress)
                        yield record
                        continue

                record = self._resolve_prefix(net, cc, progress)
                progress.done += 1
                self._tally(progress, record)
                if journal is not None:
                    journal.record(prefix, _payload_from_record(record))
                    journal.flush()
                self._emit_progress(progress)
                yield record
        finally:
            # Persist whatever progress exists even if the caller stops
            # consuming (Ctrl+C, break, exception) — that is the whole point
            # of the journal.
            if journal is not None:
                journal.flush(force=True)

        if journal is not None:
            journal.clear()
        log.info(
            "exhaustive sweep for %s complete: %d prefix(es) (%d named, %d unnamed, "
            "%d resumed from journal, %d lookup error(s))",
            country,
            progress.done,
            progress.named,
            progress.unnamed,
            progress.resumed,
            progress.errors,
        )

    # ---- steps -----------------------------------------------------------
    def _fetch_delegated(self) -> str | None:
        try:
            return get_text(DELEGATED_URL, timeout=max(30.0, self.context.timeout))
        except HTTPError as exc:
            log.warning("could not fetch RIR delegated statistics: %s", exc)
            return None

    def _resolve_prefix(
        self,
        net: ipaddress.IPv4Network | ipaddress.IPv6Network,
        cc: str,
        progress: SweepProgress,
    ) -> IPRecord:
        prefix = str(net)
        asn = self._announcing_asn(prefix, progress)
        org: str | None = None
        org_country: str | None = None
        if asn:
            org, org_country = self._organization_for(asn, progress)

        return IPRecord(
            prefix=prefix,
            source="exhaustive",
            asn=asn,
            organization=org or UNNAMED_ORG,
            country=cc or org_country,
            notes="exhaustive country sweep (RIR delegated + BGP + RDAP/WHOIS)",
        )

    def _announcing_asn(self, prefix: str, progress: SweepProgress) -> str | None:
        """Which ASN currently announces ``prefix``, per RIPEstat."""
        try:
            data = get_json(
                _NETWORK_INFO_URL.format(prefix=prefix), timeout=self.context.timeout
            )
        except HTTPError as exc:
            # A 404 here just means "nothing announces this block" — common for
            # allocated-but-unrouted space, and not worth a warning.
            if not exc.not_found:
                progress.errors += 1
                log.debug("ASN lookup failed for %s: %s", prefix, exc)
            return None
        asns = (data.get("data") or {}).get("asns") or []
        for candidate in asns:
            if candidate in (None, ""):
                continue
            try:
                return normalize_asn(str(candidate))
            except ValueError:
                continue
        return None

    def _organization_for(
        self, asn: str, progress: SweepProgress
    ) -> tuple[str | None, str | None]:
        """Resolve ``asn`` to (organization, country), RDAP first then WHOIS.

        Cached per ASN: a big hoster announcing 200 prefixes costs one lookup,
        which is what keeps a country-wide sweep finishing in minutes.
        """
        if asn in self._org_cache:
            return self._org_cache[asn]

        result: tuple[str | None, str | None] = (None, None)
        try:
            result = self._rdap_org(asn)
            if not result[0]:
                whois_org = self._whois_org(asn)
                if whois_org:
                    result = (whois_org, result[1])
        except Exception as exc:  # noqa: BLE001 - one bad ASN must not abort a sweep
            progress.errors += 1
            log.debug("organization lookup failed for %s: %s", asn, exc)

        self._org_cache[asn] = result
        return result

    def _rdap_org(self, asn: str) -> tuple[str | None, str | None]:
        """Reuse RDAPSource's autnum parsing rather than reimplementing jCard."""
        source = RDAPSource(self.context)
        return source._lookup_autnum(source._asn_number(asn))

    def _whois_org(self, asn: str) -> str | None:
        """Last-resort org name from a WHOIS ``aut-num`` object."""
        source = WhoisSource(self.context)
        try:
            response = source._raw_query(asn)
        except OSError as exc:
            log.debug("WHOIS org lookup failed for %s: %s", asn, exc)
            return None
        return _parse_whois_org(response)

    # ---- helpers ---------------------------------------------------------
    @staticmethod
    def _tally(progress: SweepProgress, record: IPRecord | None) -> None:
        if record is None:
            return
        if record.organization and record.organization != UNNAMED_ORG:
            progress.named += 1
        else:
            progress.unnamed += 1

    def _emit_progress(self, progress: SweepProgress) -> None:
        if self.progress_callback is None:
            return
        try:
            self.progress_callback(progress)
        except Exception as exc:  # noqa: BLE001 - display must never break a sweep
            log.debug("progress callback failed: %s", exc)


def discover_country(
    country: str,
    *,
    context: DiscoveryContext | None = None,
    include_ipv6: bool = True,
    resume: bool = True,
    progress_callback: Callable[[SweepProgress], None] | None = None,
) -> list[IPRecord]:
    """Convenience wrapper: exhaustively discover every allocation for ``country``."""
    from ..models import Filters

    ctx = context or DiscoveryContext(filters=Filters(countries=[country.upper()]))
    sweep = ExhaustiveSweep(
        country=country,
        context=ctx,
        include_ipv6=include_ipv6,
        resume=resume,
        progress_callback=progress_callback,
    )
    return sweep.run()


def _dataset_marker(body: str) -> str:
    """Identify the delegated-stats snapshot from its version header line.

    The first non-comment line looks like
    ``2|ripencc|20260727|56789|19860514|20260727|+0000``; field 2 is the
    serial/date of the publication, which is exactly the "has upstream data
    changed?" signal the resume journal needs.
    """
    for line in body.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("|")
        if len(parts) >= 3:
            return f"{parts[1]}-{parts[2]}"
        return line[:32]
    return ""


def _parse_whois_org(response: str) -> str | None:
    """Pull an organization name out of a WHOIS ``aut-num`` response."""
    preferred: str | None = None
    fallback: str | None = None
    for raw in response.splitlines():
        key, sep, value = raw.partition(":")
        if not sep:
            continue
        key = key.strip().lower()
        value = value.strip()
        if not value or value.startswith("#"):
            continue
        if key in ("org-name", "orgname", "organization"):
            return value
        if key == "as-name" and preferred is None:
            preferred = value
        elif key in ("descr", "owner") and fallback is None:
            fallback = value
    return preferred or fallback


def _payload_from_record(record: IPRecord) -> dict[str, object]:
    """Journal representation of a resolved record."""
    return {
        "prefix": record.prefix,
        "asn": record.asn,
        "organization": record.organization,
        "country": record.country,
    }


def _record_from_payload(payload: dict[str, object]) -> IPRecord | None:
    """Rebuild a record from its journal payload, or ``None`` if unusable."""
    prefix = payload.get("prefix")
    if not isinstance(prefix, str) or not prefix:
        return None
    try:
        return IPRecord(
            prefix=prefix,
            source="exhaustive",
            asn=_opt_str(payload.get("asn")),
            organization=_opt_str(payload.get("organization")) or UNNAMED_ORG,
            country=_opt_str(payload.get("country")),
            notes="exhaustive country sweep (resumed from journal)",
        )
    except ValueError:
        return None


def _opt_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def summarize(records: Iterable[IPRecord]) -> dict[str, int]:
    """Aggregate counts for reporting after a sweep."""
    records = list(records)
    named = sum(
        1 for r in records if r.organization and r.organization != UNNAMED_ORG
    )
    return {
        "prefixes": len(records),
        "named": named,
        "unnamed": len(records) - named,
        "asns": len({r.asn for r in records if r.asn}),
        "organizations": len(
            {
                r.organization
                for r in records
                if r.organization and r.organization != UNNAMED_ORG
            }
        ),
    }
