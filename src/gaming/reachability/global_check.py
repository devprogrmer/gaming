"""Global ("abroad") reachability checks behind a pluggable provider interface.

The abroad check answers "is this IP reachable from outside Iran?" Historically
this depended entirely on one free third-party service (check-host.net) with no
SLA. To reduce that single point of failure, the check is abstracted behind a
small :class:`AbroadProvider` interface with two implementations:

* :class:`CheckHostProvider` — the original check-host.net logic, unchanged.
* :class:`RipeAtlasProvider` — an optional, more authoritative alternative
  using RIPE Atlas (requires a free API key; skipped entirely when unset).

A provider returns an :class:`AbroadResult` that distinguishes three cases the
old boolean/``None`` return could not tell apart:

* the check ran and got a real answer (``status="ok"``, ``reachable`` True/False
  with node counts),
* the check does not apply (non-public host) — ``status="not_applicable"``,
* the provider could not run at all (service down / HTTP errors) —
  ``status="unavailable"``.

Only public IP addresses are submitted; private/reserved addresses are skipped.

:func:`global_reachability` is retained as a thin, backward-compatible wrapper
returning the original ``(reachable, nodes_ok, nodes_total)`` tuple, so existing
callers and tests keep working while new code uses :func:`check_abroad`.
"""

from __future__ import annotations

import abc
import ipaddress
import os
import time
from dataclasses import dataclass

from ..logging_setup import get_logger
from ..utils.http import HTTPError, get_json, post_json

log = get_logger("gaming.reachability.global")

_BASE = "https://check-host.net"
_ATLAS_BASE = "https://atlas.ripe.net/api/v2"
_ATLAS_PROBES_URL = f"{_ATLAS_BASE}/probes/"
_RIPESTAT_NETWORK_INFO = "https://stat.ripe.net/data/network-info/data.json?resource={ip}"

#: Environment variable holding an optional RIPE Atlas API key. Never hardcoded.
RIPE_ATLAS_KEY_ENV = "GAMING_RIPE_ATLAS_KEY"

# Status values for measure_from_near()/ProximityPingResult — distinct from the
# ABROAD_* constants because this measures something conceptually different
# (a third-party probe's path to an arbitrary destination, not "is this host
# reachable from outside Iran").
PROXIMITY_OK = "ok"
PROXIMITY_NO_PROBE = "no_nearby_probe"
PROXIMITY_UNAVAILABLE = "unavailable"

#: Must accompany every ProximityPingResult shown to a user — this is always an
#: approximation, never a measurement literally made by the source IP itself.
PROXIMITY_APPROXIMATION_NOTE = (
    "Approximate — measured from the nearest available RIPE Atlas probe "
    "to this IP's network, not from the IP itself."
)

# Abroad-check status values. Distinct so a user watching results over time can
# tell "the service is down right now" apart from "this IP simply isn't
# internationally reachable" and "we didn't/ couldn't check this host".
ABROAD_OK = "ok"  # a real answer came back (reachable is True/False)
ABROAD_NOT_APPLICABLE = "not_applicable"  # non-public host / not checked
ABROAD_UNAVAILABLE = "unavailable"  # provider(s) could not run at all

# Provider choices for config/Settings.
PROVIDER_CHECK_HOST = "check-host"
PROVIDER_RIPE_ATLAS = "ripe-atlas"
PROVIDER_BOTH = "both"


