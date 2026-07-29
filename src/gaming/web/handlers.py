"""Request routing, session middleware, and JSON/handler logic for the dashboard.

This module is transport-agnostic: it works on plain :class:`Request` /
:class:`Response` dataclasses, so the whole dashboard can be driven in tests
without opening a socket. :mod:`gaming.web.server` is the thin adapter that maps
``http.server`` requests onto :class:`WebApp.handle`.

Every route delegates to existing modules — ``pipeline``/``providers``/
``ranges``/``scanner``/``storage``/``reporting`` — so there is no business logic
duplicated here, only HTTP glue, auth enforcement, and JSON shaping.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from http.cookies import SimpleCookie
from typing import Any
from urllib.parse import parse_qs, urlparse

from ..logging_setup import get_logger
from .auth import AuthError, CredentialStore, RateLimiter
from .jobs import JobManager

log = get_logger("gaming.web.handlers")

_SESSION_COOKIE = "gaming_session"


@dataclass(slots=True)
class Request:
    method: str
    path: str
    query: dict[str, list[str]] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""
    client_ip: str = "0.0.0.0"

    def query_one(self, key: str, default: str = "") -> str:
        vals = self.query.get(key)
        return vals[0] if vals else default

    def json(self) -> dict[str, Any]:
        if not self.body:
            return {}
        try:
            data = json.loads(self.body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def cookie(self, name: str) -> str:
        raw = self.headers.get("cookie") or self.headers.get("Cookie")
        if not raw:
            return ""
        jar = SimpleCookie()
        try:
            jar.load(raw)
        except Exception:  # noqa: BLE001 - malformed cookies are just "absent"
            return ""
        morsel = jar.get(name)
        return morsel.value if morsel else ""

    def bearer(self) -> str:
        auth = self.headers.get("authorization") or self.headers.get("Authorization")
        if auth and auth.lower().startswith("bearer "):
            return auth[7:].strip()
        return ""


@dataclass(slots=True)
class Response:
    status: int = 200
    body: bytes = b""
    headers: dict[str, str] = field(default_factory=dict)

    @classmethod
    def json(cls, data: Any, *, status: int = 200) -> Response:
        body = json.dumps(data).encode("utf-8")
        return cls(status=status, body=body, headers={"Content-Type": "application/json"})

    @classmethod
    def text(
        cls, text: str, *, status: int = 200, content_type: str = "text/plain"
    ) -> Response:
        return cls(
            status=status,
            body=text.encode("utf-8"),
            headers={"Content-Type": f"{content_type}; charset=utf-8"},
        )

    @classmethod
    def download(cls, text: str, *, filename: str, content_type: str) -> Response:
        return cls(
            status=200,
            body=text.encode("utf-8"),
            headers={
                "Content-Type": content_type,
                "Content-Disposition": f'attachment; filename="{filename}"',
            },
        )


Handler = Callable[["WebApp", Request], Response]

# Routes that do not require an authenticated session.
_PUBLIC_PATHS = {"/login", "/api/login", "/health"}
_STATIC_PREFIX = "/static/"


class WebApp:
    """The dashboard application: routing + auth + delegation to core modules."""

    def __init__(
        self,
        *,
        credentials: CredentialStore | None = None,
        jobs: JobManager | None = None,
        rate_limiter: RateLimiter | None = None,
        store: Any = None,
    ) -> None:
        self.credentials = credentials or CredentialStore()
        self.jobs = jobs or JobManager()
        self.rate_limiter = rate_limiter or RateLimiter()
        # Lazily import to avoid pulling sqlite/interactive at module import time.
        if store is None:
            from ..interactive.storage import HistoryStore

            store = HistoryStore()
        self.store = store
        self._routes: dict[tuple[str, str], Handler] = {}
        self._register_routes()

    # ---- routing ---------------------------------------------------------
    def route(self, method: str, path: str, handler: Handler) -> None:
        self._routes[(method.upper(), path)] = handler

    def handle(self, request: Request) -> Response:
        """Top-level entry: enforce auth, dispatch, and never leak a traceback."""
        parsed = urlparse(request.path)
        clean_path = parsed.path
        if not request.query:
            request.query = parse_qs(parsed.query)

        try:
            if not self._is_authorized(request, clean_path):
                if clean_path.startswith("/api/"):
                    return Response.json({"error": "unauthorized"}, status=401)
                return _redirect("/login")

            handler = self._routes.get((request.method.upper(), clean_path))
            if handler is None:
                # Static assets and the SPA shell are resolved by the server
                # adapter; here, an unknown API path is a 404.
                return Response.json({"error": "not found"}, status=404)
            return handler(self, request)
        except Exception as exc:  # noqa: BLE001 - a handler bug must not crash server
            log.warning("handler error for %s %s: %s", request.method, clean_path, exc)
            return Response.json({"error": "internal error"}, status=500)

    def _is_authorized(self, request: Request, path: str) -> bool:
        if path in _PUBLIC_PATHS or path == "/" or path.startswith(_STATIC_PREFIX):
            return True
        # Bearer token (automation) OR a valid session cookie.
        if self.credentials.verify_bearer(request.bearer()):
            return True
        return self.credentials.validate_session(request.cookie(_SESSION_COOKIE))

    def _register_routes(self) -> None:
        self.route("POST", "/api/login", _login)
        self.route("POST", "/api/logout", _logout)
        self.route("GET", "/api/me", _me)
        self.route("POST", "/api/change-credentials", _change_credentials)
        self.route("POST", "/api/search", _start_search)
        self.route("GET", "/api/jobs", _get_job)  # ?id=...
        self.route("POST", "/api/scan", _run_scan)
        self.route("GET", "/api/history", _history)
        self.route("GET", "/api/scan-results", _scan_results)  # ?id=...
        self.route("GET", "/api/settings", _get_settings)
        self.route("POST", "/api/settings", _save_settings)
        self.route("GET", "/api/summary", _summary)
        self.route("GET", "/api/export", _export)  # ?kind=csv|json|whitelist|ip-list
        self.route("POST", "/api/proximity-ping", _start_proximity_ping)
        self.route("POST", "/api/lookup-ip", _lookup_ip)
        self.route("POST", "/api/provider-lookup", _provider_lookup)
        self.route("POST", "/api/discover-exhaustive", _start_exhaustive)
        self.route("GET", "/api/watch", _watch_status)
        self.route("POST", "/api/watch", _watch_control)


def _redirect(location: str) -> Response:
    return Response(status=302, headers={"Location": location})


# --------------------------------------------------------------------------
# Auth handlers
# --------------------------------------------------------------------------
def _login(app: WebApp, req: Request) -> Response:
    if app.rate_limiter.is_blocked(req.client_ip):
        return Response.json(
            {"error": "too many attempts; try again later"}, status=429
        )
    data = req.json()
    username = str(data.get("username", ""))
    password = str(data.get("password", ""))
    if app.credentials.verify_password(username, password):
        app.rate_limiter.reset(req.client_ip)
        token = app.credentials.issue_session()
        resp = Response.json({"ok": True})
        cookie = (
            f"{_SESSION_COOKIE}={token}; HttpOnly; SameSite=Strict; Path=/"
        )
        resp.headers["Set-Cookie"] = cookie
        return resp
    app.rate_limiter.record_failure(req.client_ip)
    return Response.json({"error": "invalid credentials"}, status=401)


def _logout(app: WebApp, req: Request) -> Response:
    resp = Response.json({"ok": True})
    resp.headers["Set-Cookie"] = (
        f"{_SESSION_COOKIE}=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0"
    )
    return resp


def _me(app: WebApp, req: Request) -> Response:
    creds, _ = app.credentials.ensure_credentials()
    return Response.json({"username": creds.username})


def _change_credentials(app: WebApp, req: Request) -> Response:
    data = req.json()
    try:
        app.credentials.change_credentials(
            str(data.get("current_password", "")),
            str(data.get("new_username", "")),
            str(data.get("new_password", "")),
        )
    except AuthError as exc:
        return Response.json({"error": str(exc)}, status=400)
    # Rotating the secret invalidated this session; force a fresh login.
    resp = Response.json({"ok": True})
    resp.headers["Set-Cookie"] = (
        f"{_SESSION_COOKIE}=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0"
    )
    return resp


# --------------------------------------------------------------------------
# Search (background discovery job)
# --------------------------------------------------------------------------
def _start_search(app: WebApp, req: Request) -> Response:
    data = req.json()
    query = str(data.get("query", ""))
    provider = str(data.get("provider", ""))
    country = str(data.get("country", ""))
    asn = str(data.get("asn", ""))

    def _work(job) -> dict[str, Any]:
        records = _discover_and_filter(query, provider, country, asn)
        job.progress = 1.0
        return {"count": len(records), "records": [_record_dict(r) for r in records]}

    job = app.jobs.start("search", _work, meta={"query": query})
    return Response.json({"job_id": job.id})


def _discover_and_filter(query: str, provider: str, country: str, asn: str) -> list:
    """Run discovery + seed providers, filtered by the web search terms.

    Reuses ``pipeline.discover``/``process`` and ``providers.load_seed_records``
    (the same sources the terminal menu uses) plus the shared ``record_matches``
    predicate, so the web search behaves identically to the CLI/menu.
    """
    from .. import pipeline
    from ..config import load_config
    from ..interactive import providers as providers_mod
    from ..interactive.filters_shared import record_matches
    from ..models import Filters

    records: list = list(providers_mod.load_seed_records())
    try:
        config = load_config()
        filters = Filters(
            countries=[country] if country else [],
            asns=[asn] if asn else [],
            providers=[provider] if provider else [],
        )
        raw = pipeline.discover(config, filters, verbose_errors=False)
        records.extend(pipeline.process(raw, filters))
    except Exception as exc:  # noqa: BLE001 - discovery is best-effort
        log.warning("web search discovery failed: %s: %s", type(exc).__name__, exc)

    seen: set[str] = set()
    out: list = []
    for rec in records:
        prefix = getattr(rec, "prefix", None)
        if not prefix or prefix in seen:
            continue
        if record_matches(rec, query=query, provider=provider, country=country, asn=asn):
            seen.add(prefix)
            out.append(rec)
    return out


def _get_job(app: WebApp, req: Request) -> Response:
    job = app.jobs.get(req.query_one("id"))
    if job is None:
        return Response.json({"error": "no such job"}, status=404)
    return Response.json(job.to_dict())


# --------------------------------------------------------------------------
# Scan (bidirectional) — synchronous-per-request but hosts are bounded
# --------------------------------------------------------------------------
_SCAN_MODE_COMBINED = "combined"
_SCAN_MODE_SEQUENTIAL = "sequential"


def _run_scan(app: WebApp, req: Request) -> Response:
    data = req.json()
    cidrs = data.get("cidrs")
    category = str(data.get("category", ""))
    mode = str(data.get("mode", _SCAN_MODE_COMBINED)).strip().lower()
    if mode not in (_SCAN_MODE_COMBINED, _SCAN_MODE_SEQUENTIAL):
        mode = _SCAN_MODE_COMBINED

    if cidrs is not None:
        try:
            cidrs = _validate_cidrs(cidrs)
        except ValueError as exc:
            return Response.json({"error": str(exc)}, status=400)

    if mode == _SCAN_MODE_SEQUENTIAL:
        def _work(job) -> dict[str, Any]:
            return _scan_sequential_and_store(app, cidrs, category, job)

        job = app.jobs.start(
            "scan-sequential", _work, meta={"category": category, "mode": mode}
        )
    else:
        def _work(job) -> dict[str, Any]:
            return _scan_and_store(app, cidrs, category)

        job = app.jobs.start("scan", _work, meta={"category": category, "mode": mode})
    return Response.json({"job_id": job.id})


def _validate_cidrs(cidrs: Any) -> list[str]:
    """Normalize caller-supplied CIDRs, rejecting anything malformed.

    Now that the browser can hand over a typed-in range, bad input has to fail
    here with a clear message rather than several layers down inside the host
    expander, where the error would surface as an opaque failed job. A bare
    address is accepted and treated as a single host, matching ``gaming check``.
    """
    import ipaddress

    if not isinstance(cidrs, list):
        raise ValueError("cidrs must be a list of CIDR strings")
    out: list[str] = []
    for raw in cidrs:
        text = str(raw).strip()
        if not text:
            continue
        try:
            network = ipaddress.ip_network(text, strict=False)
        except ValueError:
            raise ValueError(f"{text!r} is not a valid CIDR or IP address") from None
        out.append(str(network))
    if not out:
        raise ValueError("no CIDRs given")
    return out


def _resolve_cidrs(cidrs: Any, category: str) -> tuple[list[str], list[str]]:
    """Resolve the request's ``cidrs``/``category`` into a scan list.

    Shared by both scan modes. Returns ``(cidr_list, unverified_iran)`` — the
    second element lists CIDRs excluded from an Iran-origin category scan
    because they aren't verified as IR-located (foreign PoPs/anycast edges
    saved under an Iranian provider), surfaced separately rather than silently
    scanned as "Iranian".
    """
    from ..interactive import ranges as ranges_mod
    from ..processing.filters import partition_by_country

    cidr_list: list[str] = []
    unverified_iran: list[str] = []
    if isinstance(cidrs, list):
        cidr_list = [str(c) for c in cidrs]
    elif category in ranges_mod.CATEGORIES:
        entries = ranges_mod.category_entries(category)
        if category.startswith("iran"):
            part = partition_by_country(entries, "IR")
            cidr_list = [e.cidr for e in part.matched]
            unverified_iran = [e.cidr for e in part.unverified]
        else:
            cidr_list = [e.cidr for e in entries]
    return cidr_list, unverified_iran


def _scan_and_store(app: WebApp, cidrs: Any, category: str) -> dict[str, Any]:
    from ..interactive import ranges as ranges_mod
    from ..interactive import scanner
    from ..interactive.settings import load_settings

    settings = load_settings()
    cidr_list, unverified_iran = _resolve_cidrs(cidrs, category)

    hosts = ranges_mod.expand_hosts(
        cidr_list,
        sample_per_range=settings.sample_per_range,
        max_hosts=settings.max_hosts,
    )
    report = scanner.run_scan(category or "web", settings, hosts=hosts)
    scan_id = scanner.persist(report, app.store)
    rows = [_combined_row(report, c) for c in report.combined]
    return {
        "mode": _SCAN_MODE_COMBINED,
        "scan_id": scan_id,
        "counts": report.combined_counts,
        "results": rows,
        "location_unverified": unverified_iran,
    }


def _scan_sequential_and_store(
    app: WebApp, cidrs: Any, category: str, job: Any
) -> dict[str, Any]:
    """Scan each matched CIDR as its own sequential step (one job overall).

    Each CIDR's scan runs to completion before the next starts. ``job.result``
    is updated after every CIDR so a client polling ``GET /api/jobs`` sees
    per-CIDR progress and results as they arrive, not just at the very end. A
    failure scanning one CIDR is recorded against that CIDR only (fail-soft) —
    it never aborts the remaining queued CIDRs. All hosts across every CIDR are
    still persisted as a single scan at the end, so the existing
    scan-id-based export/download endpoints work unchanged in this mode too.
    """
    from ..interactive import ranges as ranges_mod
    from ..interactive import scanner
    from ..interactive.settings import load_settings

    settings = load_settings()
    cidr_list, unverified_iran = _resolve_cidrs(cidrs, category)

    per_cidr: list[dict[str, Any]] = []
    all_results: list = []
    all_combined: list = []
    total = len(cidr_list)

    def _snapshot(
        scan_id: int | None = None, counts: dict | None = None
    ) -> dict[str, Any]:
        return {
            "mode": _SCAN_MODE_SEQUENTIAL,
            "per_cidr": list(per_cidr),
            "cidrs_total": total,
            "cidrs_done": len(per_cidr),
            "location_unverified": unverified_iran,
            "scan_id": scan_id,
            "counts": counts or {},
        }

    for cidr in cidr_list:
        if job.cancelled():
            # Shutdown asked us to stop: return what we have rather than being
            # killed mid-scan. Already-scanned CIDRs are still persisted below.
            log.info("sequential scan cancelled after %d/%d CIDRs", len(per_cidr), total)
            break
        try:
            hosts = ranges_mod.expand_hosts(
                [cidr],
                sample_per_range=settings.sample_per_range,
                max_hosts=settings.max_hosts,
            )
            report = scanner.run_scan(category or "web", settings, hosts=hosts)
            rows = [_combined_row(report, c) for c in report.combined]
            per_cidr.append(
                {
                    "cidr": cidr,
                    "counts": report.combined_counts,
                    "results": rows,
                    "error": None,
                }
            )
            all_results.extend(report.results)
            all_combined.extend(report.combined)
        except Exception as exc:  # noqa: BLE001 - one bad CIDR must not stop the queue
            log.warning(
                "sequential scan of %s failed: %s: %s", cidr, type(exc).__name__, exc
            )
            per_cidr.append(
                {"cidr": cidr, "counts": {}, "results": [], "error": str(exc)}
            )
        job.progress = len(per_cidr) / total if total else 1.0
        job.result = _snapshot()

    scan_id = None
    counts: dict[str, int] = {}
    if all_results:
        aggregate = scanner.ScanReport(
            scope=category or "web", results=all_results, combined=all_combined
        )
        scan_id = scanner.persist(aggregate, app.store)
        counts = aggregate.combined_counts
    return _snapshot(scan_id=scan_id, counts=counts)


def _combined_row(report, c) -> dict[str, Any]:
    verdict = report.combined_verdict(c)
    p = c.probe
    return {
        "host": p.host,
        "health": next((v for pr, v in report.results if pr.host == p.host), ""),
        "avg_ms": p.avg_ms,
        "loss_pct": p.loss_pct,
        "iran_reachable": c.iran_reachable,
        "abroad_reachable": c.abroad_reachable,
        "abroad_nodes_ok": c.abroad_nodes_ok,
        "abroad_nodes_total": c.abroad_nodes_total,
        "abroad_status": getattr(c, "abroad_status", None),
        "combined": verdict,
        "open_ports": list(c.open_ports),
    }


# --------------------------------------------------------------------------
# History
# --------------------------------------------------------------------------
def _history(app: WebApp, req: Request) -> Response:
    scans = app.store.list_scans(limit=int(req.query_one("limit", "50") or 50))
    return Response.json(
        {
            "scans": [
                {
                    "id": s.id,
                    "started_at": s.started_at,
                    "scope": s.scope,
                    "total": s.total,
                    "good": s.good,
                    "medium": s.medium,
                    "bad": s.bad,
                }
                for s in scans
            ]
        }
    )


def _scan_results(app: WebApp, req: Request) -> Response:
    try:
        scan_id = int(req.query_one("id"))
    except ValueError:
        return Response.json({"error": "bad scan id"}, status=400)
    rows = app.store.get_results(scan_id)
    return Response.json(
        {
            "results": [
                {
                    "host": r.host,
                    "health": r.verdict,
                    "avg_ms": r.avg_ms,
                    "loss_pct": r.loss_pct,
                    "abroad_reachable": r.abroad_reachable,
                    "abroad_nodes_ok": r.abroad_nodes_ok,
                    "abroad_nodes_total": r.abroad_nodes_total,
                    "abroad_status": r.abroad_status,
                    "combined": r.combined_verdict,
                    "open_ports": r.open_ports,
                }
                for r in rows
            ]
        }
    )


# --------------------------------------------------------------------------
# Settings (shared file with CLI/menu)
# --------------------------------------------------------------------------
def _get_settings(app: WebApp, req: Request) -> Response:
    from ..interactive.settings import load_settings

    return Response.json({"settings": load_settings().to_dict()})


def _save_settings(app: WebApp, req: Request) -> Response:
    from ..interactive.settings import Settings, load_settings, save_settings

    data = req.json()
    current = load_settings().to_dict()
    known = set(current)
    for key, value in data.items():
        if key in known:
            current[key] = value
    try:
        settings = Settings(**current).clamped()
    except (TypeError, ValueError) as exc:
        return Response.json({"error": f"invalid settings: {exc}"}, status=400)
    save_settings(settings)
    return Response.json({"ok": True, "settings": settings.to_dict()})


# --------------------------------------------------------------------------
# Provider/country connectivity summary (Part C.5 widget)
# --------------------------------------------------------------------------
def _summary(app: WebApp, req: Request) -> Response:
    from .summary import provider_connectivity

    return Response.json({"providers": provider_connectivity(app.store)})


# --------------------------------------------------------------------------
# Export (CSV / JSON / whitelist bare IPs)
# --------------------------------------------------------------------------
def _export(app: WebApp, req: Request) -> Response:
    kind = req.query_one("kind", "json").lower()
    try:
        scan_id = int(req.query_one("scan"))
    except ValueError:
        return Response.json({"error": "bad scan id"}, status=400)
    rows = app.store.get_results(scan_id)

    if kind == "whitelist":
        from ..interactive.filters_shared import format_bare_ips

        hosts = [r.host for r in rows if r.combined_verdict == "INTERNATIONAL"]
        return Response.download(
            format_bare_ips(hosts),
            filename=f"whitelist-scan-{scan_id}.txt",
            content_type="text/plain; charset=utf-8",
        )

    records = [_row_to_record(r) for r in rows]
    if kind == "csv":
        from ..reporting import to_csv

        return Response.download(
            to_csv(records),
            filename=f"scan-{scan_id}.csv",
            content_type="text/csv; charset=utf-8",
        )
    if kind in ("ip-list", "ip_list", "iplist"):
        from ..reporting import to_ip_list

        return Response.download(
            to_ip_list(records),
            filename=f"scan-{scan_id}-ips.txt",
            content_type="text/plain; charset=utf-8",
        )
    from ..reporting import to_json

    return Response.download(
        to_json(records),
        filename=f"scan-{scan_id}.json",
        content_type="application/json; charset=utf-8",
    )


def _row_to_record(row):
    """Adapt a stored ResultRow into an IPRecord for the shared exporters."""
    from ..models import IPRecord

    rec = IPRecord(prefix=row.host, source="scan")
    rec.open_ports = list(row.open_ports)
    note_bits = [f"health={row.verdict}"]
    if row.combined_verdict:
        note_bits.append(f"combined={row.combined_verdict}")
    rec.notes = "; ".join(note_bits)
    return rec


# --------------------------------------------------------------------------
# Proximity ping — explicitly separate, opt-in "test path to..." action
# --------------------------------------------------------------------------
def _start_proximity_ping(app: WebApp, req: Request) -> Response:
    """Start an approximate proximity-ping job for a discovered IP's row.

    Conceptually distinct from the bidirectional Iran/abroad reachability
    columns: this asks the nearest available RIPE Atlas probe to the source
    IP's network to ping an arbitrary destination the user chooses. It is
    never the source IP's own ping — see
    :func:`gaming.reachability.global_check.measure_from_near`.
    """
    data = req.json()
    source_ip = str(data.get("source_ip", "")).strip()
    destination_ip = str(data.get("destination_ip", "")).strip()
    if not source_ip or not destination_ip:
        return Response.json(
            {"error": "source_ip and destination_ip are required"}, status=400
        )

    def _work(job) -> dict[str, Any]:
        from ..reachability.global_check import measure_from_near

        result = measure_from_near(source_ip, destination_ip)
        return _proximity_result_dict(result)

    job = app.jobs.start(
        "proximity-ping",
        _work,
        meta={"source_ip": source_ip, "destination_ip": destination_ip},
    )
    return Response.json({"job_id": job.id})


def _proximity_result_dict(result) -> dict[str, Any]:
    return {
        "status": result.status,
        "probe_id": result.probe_id,
        "probe_asn": result.probe_asn,
        "avg_ms": result.avg_ms,
        "reachable": result.reachable,
        "note": result.note,
    }


def _record_dict(rec) -> dict[str, Any]:
    return {
        "prefix": getattr(rec, "prefix", ""),
        "asn": getattr(rec, "asn", None),
        "organization": getattr(rec, "organization", None),
        "country": getattr(rec, "country", None),
        "provider": getattr(rec, "provider", None),
    }


# --------------------------------------------------------------------------
# Reverse IP lookup — "which of my stored ranges contains this address?"
# --------------------------------------------------------------------------
def _lookup_ip(app: WebApp, req: Request) -> Response:
    """Look one IP up against every stored CIDR.

    Mirrors ``gaming check-membership``: same module, same match ordering, same
    optional live-RDAP fallback, so the dashboard and the CLI can never diverge
    on what "this IP belongs to X" means.
    """
    data = req.json()
    ip = str(data.get("ip", "")).strip()
    if not ip:
        return Response.json({"error": "ip is required"}, status=400)

    from ..interactive import membership

    try:
        result = membership.lookup_ip(
            ip, include_bundled=bool(data.get("include_bundled", True))
        )
    except ValueError:
        return Response.json({"error": f"{ip!r} is not a valid IP address"}, status=400)

    if not result.found and data.get("live"):
        result.live = membership.live_lookup(ip)

    return Response.json(result.as_dict())


# --------------------------------------------------------------------------
# Provider lookup by name — the same shared function the CLI and menu call
# --------------------------------------------------------------------------
def _provider_lookup(app: WebApp, req: Request) -> Response:
    """Resolve one organization name against the live registries.

    Runs as a tracked job rather than inline: a RIPE entity search follows up
    to a dozen handles, so the whole lookup can outlast an HTTP timeout. The
    browser polls ``/api/jobs`` exactly as it does for search and scan.
    """
    data = req.json()
    name = str(data.get("name", "")).strip()
    if not name:
        return Response.json({"error": "name is required"}, status=400)

    def _work(job) -> dict[str, Any]:
        from ..discovery import provider_lookup

        result = provider_lookup.lookup_provider_by_name(name)
        job.progress = 1.0
        return result.as_dict()

    job = app.jobs.start("provider-lookup", _work, meta={"name": name})
    return Response.json({"job_id": job.id})


# --------------------------------------------------------------------------
# Exhaustive country discovery — long-running, so it runs as a tracked job
# --------------------------------------------------------------------------
def _start_exhaustive(app: WebApp, req: Request) -> Response:
    """Kick off a full-country sweep in the background job manager.

    A sweep resolves thousands of prefixes and can run for minutes, far past
    any sane HTTP timeout, so the response is a job id the UI polls via
    ``/api/jobs`` — the same pattern the search endpoint uses.
    """
    data = req.json()
    country = str(data.get("country", "IR")).strip().upper() or "IR"
    include_ipv6 = bool(data.get("include_ipv6", True))
    resume = bool(data.get("resume", True))
    save = bool(data.get("save", True))

    def _work(job) -> dict[str, Any]:
        from ..discovery.base import DiscoveryContext
        from ..discovery.exhaustive import ExhaustiveSweep, summarize
        from ..models import Filters

        def _progress(p) -> None:
            job.progress = (p.done / p.total) if p.total else 0.0

        sweep = ExhaustiveSweep(
            country=country,
            context=DiscoveryContext(filters=Filters(countries=[country])),
            include_ipv6=include_ipv6,
            resume=resume,
            progress_callback=_progress,
        )
        records = sweep.run()
        saved: dict[str, int] = {}
        if save:
            from ..interactive import ranges as ranges_mod

            saved = ranges_mod.persist_exhaustive_records(
                records, home_country=country
            )
        job.progress = 1.0
        return {
            "country": country,
            "count": len(records),
            "summary": summarize(records),
            "saved": saved,
            "records": [_record_dict(r) for r in records],
        }

    job = app.jobs.start("discover-exhaustive", _work, meta={"country": country})
    return Response.json({"job_id": job.id})


# --------------------------------------------------------------------------
# 24/7 watch controls — one in-process loop, shared with `gaming watch`
# --------------------------------------------------------------------------
#: The dashboard-owned watch loop, if one is running. Module-level because the
#: loop must outlive any single request, and only one may run per process.
_WATCH_LOOP: Any = None


def _watch_status(app: WebApp, req: Request) -> Response:
    """Report the in-process loop, plus any detached ``gaming watch --daemon``."""
    from ..interactive import paths
    from . import daemon as daemon_mod

    loop = _WATCH_LOOP
    daemon_status = daemon_mod.status(paths.watch_pid_path())
    return Response.json(
        {
            "running": bool(loop is not None and loop.running),
            "state": loop.state.as_dict() if loop is not None else None,
            "daemon": {
                "running": daemon_status.running,
                "pid": daemon_status.pid,
                "since": daemon_status.since,
            },
        }
    )


def _watch_control(app: WebApp, req: Request) -> Response:
    """Start or stop the dashboard's watch loop.

    Uses the exact same :class:`~gaming.interactive.watch.WatchLoop` as the CLI
    rather than a parallel implementation, so a watch started from the browser
    behaves identically to one started from a terminal.
    """
    global _WATCH_LOOP

    data = req.json()
    action = str(data.get("action", "")).strip().lower()

    if action == "stop":
        loop = _WATCH_LOOP
        if loop is None or not loop.running:
            return Response.json({"running": False, "stopped": False})
        loop.stop()
        _WATCH_LOOP = None
        return Response.json({"running": False, "stopped": True})

    if action != "start":
        return Response.json({"error": "action must be 'start' or 'stop'"}, status=400)

    if _WATCH_LOOP is not None and _WATCH_LOOP.running:
        return Response.json(
            {"error": "a watch is already running", "state": _WATCH_LOOP.state.as_dict()},
            status=409,
        )

    from ..interactive.watch import WatchLoop, parse_interval

    loop = WatchLoop(
        country=str(data.get("country", "IR")).strip().upper() or "IR",
        interval_seconds=parse_interval(data.get("interval", "1h")),
        scope=str(data.get("scope", "iran")).strip().lower() or "iran",
        include_ipv6=bool(data.get("include_ipv6", True)),
        store=app.store,
    )
    loop.start()
    _WATCH_LOOP = loop
    return Response.json({"running": True, "state": loop.state.as_dict()})