@dataclass(slots=True)
class AbroadResult:
    """Outcome of an abroad-reachability check for one host.

    ``reachable`` is ``True``/``False`` only when ``status == "ok"``; otherwise
    it is ``None``. ``nodes_ok``/``nodes_total`` carry the responding-node counts
    (across all providers, when combined) so the UI can show ``OK (n/total)``.
    ``status`` is one of :data:`ABROAD_OK`, :data:`ABROAD_NOT_APPLICABLE`,
    :data:`ABROAD_UNAVAILABLE`.
    """

    reachable: bool | None
    nodes_ok: int = 0
    nodes_total: int = 0
    status: str = ABROAD_NOT_APPLICABLE

    def __iter__(self):
        # Backward-compatible unpacking as the old 3-tuple.
        yield self.reachable
        yield self.nodes_ok
        yield self.nodes_total

    @classmethod
    def ok(cls, reachable: bool, nodes_ok: int, nodes_total: int) -> AbroadResult:
        return cls(reachable, nodes_ok, nodes_total, ABROAD_OK)

    @classmethod
    def not_applicable(cls) -> AbroadResult:
        return cls(None, 0, 0, ABROAD_NOT_APPLICABLE)

    @classmethod
    def unavailable(cls) -> AbroadResult:
        return cls(None, 0, 0, ABROAD_UNAVAILABLE)


def _is_public(host: str) -> bool:
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


class AbroadProvider(abc.ABC):
    """Interface for an abroad-reachability provider.

    Implementations perform a measurement of ``host`` from outside Iran and
    return an :class:`AbroadResult`. They must be fail-soft: any network/parse
    error is reported as ``AbroadResult.unavailable()``, never raised.
    """

    name: str = "abroad"

    @abc.abstractmethod
    def check(
        self,
        host: str,
        *,
        timeout: float = 5.0,
        port: int = 80,
        min_ok_fraction: float = 0.5,
    ) -> AbroadResult:
        """Measure abroad reachability of ``host`` and return an AbroadResult."""


class CheckHostProvider(AbroadProvider):
    """check-host.net implementation (the original, behaviour-preserving logic).

    Kicks off distributed probes (returns a request id), then polls the result
    endpoint until enough nodes have reported. A non-public host is
    ``not_applicable``; a failure to start or to ever get a node result is
    ``unavailable`` (distinct from a real "not reachable" answer).
    """

    name = PROVIDER_CHECK_HOST

    def __init__(
        self,
        *,
        check_type: str = "tcp",
        poll_attempts: int = 3,
        poll_interval: float = 2.0,
        max_nodes: int = 8,
    ) -> None:
        self.check_type = check_type
        self.poll_attempts = poll_attempts
        self.poll_interval = poll_interval
        self.max_nodes = max_nodes

    def check(
        self,
        host: str,
        *,
        timeout: float = 5.0,
        port: int = 80,
        min_ok_fraction: float = 0.5,
    ) -> AbroadResult:
        if not _is_public(host):
            log.debug("skipping global check for non-public host %s", host)
            return AbroadResult.not_applicable()

        target = f"{host}:{port}" if self.check_type == "tcp" else host
        start_url = (
            f"{_BASE}/check-{self.check_type}?host={target}&max_nodes={self.max_nodes}"
        )
        try:
            started = get_json(
                start_url, timeout=timeout, headers={"Accept": "application/json"}
            )
        except HTTPError as exc:
            log.debug("global check start failed for %s: %s", host, exc)
            return AbroadResult.unavailable()

        request_id = started.get("request_id") if isinstance(started, dict) else None
        if not request_id:
            return AbroadResult.unavailable()

        result_url = f"{_BASE}/check-result/{request_id}"
        for _ in range(max(1, self.poll_attempts)):
            time.sleep(self.poll_interval)
            try:
                results = get_json(result_url, timeout=timeout)
            except HTTPError as exc:
                log.debug("global check poll failed for %s: %s", host, exc)
                continue
            ok, total = _interpret(results)
            if total > 0:
                reachable = (ok / total) >= min_ok_fraction
                return AbroadResult.ok(reachable, ok, total)
        # Started but never got a usable node result -> the service could not
        # give us an answer, which is an outage/timeout, not "not reachable".
        return AbroadResult.unavailable()


class RipeAtlasProvider(AbroadProvider):
    """Optional RIPE Atlas implementation (https://atlas.ripe.net).

    Creates a one-off ping measurement toward ``host`` and polls its results,
    analogous to the check-host.net poll loop. Requires an API key (constructor
    arg or :data:`RIPE_ATLAS_KEY_ENV`); with no key the provider reports every
    check as ``unavailable`` — :func:`build_providers` skips it entirely so the
    tool falls back to check-host.net only with no behaviour change.
    """

    name = PROVIDER_RIPE_ATLAS

    def __init__(
        self,
        *,
        api_key: str | None = None,
        probes: int = 5,
        poll_attempts: int = 5,
        poll_interval: float = 3.0,
    ) -> None:
        self.api_key = api_key or os.environ.get(RIPE_ATLAS_KEY_ENV) or None
        self.probes = probes
        self.poll_attempts = poll_attempts
        self.poll_interval = poll_interval

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def check(
        self,
        host: str,
        *,
        timeout: float = 5.0,
        port: int = 80,
        min_ok_fraction: float = 0.5,
    ) -> AbroadResult:
        if not _is_public(host):
            return AbroadResult.not_applicable()
        if not self.api_key:
            log.debug("RIPE Atlas provider used without an API key; unavailable")
            return AbroadResult.unavailable()

        create_url = f"{_ATLAS_BASE}/measurements/?key={self.api_key}"
        payload = {
            "definitions": [
                {
                    "target": host,
                    "af": ipaddress.ip_address(host).version,
                    "type": "ping",
                    "description": "gaming abroad-reachability check",
                    "packets": 3,
                }
            ],
            "probes": [{"requested": self.probes, "type": "area", "value": "WW"}],
            "is_oneoff": True,
        }
        try:
            created = post_json(create_url, payload, timeout=timeout)
        except HTTPError as exc:
            log.debug("RIPE Atlas measurement create failed for %s: %s", host, exc)
            return AbroadResult.unavailable()

        ids = created.get("measurements") if isinstance(created, dict) else None
        if not ids:
            return AbroadResult.unavailable()
        measurement_id = ids[0]

        result_url = (
            f"{_ATLAS_BASE}/measurements/{measurement_id}/results/?key={self.api_key}"
        )
        for _ in range(max(1, self.poll_attempts)):
            time.sleep(self.poll_interval)
            try:
                results = get_json(result_url, timeout=timeout)
            except HTTPError as exc:
                log.debug("RIPE Atlas poll failed for %s: %s", host, exc)
                continue
            ok, total = _interpret_atlas(results)
            if total > 0:
                reachable = (ok / total) >= min_ok_fraction
                return AbroadResult.ok(reachable, ok, total)
        return AbroadResult.unavailable()


@dataclass(slots=True)
class ProximityPingResult:
    """Approximate "ping from a discovered IP" result — see :func:`measure_from_near`.

    This is NEVER the source IP's own ping. No external tool can make an
    arbitrary third-party host originate a probe on our behalf; the closest
    honest approximation is to ask the RIPE Atlas probe nearest to the source
    IP's network to ping the destination. ``note`` carries that disclaimer and
    must be surfaced with every result shown to a user.

    ``status`` is one of :data:`PROXIMITY_OK`, :data:`PROXIMITY_NO_PROBE`
    (no probe exists near the source IP's network), or
    :data:`PROXIMITY_UNAVAILABLE` (not configured, or the measurement could not
    be completed) — never a silently wrong number.
    """

    status: str
    probe_id: int | None = None
    probe_asn: str | None = None
    avg_ms: float | None = None
    reachable: bool | None = None
    note: str = PROXIMITY_APPROXIMATION_NOTE

    @classmethod
    def unavailable(cls, reason: str = "") -> ProximityPingResult:
        note = PROXIMITY_APPROXIMATION_NOTE
        if reason:
            note = f"{note} ({reason})"
        return cls(status=PROXIMITY_UNAVAILABLE, note=note)

    @classmethod
    def no_nearby_probe(cls) -> ProximityPingResult:
        return cls(
            status=PROXIMITY_NO_PROBE,
            note=(
                f"{PROXIMITY_APPROXIMATION_NOTE} "
                "No RIPE Atlas probe was found near this IP's network."
            ),
        )


def measure_from_near(
    source_ip: str,
    destination_ip: str,
    *,
    timeout: float = 15.0,
    api_key: str | None = None,
    poll_attempts: int = 5,
    poll_interval: float = 3.0,
) -> ProximityPingResult:
    """Best-effort: find the RIPE Atlas probe(s) nearest to ``source_ip``
    (by ASN match against the RIPE Atlas probe metadata API) and request a
    one-off ping measurement from that probe to ``destination_ip``. Returns
    latency/loss if a nearby probe exists and the measurement completes;
    returns a clear "no nearby probe available" / "measurement unavailable"
    result otherwise — never a silently wrong number.

    This is an approximation, NOT literally the discovered IP's own ping: a
    remote tool cannot make a third party's host originate traffic. The
    returned :class:`ProximityPingResult` always carries that disclaimer in
    its ``note``. Gated behind :data:`RIPE_ATLAS_KEY_ENV` (or ``api_key``); with
    no key it is a no-op returning ``unavailable`` with a "not configured"
    reason.
    """
    key = api_key or os.environ.get(RIPE_ATLAS_KEY_ENV) or None
    if not key:
        return ProximityPingResult.unavailable("RIPE Atlas API key not configured")

    try:
        ipaddress.ip_address(source_ip)
        dest_af = ipaddress.ip_address(destination_ip).version
    except ValueError:
        return ProximityPingResult.unavailable("invalid IP address")

    # 1) Map the source IP to its origin ASN so we can search for probes in the
    #    same network. A network error here is "unavailable" (we couldn't check),
    #    distinct from "no probe near it".
    try:
        net_info = get_json(_RIPESTAT_NETWORK_INFO.format(ip=source_ip), timeout=timeout)
    except HTTPError as exc:
        log.debug("RIPEstat network-info lookup failed for %s: %s", source_ip, exc)
        return ProximityPingResult.unavailable(
            "could not determine the source IP's network"
        )

    asns = []
    if isinstance(net_info, dict):
        asns = ((net_info.get("data") or {}).get("asns")) or []
    if not asns:
        return ProximityPingResult.no_nearby_probe()
    asn = str(asns[0])

    # 2) Look for a connected RIPE Atlas probe hosted in that ASN.
    probe_key = "asn_v6" if dest_af == 6 else "asn_v4"
    try:
        probes = get_json(
            f"{_ATLAS_PROBES_URL}?{probe_key}={asn}&status=1&key={key}", timeout=timeout
        )
    except HTTPError as exc:
        log.debug("RIPE Atlas probe search failed for AS%s: %s", asn, exc)
        return ProximityPingResult.unavailable("probe search failed")

    probe_list = probes.get("results") if isinstance(probes, dict) else None
    if not probe_list:
        return ProximityPingResult.no_nearby_probe()
    probe_id = probe_list[0].get("id") if isinstance(probe_list[0], dict) else None
    if probe_id is None:
        return ProximityPingResult.no_nearby_probe()

    # 3) Ask exactly that probe to ping the destination (one-off).
    create_url = f"{_ATLAS_BASE}/measurements/?key={key}"
    payload = {
        "definitions": [
            {
                "target": destination_ip,
                "af": dest_af,
                "type": "ping",
                "description": "gaming proximity ping (approximate)",
                "packets": 3,
            }
        ],
        "probes": [{"requested": 1, "type": "probes", "value": str(probe_id)}],
        "is_oneoff": True,
    }
    try:
        created = post_json(create_url, payload, timeout=timeout)
    except HTTPError as exc:
        log.debug("RIPE Atlas proximity measurement create failed: %s", exc)
        return ProximityPingResult.unavailable("measurement could not be started")

    ids = created.get("measurements") if isinstance(created, dict) else None
    if not ids:
        return ProximityPingResult.unavailable("measurement could not be started")
    measurement_id = ids[0]

    result_url = f"{_ATLAS_BASE}/measurements/{measurement_id}/results/?key={key}"
    for _ in range(max(1, poll_attempts)):
        time.sleep(poll_interval)
        try:
            results = get_json(result_url, timeout=timeout)
        except HTTPError as exc:
            log.debug("RIPE Atlas proximity poll failed: %s", exc)
            continue
        if isinstance(results, list) and results and isinstance(results[0], dict):
            entry = results[0]
            rcvd = entry.get("rcvd")
            avg = entry.get("avg")
            if rcvd is not None or avg is not None:
                reachable = (isinstance(rcvd, int) and rcvd > 0) or (
                    isinstance(avg, (int, float)) and avg > 0
                )
                avg_ms = avg if isinstance(avg, (int, float)) and avg > 0 else None
                return ProximityPingResult(
                    status=PROXIMITY_OK,
                    probe_id=probe_id,
                    probe_asn=asn,
                    avg_ms=avg_ms,
                    reachable=reachable,
                )
    return ProximityPingResult.unavailable("measurement did not complete in time")


def combine_results(
    results: list[AbroadResult], *, min_ok_fraction: float = 0.5
) -> AbroadResult:
    """Merge several providers' results into one verdict.

    Node-ok/node-total counts from every provider that produced a real answer
    (``status == "ok"``) are summed *before* applying ``min_ok_fraction``, so a
    single provider's outage or rate-limit doesn't by itself decide the verdict.
    If no provider produced a real answer, the combined status is
    ``unavailable`` when at least one tried and failed, else ``not_applicable``.
    """
    ok_total = 0
    node_total = 0
    saw_ok = False
    saw_unavailable = False
    for res in results:
        if res.status == ABROAD_OK:
            saw_ok = True
            ok_total += res.nodes_ok
            node_total += res.nodes_total
        elif res.status == ABROAD_UNAVAILABLE:
            saw_unavailable = True
    if saw_ok and node_total > 0:
        return AbroadResult.ok(
            (ok_total / node_total) >= min_ok_fraction, ok_total, node_total
        )
    if saw_unavailable:
        return AbroadResult.unavailable()
    return AbroadResult.not_applicable()


def build_providers(
    choice: str = PROVIDER_CHECK_HOST,
    *,
    ripe_atlas_key: str | None = None,
    check_type: str = "tcp",
) -> list[AbroadProvider]:
    """Build the provider list for a ``choice`` (check-host / ripe-atlas / both).

    The RIPE Atlas provider is only included when an API key is available
    (argument or :data:`RIPE_ATLAS_KEY_ENV`); otherwise it is silently skipped,
    so an unconfigured install falls back to check-host.net with no crash. If a
    choice would leave no usable provider, check-host.net is used as the floor.
    """
    choice = (choice or PROVIDER_CHECK_HOST).strip().lower()
    key = ripe_atlas_key or os.environ.get(RIPE_ATLAS_KEY_ENV) or None
    providers: list[AbroadProvider] = []

    want_check_host = choice in (PROVIDER_CHECK_HOST, PROVIDER_BOTH)
    want_atlas = choice in (PROVIDER_RIPE_ATLAS, PROVIDER_BOTH)

    if want_check_host:
        providers.append(CheckHostProvider(check_type=check_type))
    if want_atlas:
        atlas = RipeAtlasProvider(api_key=key)
        if atlas.available:
            providers.append(atlas)
        else:
            log.debug("RIPE Atlas requested but no API key set; skipping")

    if not providers:
        providers.append(CheckHostProvider(check_type=check_type))
    return providers


def check_abroad(
    host: str,
    *,
    providers: list[AbroadProvider] | None = None,
    timeout: float = 5.0,
    port: int = 80,
    min_ok_fraction: float = 0.5,
    check_type: str = "tcp",
) -> AbroadResult:
    """Check abroad reachability of ``host`` across one or more providers.

    ``providers`` defaults to a single check-host.net provider. Each provider is
    run fail-soft (an exception becomes an ``unavailable`` result) and the
    outcomes are merged with :func:`combine_results`.
    """
    if providers is None:
        providers = [CheckHostProvider(check_type=check_type)]

    results: list[AbroadResult] = []
    for provider in providers:
        try:
            results.append(
                provider.check(
                    host, timeout=timeout, port=port, min_ok_fraction=min_ok_fraction
                )
            )
        except Exception as exc:  # noqa: BLE001 - a provider must never crash a scan
            log.warning(
                "abroad provider %s failed for %s: %s: %s",
                getattr(provider, "name", "?"),
                host,
                type(exc).__name__,
                exc,
            )
            results.append(AbroadResult.unavailable())
    return combine_results(results, min_ok_fraction=min_ok_fraction)


def global_reachability(
    host: str,
    *,
    timeout: float = 5.0,
    check_type: str = "tcp",
    port: int = 80,
    poll_attempts: int = 3,
    poll_interval: float = 2.0,
    min_ok_fraction: float = 0.5,
) -> tuple[bool | None, int, int]:
    """Backward-compatible check-host.net query returning the original tuple.

    Returns ``(reachable, nodes_ok, nodes_total)``; ``reachable`` is ``None``
    for a non-public host or when the service could not give an answer. New code
    should prefer :func:`check_abroad`, which additionally distinguishes
    "service unavailable" from "not applicable" via :class:`AbroadResult`.
    """
    provider = CheckHostProvider(
        check_type=check_type,
        poll_attempts=poll_attempts,
        poll_interval=poll_interval,
    )
    result = provider.check(
        host, timeout=timeout, port=port, min_ok_fraction=min_ok_fraction
    )
    return result.reachable, result.nodes_ok, result.nodes_total


def _interpret(results: dict) -> tuple[int, int]:
    """Count OK vs total responding nodes in a check-host.net result payload.

    Returns ``(nodes_ok, nodes_total)`` where ``nodes_total`` counts nodes that
    have reported a non-pending (non-``None``) value and ``nodes_ok`` how many
    of those look successful. A still-fully-pending or non-dict payload yields
    ``(0, 0)``, which the caller treats as "keep polling / unknown".
    """
    if not isinstance(results, dict):
        return 0, 0
    ok = 0
    total = 0
    for value in results.values():
        if value is None:
            # Still pending for this node.
            continue
        total += 1
        # For tcp: value like [{"time": 0.12, "address": "1.2.3.4"}]
        # For ping: value like [[["OK", 0.1], ...]]
        if _node_ok(value):
            ok += 1
    return ok, total


def _node_ok(value) -> bool:
    try:
        if isinstance(value, list) and value:
            first = value[0]
            if isinstance(first, dict):
                return "error" not in first and ("time" in first or "address" in first)
            if isinstance(first, list) and first:
                inner = first[0]
                if isinstance(inner, list) and inner:
                    return str(inner[0]).upper() == "OK"
    except (IndexError, TypeError, ValueError):
        return False
    return False


def _interpret_atlas(results) -> tuple[int, int]:
    """Count OK vs total probes in a RIPE Atlas ping result payload.

    Each element is one probe's ping result; a probe counts as OK when it
    received at least one reply (``rcvd > 0`` or a positive ``avg`` RTT). A
    non-list or empty payload yields ``(0, 0)`` so the caller keeps polling.
    """
    if not isinstance(results, list) or not results:
        return 0, 0
    ok = 0
    total = 0
    for entry in results:
        if not isinstance(entry, dict):
            continue
        total += 1
        rcvd = entry.get("rcvd")
        avg = entry.get("avg")
        if (isinstance(rcvd, int) and rcvd > 0) or (
            isinstance(avg, (int, float)) and avg > 0
        ):
            ok += 1
    return ok, total
